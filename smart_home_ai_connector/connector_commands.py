"""Durable at-most-once command execution, including lost acknowledgements."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from aiohttp import ClientError
from connector_protocol import command_expiry, service_data
from connector_utils import write_private_json


class CommandProcessor:
    def __init__(self, path: Path, allow_control: bool = False, allow_locks: bool = False):
        self.path = path
        self.allow_control = allow_control
        self.allow_locks = allow_locks
        try:
            self.receipts = json.loads(path.read_text())
        except FileNotFoundError:
            self.receipts = {}
        if not isinstance(self.receipts, dict) or any(not isinstance(v, dict) for v in self.receipts.values()):
            raise RuntimeError("Command receipt store is invalid; remote control is stopped")

    def _save(self):
        write_private_json(self.path, self.receipts)

    async def process(self, command: dict, home_assistant, relay, home_name: str):
        command_id = command.get("id")
        try:
            if not isinstance(command_id, str) or str(uuid.UUID(command_id)) != command_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("Command ID must be a UUID") from None
        fingerprint = hashlib.sha256(json.dumps(command, sort_keys=True, allow_nan=False).encode()).hexdigest()
        saved = self.receipts.get(command_id)
        if saved:
            if saved.get("fingerprint") != fingerprint:
                await relay.command_result(home_name, command_id, False, "Command ID was reused with different contents")
                return
            await relay.command_result(home_name, command_id, saved.get("success", False), saved.get("error") or ("Execution outcome is uncertain; command will not be replayed" if saved.get("pending") else None))
            return
        error = None
        try:
            command_expiry(command)
            if not self.allow_control:
                raise ValueError("Remote control is disabled in the connector")
            service_data(command.get("domain"), command.get("service"), command.get("entity_id"), command.get("parameters", {}), self.allow_locks)
        except (ValueError, TypeError):
            error = "Command rejected by local policy or expiration"
        # Retain receipts for a day; expired commands can never be executed again.
        self.receipts = {k: v for k, v in self.receipts.items() if v.get("recorded_at", 0) > time.time() - 86400}
        if len(self.receipts) >= 10000:
            raise RuntimeError("Command receipt store is full; remote control is stopped")
        receipt = {"fingerprint": fingerprint, "recorded_at": time.time(), "pending": error is None, "success": False, "error": error}
        self.receipts[command_id] = receipt
        self._save()  # Durable BEFORE calling HA: a crash must not repeat a physical action.
        if error is None:
            try:
                await home_assistant.call_service(command["domain"], command["service"], command["entity_id"], command.get("parameters", {}), self.allow_locks)
                receipt["success"] = True
            except (ClientError, TimeoutError, OSError, RuntimeError, ValueError):
                receipt["error"] = "Home Assistant did not confirm the action; check device state before retrying"
            receipt["pending"] = False
            self._save()
        # Delivery failures propagate without overwriting the execution result.
        await relay.command_result(home_name, command_id, receipt["success"], receipt["error"])
