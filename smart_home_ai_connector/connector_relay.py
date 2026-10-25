"""Outbound HTTPS relay with destination-bound credentials and bounded retries."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone
from aiohttp import ClientSession
from connector_protocol import relay_address
from connector_utils import read_json, write_private_json


class PairingRequired(RuntimeError):
    pass


class RelayError(RuntimeError):
    def __init__(self, status: int):
        super().__init__(f"Relay request failed (HTTP {status})")
        self.status = status


class RelayClient:
    def __init__(self, session: ClientSession, relay_url: str, pairing_code: str, credentials_path: Path, installation_id: str, version: str) -> None:
        self.session = session
        self.relay_url = relay_address(relay_url)
        self.pairing_code = pairing_code.strip()
        self.credentials_path = credentials_path
        self.installation_id = installation_id
        self.version = version
        self.pairing_lock = asyncio.Lock()
        self.binding = hashlib.sha256((self.relay_url + "\n" + self.pairing_code).encode()).hexdigest()
        self.next_pair_attempt = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.relay_url and self.pairing_code)

    def _credentials(self) -> dict:
        return read_json(self.credentials_path, {})

    async def _json(self, response) -> dict:
        if response.content_type != "application/json":
            raise RuntimeError("Relay returned an unexpected response format")
        content = bytearray()
        while chunk := await response.content.read(16384):
            content.extend(chunk)
            if len(content) > 262144:
                raise RuntimeError("Relay response is too large")
        value = json.loads(content)
        if not isinstance(value, dict):
            raise RuntimeError("Relay response must be an object")
        return value

    async def _access_token(self, home_name: str) -> str:
        async with self.pairing_lock:
            credentials = self._credentials()
            if credentials.get("binding") == self.binding:
                if credentials.get("revoked"):
                    raise PairingRequired("Pairing expired or was revoked; generate a new pairing code in HomeCore")
                token = credentials.get("access_token")
                if isinstance(token, str) and token:
                    return token
            now = asyncio.get_running_loop().time()
            if now < self.next_pair_attempt:
                raise PairingRequired("Pairing is waiting before retrying")
            self.next_pair_attempt = now + 60
            payload = {"pairing_code": self.pairing_code, "installation_id": self.installation_id, "home_name": home_name, "connector_version": self.version}
            async with self.session.post(f"{self.relay_url}/v1/connectors/pair", json=payload, allow_redirects=False) as response:
                if response.status in (400, 401, 403, 410):
                    write_private_json(self.credentials_path, {"installation_id": self.installation_id, "binding": self.binding, "revoked": True})
                    raise PairingRequired("Pairing code was rejected; generate a new code in HomeCore")
                if response.status != 200:
                    raise RelayError(response.status)
                body = await self._json(response)
                token = body.get("access_token")
                if not isinstance(token, str) or not 32 <= len(token) <= 4096:
                    raise RuntimeError("Relay did not return a valid connector credential")
                write_private_json(self.credentials_path, {"installation_id": self.installation_id, "binding": self.binding, "relay_url": self.relay_url, "access_token": token, "paired_at": datetime.now(timezone.utc).isoformat()})
                return token

    async def _request(self, method: str, path: str, home_name: str, expected: int, payload: Any = None, read_body: bool = False) -> dict:
        token = await self._access_token(home_name)
        async with self.session.request(method, f"{self.relay_url}/v1/connectors/{path}", json=payload, headers={"Authorization": f"Bearer {token}"}, allow_redirects=False) as response:
            if response.status in (401, 403):
                async with self.pairing_lock:
                    saved = self._credentials()
                    if saved.get("binding") == self.binding and saved.get("access_token") == token:
                        write_private_json(self.credentials_path, {"installation_id": self.installation_id, "binding": self.binding, "revoked": True})
                raise PairingRequired("Connector access was rejected; generate a new pairing code in HomeCore")
            if response.status != expected:
                raise RelayError(response.status)
            return await self._json(response) if read_body else {}

    async def sync(self, snapshot: dict) -> None:
        await self._request("POST", "snapshot", snapshot["home"]["name"], 202, snapshot)

    async def live(self, home_name: str) -> dict:
        return await self._request("GET", "live", home_name, 200, read_body=True)

    async def commands(self, home_name: str) -> list[dict]:
        body = await self._request("GET", "commands", home_name, 200, read_body=True)
        commands = body.get("commands")
        if not isinstance(commands, list) or len(commands) > 20 or any(not isinstance(c, dict) for c in commands):
            raise RuntimeError("Relay returned an invalid command batch")
        return commands

    async def command_result(self, home_name: str, command_id: str, success: bool, error: str | None = None) -> None:
        await self._request("POST", "commands/result", home_name, 200, {"id": command_id, "success": success, "error": error})
