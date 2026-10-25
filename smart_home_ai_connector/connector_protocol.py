"""Small, explicit outbound protocol; no raw Home Assistant data leaves the home."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
DOMAINS = {"light", "switch", "fan", "cover", "climate", "media_player", "vacuum", "lock", "camera", "sensor", "binary_sensor"}
SENSOR_CLASSES = {"battery", "temperature", "humidity", "illuminance", "power", "energy", "current", "voltage", "signal_strength", "carbon_dioxide", "carbon_monoxide", "pm1", "pm10", "pm25", "volatile_organic_compounds", "volatile_organic_compounds_parts", "water", "gas", "pressure", "atmospheric_pressure", "duration", "distance", "volume", "volume_flow_rate", "speed"}
BINARY_CLASSES = {"battery", "battery_charging", "carbon_monoxide", "cold", "connectivity", "door", "garage_door", "gas", "heat", "light", "lock", "moisture", "motion", "moving", "occupancy", "opening", "plug", "power", "presence", "problem", "running", "safety", "smoke", "sound", "tamper", "update", "vibration", "window"}
ATTRIBUTES = {"friendly_name", "device_class", "unit_of_measurement", "supported_features", "brightness", "hs_color", "rgb_color", "color_temp_kelvin", "min_color_temp_kelvin", "max_color_temp_kelvin", "supported_color_modes", "color_mode", "current_position", "current_tilt_position", "current_temperature", "current_humidity", "temperature", "target_temp_high", "target_temp_low", "min_temp", "max_temp", "target_temp_step", "hvac_action", "hvac_modes", "fan_mode", "fan_modes", "percentage", "percentage_step", "preset_mode", "preset_modes", "oscillating", "direction", "volume_level", "is_volume_muted", "battery_level"}


def relay_address(value: str) -> str:
    if not value.strip():
        return ""
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Relay URL must be HTTPS without credentials, query parameters, or a fragment")
    if parsed.port not in (None, 443) or any(x in parsed.path for x in ("..", "%", "\\")):
        raise ValueError("Relay URL must use port 443 and a plain base path")
    return urlunsplit(("https", parsed.hostname.lower(), parsed.path.rstrip("/"), "", ""))


def scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    if isinstance(value, list) and len(value) <= 32:
        return [scalar(item) for item in value if isinstance(item, (str, int, float, bool))]
    return None


def fields(value: dict, names: set[str]) -> dict:
    return {key: scalar(value[key]) for key in names if key in value}


def minimal_snapshot(config: dict, states: list, registries: dict, installation_id: str, version: str) -> dict:
    entities = {e.get("entity_id"): e for e in registries.get("entities", []) if isinstance(e, dict)}
    clean_states = []
    clean_entities = []
    used_devices = set()
    used_areas = set()
    for state in states:
        if not isinstance(state, dict):
            continue
        entity_id = state.get("entity_id", "")
        if not isinstance(entity_id, str) or not ENTITY.fullmatch(entity_id):
            continue
        domain = entity_id.split(".")[0]
        attrs = state.get("attributes") or {}
        entity = entities.get(entity_id, {})
        if domain not in DOMAINS or entity.get("disabled_by") or entity.get("hidden_by"):
            continue
        if domain == "sensor" and attrs.get("device_class") not in SENSOR_CLASSES:
            continue
        if domain == "binary_sensor" and attrs.get("device_class") not in BINARY_CLASSES:
            continue
        # Numeric sensors cannot smuggle free-form text (addresses, calendar entries, URLs).
        value = state.get("state")
        if domain == "sensor" and value not in ("unknown", "unavailable"):
            try:
                if not math.isfinite(float(value)):
                    continue
            except (TypeError, ValueError):
                continue
        clean_states.append({**fields(state, {"entity_id", "state", "last_changed", "last_updated"}), "attributes": fields(attrs, ATTRIBUTES)})
        clean_entities.append({"entity_id": entity_id, **fields(entity, {"device_id", "area_id", "name", "original_name", "icon", "entity_category"})})
        used_devices.add(entity.get("device_id"))
        used_areas.add(entity.get("area_id"))
    devices = []
    for device in registries.get("devices", []):
        if device.get("id") in used_devices:
            devices.append(fields(device, {"id", "area_id", "name", "name_by_user", "manufacturer", "model", "model_id", "sw_version", "hw_version"}))
            used_areas.add(device.get("area_id"))
    return {
        "schema_version": 2, "connector_version": version, "installation_id": installation_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "home": {"name": scalar(config.get("location_name") or "Home"), "temperature_unit": scalar(config.get("unit_system", {}).get("temperature")), "time_zone": scalar(config.get("time_zone"))},
        "areas": [fields(area, {"area_id", "name", "icon", "floor_id"}) for area in registries.get("areas", []) if area.get("area_id") in used_areas],
        "devices": devices, "entities": clean_entities, "states": clean_states,
    }


def service_data(domain: str, service: str, entity_id: str, parameters: Any, allow_locks: bool = False) -> dict:
    allowed = {("light", "turn_on"), ("light", "turn_off")}
    if allow_locks:
        allowed |= {("lock", "lock"), ("lock", "unlock")}
    if (domain, service) not in allowed or not isinstance(entity_id, str) or not ENTITY.fullmatch(entity_id) or not entity_id.startswith(domain + "."):
        raise ValueError("Command is outside the connector allowlist")
    if not isinstance(parameters, dict):
        raise ValueError("Command parameters must be an object")
    if parameters and (domain, service) != ("light", "turn_on"):
        raise ValueError("Parameters are only supported for light.turn_on")
    if set(parameters) - {"brightnessPct", "hue"}:
        raise ValueError("Unsupported command parameters")
    data = {"entity_id": entity_id}
    for key, minimum, maximum in (("brightnessPct", 1, 100), ("hue", 0, 360)):
        if key in parameters:
            value = parameters[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise ValueError("Command value is outside the allowed range")
            if key == "brightnessPct":
                data["brightness_pct"] = value
            else:
                data["hs_color"] = [value, 100]
    return data


def command_expiry(command: dict, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    try:
        expires = datetime.fromisoformat(command["expires_at"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(command["created_at"].replace("Z", "+00:00"))
        if expires.tzinfo is None or created.tzinfo is None:
            raise ValueError()
        if not 0 < (expires - now).total_seconds() <= 120 or not 0 < (expires - created).total_seconds() <= 120 or (created - now).total_seconds() > 10:
            raise ValueError()
        return expires
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("Command is expired or has an invalid lifetime") from None
