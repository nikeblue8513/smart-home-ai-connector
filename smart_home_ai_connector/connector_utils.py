"""Dependency-free helpers shared by the connector and its tests."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_HA_API_URL = "http://supervisor/core/api"
DEFAULT_HA_WS_URL = "ws://supervisor/core/websocket"
SENSITIVE_FRAGMENTS = ("access_token", "api_key", "apikey", "authorization", "password", "secret", "token")


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default.copy()
    return data if isinstance(data, dict) else default.copy()


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def sanitize(value: Any) -> Any:
    """Remove likely credentials before any snapshot can leave the home."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in SENSITIVE_FRAGMENTS):
                continue
            clean[str(key)] = sanitize(item)
        return clean
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def stable_installation_id(seed_path: Path) -> str:
    saved = read_json(seed_path, {})
    existing = saved.get("installation_id")
    if isinstance(existing, str) and existing:
        return existing
    installation_id = secrets.token_hex(16)
    write_private_json(seed_path, {**saved, "installation_id": installation_id})
    return installation_id


def websocket_url(api_url: str, override: str | None = None) -> str:
    if override:
        return override.rstrip("/")
    if api_url == DEFAULT_HA_API_URL:
        return DEFAULT_HA_WS_URL
    parsed = urlparse(api_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    base_path = parsed.path.removesuffix("/api")
    return f"{scheme}://{parsed.netloc}{base_path}/websocket"

