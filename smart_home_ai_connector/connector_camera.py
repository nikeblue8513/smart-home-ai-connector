"""On-demand WebRTC signaling. Media never traverses or is recorded by this relay."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from uuid import UUID
from datetime import datetime, timezone
from aiohttp import ClientError, WSMsgType
from connector_live import wait_for_wake

LOGGER = logging.getLogger('smart-home-ai-connector')

def validate_session(value: dict, exported: set[str]) -> dict:
    if not isinstance(value, dict) or not re.fullmatch(r'[a-f0-9-]{36}', str(value.get('id', ''))):
        raise ValueError('Invalid camera session')
    if str(UUID(value['id'])) != value['id']:
        raise ValueError('Invalid camera session')
    entity = value.get('entityId', '')
    if not isinstance(entity, str) or not re.fullmatch(r'camera\.[a-z0-9_]+', entity) or entity not in exported:
        raise ValueError('Camera is not exported')
    expires = datetime.fromisoformat(str(value.get('expiresAt', '')).replace('Z', '+00:00'))
    if not expires.tzinfo or not 0 < (expires-datetime.now(timezone.utc)).total_seconds() <= 90:
        raise ValueError('Camera session expired')
    offer = value.get('offer')
    if offer is not None and (not isinstance(offer, str) or not offer.startswith('v=0') or not 20 <= len(offer) <= 65536):
        raise ValueError('Invalid offer')
    candidates = value.get('candidates', [])
    if not isinstance(candidates, list) or len(candidates) > 64:
        raise ValueError('Invalid candidates')
    for c in candidates:
        if not isinstance(c, dict) or set(c)-{'candidate', 'sdpMid', 'sdpMLineIndex', 'usernameFragment'} or not isinstance(c.get('candidate'), str) or len(c['candidate']) > 4096:
            raise ValueError('Invalid candidate')
    return value

class CameraSession:
    def __init__(self, value, ha, relay, home_name):
        self.id = value['id']
        self.entity = value['entityId']
        self.value = value
        self.ha, self.relay, self.home_name = ha, relay, home_name
        self.ws = None
        self.offer_sent = False
        self.ha_session = None
        self.sent_candidates = 0
        self.next_id = 3
        self.lock = asyncio.Lock()
        self.task = asyncio.create_task(self.run())

    async def emit(self, event):
        if isinstance(event, dict) and event.get('type') in {'config','session','answer','candidate','error'}:
            if len(json.dumps(event)) > 80000:
                raise ValueError('Camera event too large')
            # Never relay raw upstream errors, which may contain credentialized URLs.
            if event['type'] == 'error':
                event = {'type':'error', 'message':'Home Assistant could not start this camera. Check its live view locally, then retry.'}
            await self.relay.camera_event(self.home_name, self.id, event)

    async def update(self, value):
        self.value = value
        async with self.lock:
            if not self.ws:
                return
            if value.get('offer') and not self.offer_sent:
                await self.ws.send_json({'id':2,'type':'camera/webrtc/offer','entity_id':self.entity,'offer':value['offer']})
                self.offer_sent = True
            if self.ha_session:
                while self.sent_candidates < len(value['candidates']):
                    candidate = value['candidates'][self.sent_candidates]
                    await self.ws.send_json({'id':self.next_id,'type':'camera/webrtc/candidate','entity_id':self.entity,'session_id':self.ha_session,'candidate':candidate})
                    self.sent_candidates += 1
                    self.next_id += 1

    async def run(self):
        try:
            async with asyncio.timeout(300):
                async with self.ha.session.ws_connect(self.ha.ws_url, heartbeat=20, max_msg_size=131072) as ws:
                    async with asyncio.timeout(10):
                        if (await ws.receive_json()).get('type') != 'auth_required':
                            raise RuntimeError('Missing camera handshake')
                        await ws.send_json({'type':'auth','access_token':self.ha.token})
                        if (await ws.receive_json()).get('type') != 'auth_ok':
                            raise RuntimeError('Camera authentication failed')
                        await ws.send_json({'id':1,'type':'camera/webrtc/get_client_config','entity_id':self.entity})
                        config = await ws.receive_json()
                        if not config.get('success'):
                            raise RuntimeError('Camera does not support WebRTC')
                    settings = config.get('result', {})
                    await self.emit({'type':'config','configuration':settings.get('configuration',{}), **({'dataChannel':settings['dataChannel']} if isinstance(settings.get('dataChannel'),str) else {})})
                    self.ws = ws
                    await self.update(self.value)
                    async for message in ws:
                        if message.type != WSMsgType.TEXT:
                            continue
                        payload = json.loads(message.data)
                        if payload.get('type') == 'result' and payload.get('success') is False:
                            raise RuntimeError('Camera signaling rejected')
                        if payload.get('type') == 'event' and payload.get('id') == 2:
                            event = payload.get('event', {})
                            if event.get('type') == 'session':
                                self.ha_session = event.get('session_id')
                            await self.emit(event)
                            await self.update(self.value)
        except asyncio.CancelledError:
            raise
        except (ClientError, OSError, RuntimeError, ValueError, TypeError, TimeoutError):
            LOGGER.warning('Camera session ended; no media or signaling content logged')
            try:
                await self.emit({'type':'error'})
            except (ClientError, OSError, RuntimeError, ValueError, TimeoutError):
                pass
        finally:
            self.ws = None

async def camera_loop(state, ha, relay, allowed, wake):
    active = {}
    async def close_all():
        for session in active.values():
            session.task.cancel()
        await asyncio.gather(*(s.task for s in active.values()), return_exceptions=True)
        active.clear()
    try:
        while True:
            wake.clear()
            try:
                if allowed and relay.configured and state.home_name:
                    values = await relay.camera_sessions(state.home_name)
                    if not isinstance(values, list) or len(values)>2:
                        raise ValueError('Invalid camera session count')
                    sessions = {v['id']:validate_session(v,ha.exported_entities) for v in values}
                    for sid in list(active):
                        if sid not in sessions:
                            active[sid].task.cancel()
                            await asyncio.gather(active[sid].task, return_exceptions=True)
                            del active[sid]
                    for sid, value in sessions.items():
                        if sid not in active:
                            active[sid] = CameraSession(value,ha,relay,state.home_name)
                        elif not active[sid].task.done():
                            await active[sid].update(value)
                else:
                    await close_all()
            except asyncio.CancelledError:
                raise
            except (ClientError, OSError, RuntimeError, ValueError, TypeError, KeyError, TimeoutError):
                await close_all()  # Stop media immediately if access is revoked or relay is lost.
                LOGGER.warning('Camera signaling unavailable; active views closed')
            await wait_for_wake(wake, 2 if active else 15)
    finally:
        await close_all()
