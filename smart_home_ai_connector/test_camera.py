import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiohttp import WSMsgType
from connector_camera import validate_session, CameraSession, camera_loop

SID='00000000-0000-4000-8000-000000000001'
def value(**patch):
    return {'id':SID,'entityId':'camera.doorbell','expiresAt':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),'offer':'v=0 browser offer example','candidates':[{'candidate':'candidate:local','sdpMid':'0','sdpMLineIndex':0}],**patch}

class FakeWS:
    def __init__(self):
        self.sent=[];self.closed=False;self.queue=asyncio.Queue()
        self.receive_json=AsyncMock(side_effect=[{'type':'auth_required'},{'type':'auth_ok'},{'id':1,'success':True,'result':{'configuration':{'iceServers':[]},'dataChannel':'data'}}])
    async def __aenter__(self): return self
    async def __aexit__(self,*args): self.closed=True
    async def send_json(self,data): self.sent.append(data)
    def __aiter__(self): return self
    async def __anext__(self): return SimpleNamespace(type=WSMsgType.TEXT,data=json.dumps(await self.queue.get()))

class CameraTests(unittest.IsolatedAsyncioTestCase):
    def test_only_exported_camera_and_fresh_bounded_signals(self):
        self.assertEqual(validate_session(value(),{'camera.doorbell'})['id'],SID)
        for patch in ({'id':'-'*36},{'entityId':'camera.other'},{'entityId':'light.lamp'},{'expiresAt':'2020-01-01T00:00:00Z'},{'offer':'invalid'},{'candidates':[{'candidate':'x','url':'secret'}]},{'candidates':[{'candidate':'x'}]*65}):
            with self.assertRaises(ValueError): validate_session(value(**patch),{'camera.doorbell'})

    async def test_native_offer_and_trickle_candidates_close_with_websocket(self):
        ws=FakeWS();ha=SimpleNamespace(session=SimpleNamespace(ws_connect=lambda *a,**k:ws),ws_url='ws://local/api/websocket',token='internal')
        relay=SimpleNamespace(camera_event=AsyncMock());session=CameraSession(value(),ha,relay,'home')
        for _ in range(10): await asyncio.sleep(0)
        self.assertEqual(ws.sent[0],{'type':'auth','access_token':'internal'})
        self.assertEqual(ws.sent[1]['type'],'camera/webrtc/get_client_config')
        self.assertEqual(ws.sent[2]['type'],'camera/webrtc/offer')
        self.assertEqual(relay.camera_event.call_args.args[-1],{'type':'config','configuration':{'iceServers':[]},'dataChannel':'data'})
        await ws.queue.put({'type':'event','id':2,'event':{'type':'session','session_id':'native-id'}})
        await ws.queue.put({'type':'event','id':2,'event':{'type':'answer','answer':'v=0 answer'}})
        for _ in range(10): await asyncio.sleep(0)
        self.assertEqual(ws.sent[3]['type'],'camera/webrtc/candidate');self.assertEqual(ws.sent[3]['session_id'],'native-id')
        await session.update(value());self.assertEqual(len(ws.sent),4)
        session.task.cancel();await asyncio.gather(session.task,return_exceptions=True);self.assertTrue(ws.closed)

    async def test_upstream_errors_never_expose_credentials(self):
        ws=FakeWS();ha=SimpleNamespace(session=SimpleNamespace(ws_connect=lambda *a,**k:ws),ws_url='ws://local',token='internal');relay=SimpleNamespace(camera_event=AsyncMock())
        session=CameraSession(value(),ha,relay,'home')
        await session.emit({'type':'error','message':'credential=private'})
        self.assertNotIn('private',str(relay.camera_event.call_args))
        session.task.cancel();await asyncio.gather(session.task,return_exceptions=True)

    async def test_permission_off_never_requests_sessions_or_opens_home_assistant(self):
        relay=SimpleNamespace(configured=True,camera_sessions=AsyncMock());ha=SimpleNamespace(exported_entities={'camera.doorbell'});wake=asyncio.Event()
        task=asyncio.create_task(camera_loop(SimpleNamespace(home_name='home'),ha,relay,False,wake));await asyncio.sleep(0);task.cancel();await asyncio.gather(task,return_exceptions=True);relay.camera_sessions.assert_not_called()
