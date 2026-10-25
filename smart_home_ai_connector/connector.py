#!/usr/bin/env python3
"""Smart Home AI's device-local Home Assistant connector."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web
from connector_utils import read_json, stable_installation_id, websocket_url
from connector_protocol import minimal_snapshot, service_data
from connector_relay import RelayClient, PairingRequired
from connector_commands import CommandProcessor


CONNECTOR_VERSION = "0.5.0"
DEFAULT_HA_API_URL = "http://supervisor/core/api"
DEFAULT_HA_WS_URL = "ws://supervisor/core/websocket"
DEFAULT_OPTIONS_PATH = "/data/options.json"
DEFAULT_CREDENTIALS_PATH = "/data/connector-credentials.json"
HEALTH_PORT = 8099

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("smart-home-ai-connector")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RuntimeState:
    started_at: str = field(default_factory=utc_now)
    phase: str = "starting"
    last_discovery_at: str | None = None
    last_sync_at: str | None = None
    last_error: str | None = None
    area_count: int = 0
    device_count: int = 0
    entity_count: int = 0
    state_count: int = 0
    home_name: str | None = None
    relay_status: str = "not configured"

    def public(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": CONNECTOR_VERSION,
            "started_at": self.started_at,
            "phase": self.phase,
            "last_discovery_at": self.last_discovery_at,
            "last_sync_at": self.last_sync_at,
            "last_error": self.last_error,
            "home_name": self.home_name,
            "counts": {
                "areas": self.area_count,
                "devices": self.device_count,
                "entities": self.entity_count,
                "states": self.state_count,
            },
            "relay_status": self.relay_status,
        }


class HomeAssistantClient:
    def __init__(self, session: ClientSession, api_url: str, token: str) -> None:
        self.session = session
        self.api_url = api_url.rstrip("/")
        self.ws_url = websocket_url(self.api_url, os.getenv("SMART_HOME_HA_WS_URL"))
        self.token = token

    async def _rest(self, path: str) -> Any:
        async with self.session.get(
            f"{self.api_url}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {self.token}"}, allow_redirects=False,
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def call_service(self, domain: str, service: str, entity_id: str, parameters: dict | None = None, allow_locks: bool = False) -> None:
        data = service_data(domain, service, entity_id, parameters if parameters is not None else {}, allow_locks)
        async with self.session.post(
            f"{self.api_url}/services/{domain}/{service}", json=data,
            headers={"Authorization": f"Bearer {self.token}"}, allow_redirects=False,
        ) as response:
            if response.status not in (200, 201):
                raise RuntimeError(f"Home Assistant service failed (HTTP {response.status})")

    async def _registry_snapshot(self) -> dict[str, Any]:
        async with self.session.ws_connect(self.ws_url, heartbeat=30) as ws:
            hello = await ws.receive_json()
            if hello.get("type") != "auth_required":
                raise RuntimeError("Home Assistant websocket did not request authentication")
            await ws.send_json({"type": "auth", "access_token": self.token})
            auth = await ws.receive_json()
            if auth.get("type") != "auth_ok":
                raise RuntimeError("Home Assistant rejected internal connector access")

            requests = {
                "areas": "config/area_registry/list",
                "devices": "config/device_registry/list",
                "entities": "config/entity_registry/list",
            }
            pending: dict[int, str] = {}
            for request_id, (name, command) in enumerate(requests.items(), start=1):
                pending[request_id] = name
                await ws.send_json({"id": request_id, "type": command})

            result: dict[str, Any] = {name: [] for name in requests}
            while pending:
                message = await ws.receive()
                if message.type != WSMsgType.TEXT:
                    raise RuntimeError("Home Assistant closed the registry connection")
                payload = json.loads(message.data)
                request_id = payload.get("id")
                if request_id not in pending:
                    continue
                name = pending.pop(request_id)
                if not payload.get("success"):
                    raise RuntimeError(f"Home Assistant could not list {name}")
                result[name] = payload.get("result", [])
            return result

    async def snapshot(self, installation_id: str) -> dict[str, Any]:
        async with asyncio.timeout(30):
            config, states, registries = await asyncio.gather(
                self._rest("config"), self._rest("states"), self._registry_snapshot(),
            )
        snapshot = minimal_snapshot(config, states, registries, installation_id, CONNECTOR_VERSION)
        snapshot["capabilities"] = getattr(self, "control_capabilities", {"light_control": False, "lock_control": False})
        return snapshot


def update_counts(state: RuntimeState, snapshot: dict[str, Any]) -> None:
    state.home_name = snapshot["home"].get("name")
    state.area_count = len(snapshot.get("areas", []))
    state.device_count = len(snapshot.get("devices", []))
    state.entity_count = len(snapshot.get("entities", []))
    state.state_count = len(snapshot.get("states", []))
    state.last_discovery_at = snapshot.get("captured_at")


async def health(request: web.Request) -> web.Response:
    state: RuntimeState = request.app["runtime_state"]
    return web.json_response(state.public())


async def discovery_loop(
    state: RuntimeState,
    home_assistant: HomeAssistantClient,
    relay: RelayClient,
    installation_id: str,
    interval_seconds: int,
) -> None:
    failures = 0
    while True:
        try:
            state.phase = "discovering"
            snapshot = await home_assistant.snapshot(installation_id)
            update_counts(state, snapshot)
            LOGGER.info(
                "Discovered %s areas, %s devices, %s entities, and %s states",
                state.area_count,
                state.device_count,
                state.entity_count,
                state.state_count,
            )
            if relay.configured:
                state.phase = "syncing"
                await relay.sync(snapshot)
                state.last_sync_at = utc_now()
                state.relay_status = "paired"
                LOGGER.info("Snapshot synced to Smart Home AI")
            else:
                state.relay_status = "waiting for pairing"
                LOGGER.info("Local discovery is ready; cloud pairing has not been configured")
            state.phase = "ready"
            state.last_error = None
            failures = 0
        except asyncio.CancelledError:
            raise
        except (ClientError, asyncio.TimeoutError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            state.phase = "retrying"
            state.last_error = "Pairing required" if isinstance(error, PairingRequired) else "Discovery or sync failed; retrying"
            state.relay_status = "pairing required" if isinstance(error, PairingRequired) else "retrying"
            failures += 1
            LOGGER.warning("Connector cycle failed (%s)", type(error).__name__)
        await asyncio.sleep(retry_delay(interval_seconds, failures))


def retry_delay(interval: int, failures: int) -> float:
    return min(max(300, interval), interval * (2 ** min(failures, 5))) + random.uniform(0, min(5, interval / 5))


async def command_loop(state: RuntimeState, home_assistant: HomeAssistantClient, relay: RelayClient, installation_id: str, processor: CommandProcessor, interval: int) -> None:
    failures = 0
    while True:
        try:
            if relay.configured and state.home_name and processor.allow_control:
                commands = await relay.commands(state.home_name)
                for command in commands:
                    await processor.process(command, home_assistant, relay, state.home_name)
                if commands:
                    snapshot = await home_assistant.snapshot(installation_id)
                    snapshot["capabilities"] = {"light_control": processor.allow_control, "lock_control": processor.allow_control and processor.allow_locks}
                    update_counts(state, snapshot)
                    await relay.sync(snapshot)
                    state.last_sync_at = utc_now()
            failures = 0
        except asyncio.CancelledError:
            raise
        except (ClientError, asyncio.TimeoutError, OSError, RuntimeError, ValueError, TypeError) as error:
            failures += 1
            LOGGER.warning("Command polling paused (%s)", type(error).__name__)
        await asyncio.sleep(retry_delay(interval, failures))


async def main() -> None:
    options_path = Path(os.getenv("SMART_HOME_OPTIONS_PATH", DEFAULT_OPTIONS_PATH))
    credentials_path = Path(os.getenv("SMART_HOME_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH))
    options = read_json(options_path, {})
    token = os.getenv("SUPERVISOR_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SUPERVISOR_TOKEN is missing; homeassistant_api must be enabled")

    relay_url = str(options.get("relay_url", "")).strip()
    pairing_code = str(options.get("pairing_code", "")).strip()
    poll_seconds = max(5, min(300, int(options.get("command_poll_seconds", 15))))
    interval_seconds = max(15, min(3600, int(options.get("sync_interval_seconds", 60))))
    api_url = os.getenv("SMART_HOME_HA_API_URL", DEFAULT_HA_API_URL).rstrip("/")
    installation_id = stable_installation_id(credentials_path)
    state = RuntimeState()

    timeout = ClientTimeout(total=30, connect=10)
    async with ClientSession(timeout=timeout) as session:
        home_assistant = HomeAssistantClient(session, api_url, token)
        relay = RelayClient(session, relay_url, pairing_code, credentials_path, installation_id, CONNECTOR_VERSION)
        processor = CommandProcessor(credentials_path.with_name("command-receipts.json"), options.get("allow_control") is True, options.get("allow_locks") is True)
        home_assistant.control_capabilities = {"light_control": processor.allow_control, "lock_control": processor.allow_control and processor.allow_locks}
        app = web.Application()
        app["runtime_state"] = state
        app.router.add_get("/health", health)
        app.router.add_get("/status", health)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
        await site.start()
        LOGGER.info(
            "Smart Home AI Connector %s started on %s (%s)",
            CONNECTOR_VERSION,
            socket.gethostname(),
            hashlib.sha256(installation_id.encode()).hexdigest()[:8],
        )
        discovery_task = asyncio.create_task(
            discovery_loop(state, home_assistant, relay, installation_id, interval_seconds)
        )
        command_task = asyncio.create_task(command_loop(state, home_assistant, relay, installation_id, processor, poll_seconds))
        try:
            await asyncio.gather(discovery_task, command_task)
        finally:
            discovery_task.cancel()
            command_task.cancel()
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
