"""Empty wake notifications only. Commands still require authenticated relay fetches."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from urllib.parse import urlsplit
from aiohttp import ClientError, WSMsgType

LOGGER = logging.getLogger("smart-home-ai-connector")

async def wait_for_wake(event: asyncio.Event, seconds: float, failures: int = 0) -> None:
    # Do not allow wake storms to bypass backoff during an outage.
    if failures:
        await asyncio.sleep(seconds)
        return
    try:
        await asyncio.wait_for(event.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


def live_endpoint(info: dict, relay_url: str) -> tuple[str, str, str]:
    origin = urlsplit(info.get("url", ""))
    relay = urlsplit(relay_url)
    if origin.scheme != "https" or origin.netloc != relay.netloc or origin.path not in ("", "/") or origin.query or origin.fragment or origin.username:
        raise ValueError("Live endpoint must match the paired relay origin")
    topic, key = info.get("topic", ""), info.get("apikey", "")
    if not isinstance(topic, str) or not re.fullmatch(r"homecore-wake-[a-f0-9]{64}", topic) or not isinstance(key, str) or not 20 <= len(key) <= 4096:
        raise ValueError("Invalid live delivery configuration")
    return f"wss://{origin.netloc}/realtime/v1/websocket", "realtime:" + topic, key


async def live_commands(session, relay, state, wake: asyncio.Event) -> None:
    failures = 0
    while True:
        if not relay.configured or not state.home_name:
            await asyncio.sleep(2)
            continue
        try:
            url, topic, key = live_endpoint(await relay.live(state.home_name), relay.relay_url)
            async with session.ws_connect(url, params={"apikey": key, "vsn": "1.0.0"}, heartbeat=20, max_msg_size=32768) as ws:
                await ws.send_json({"topic": topic, "event": "phx_join", "payload": {"config": {"broadcast": {"ack": False, "self": False}, "presence": {"enabled": False}, "private": False}}, "ref": "1", "join_ref": "1"})
                async with asyncio.timeout(10):
                    joined = await ws.receive_json()
                    if joined.get("event") != "phx_reply" or joined.get("payload", {}).get("status") != "ok":
                        raise RuntimeError("Live delivery subscription failed")
                state.delivery = "live"
                LOGGER.info("Live command delivery connected")
                failures = 0
                wake.set()  # Recover anything queued before subscription.
                async def heartbeat():
                    ref = 2
                    while True:
                        await asyncio.sleep(20)
                        await ws.send_json({"topic": "phoenix", "event": "heartbeat", "payload": {}, "ref": str(ref)})
                        ref += 1
                pulse = asyncio.create_task(heartbeat())
                try:
                    while True:
                        message = await asyncio.wait_for(ws.receive(), timeout=55)
                        if message.type != WSMsgType.TEXT:
                            raise RuntimeError("Live delivery disconnected")
                        data = json.loads(message.data)
                        if data.get("event") in ("phx_error", "phx_close"):
                            raise RuntimeError("Live delivery subscription closed")
                        if data.get("topic") == topic and data.get("event") == "broadcast" and data.get("payload", {}).get("event") == "commands":
                            wake.set()
                finally:
                    pulse.cancel()
                    await asyncio.gather(pulse, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except (ClientError, OSError, RuntimeError, ValueError, TypeError, asyncio.TimeoutError):
            failures += 1
            state.delivery = "polling"
            LOGGER.warning("Live delivery reconnecting; command polling remains active")
        await asyncio.sleep(min(60, 2 ** min(failures, 6)))
