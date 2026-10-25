"""Device-scoped HA catalog. Secrets stay local; actions are revalidated locally.

Catalogs describe what HA advertises, not a promise that an integration implements
every action successfully. Disabled entities remain visible but cannot be called.
"""
from __future__ import annotations

import math
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from connector_protocol import ENTITY, minimal_snapshot

SECRET = re.compile(r"(^|_)(password|passwd|token|secret|api_key|access_key|authorization|credentials|pin|code)($|_)", re.I)
ACTUATORS = {"light", "switch", "fan", "cover", "climate", "media_player", "vacuum", "lock", "camera", "humidifier", "water_heater", "lawn_mower", "valve", "siren", "alarm_control_panel", "remote", "number", "select", "button", "text", "update"}
RESERVED = {"entity_id", "device_id", "area_id", "floor_id", "label_id", "target"}


def clean(value, omitted, path="", depth=0):
    """Keep nested JSON, with explicit redaction/truncation paths for the UI."""
    if depth > 16:
        omitted.append(path + " (depth limit)")
        return None
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key = str(key)
            if SECRET.search(key):
                omitted.append(path + "." + key + " (credential)")
                continue
            result[key] = clean(item, omitted, path + "." + key, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > 2000:
            omitted.append(path + " (list limited to 2000 items)")
        return [clean(v, omitted, path + f"[{i}]", depth + 1) for i, v in enumerate(value[:2000])]
    if isinstance(value, str):
        if len(value) > 32768:
            omitted.append(path + " (text limited to 32768 characters)")
        value = value[:32768]
        # Camera thumbnails often embed a bearer token, even in relative URLs.
        if "?" in value or "://" in value:
            try:
                url = urlsplit(value)
                query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True) if not SECRET.search(k)]
                if url.username or url.password or len(query) != len(parse_qsl(url.query, keep_blank_values=True)):
                    omitted.append(path + " (URL credential)")
                    return urlunsplit((url.scheme, url.hostname or "", url.path, urlencode(query), ""))
            except ValueError:
                omitted.append(path + " (invalid URL)")
                return None
        return value
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def matches(spec, entity, attrs):
    if isinstance(spec, list):
        return any(matches(s, entity, attrs) for s in spec)
    if not isinstance(spec, dict):
        return False
    for key, values in spec.items():
        values = values if isinstance(values, list) else [values]
        if key == "supported_features":
            features = int(attrs.get("supported_features") or 0)
            if not any(isinstance(v, int) and features & v == v for v in values):
                return False
        elif key in {"domain", "integration", "device_class"}:
            actual = entity["entity_id"].split(".")[0] if key == "domain" else entity.get("platform") if key == "integration" else attrs.get(key) or entity.get(key)
            if actual not in values:
                return False
        elif key == "filter":
            if not matches(values, entity, attrs):
                return False
        elif key in {"multiple", "include_entities", "exclude_entities"}:
            if key == "include_entities" and entity["entity_id"] not in values:
                return False
            if key == "exclude_entities" and entity["entity_id"] in values:
                return False
        else:
            return False  # Unknown target restrictions must never broaden access.
    return True


def flatten_fields(fields, entity, attrs):
    result = {}
    for name, spec in (fields or {}).items():
        if not isinstance(spec, dict):
            continue
        if "fields" in spec:
            result.update(flatten_fields(spec["fields"], entity, attrs))
            continue
        if name in RESERVED:
            continue
        filt = spec.get("filter", {})
        if "supported_features" in filt and not matches({"supported_features": filt["supported_features"]}, entity, attrs):
            continue
        if any(not any(v in (attrs.get(k) if isinstance(attrs.get(k), list) else [attrs.get(k)]) for v in (vs if isinstance(vs, list) else [vs])) for k, vs in filt.get("attribute", {}).items()):
            continue
        field = {k: v for k, v in spec.items() if k != "filter"}
        selector = field.get("selector") or {}
        state_selector = selector.get("state")
        if isinstance(state_selector, dict):
            attr = state_selector.get("attribute", "")
            options = attrs.get({"effect": "effect_list", "source": "source_list", "sound_mode": "sound_mode_list", "fan_speed": "fan_speed_list", "option": "options"}.get(attr, attr + "s"))
            if isinstance(options, list):
                field["selector"] = {"select": {"options": options}}
        result[name] = field
    return result


def matches_device(spec, entity, hardware):
    if isinstance(spec, list):
        return any(matches_device(s, entity, hardware) for s in spec)
    if not isinstance(spec, dict) or not entity.get("device_id"):
        return False
    for key, values in spec.items():
        values = values if isinstance(values, list) else [values]
        if key == "integration":
            if entity.get("platform") not in values:
                return False
        elif key in {"manufacturer", "model"}:
            if hardware.get(key) not in values:
                return False
        elif key != "multiple":
            return False
    return True


def actions_for(entity, attrs, services, hardware=None):
    result = []
    for domain, services_in_domain in services.items():
        for service, spec in services_in_domain.items():
            if not re.fullmatch(r"[a-z0-9_]+", domain) or not re.fullmatch(r"[a-z0-9_]+", service):
                continue
            if domain == "homeassistant" and (service not in {"turn_on", "turn_off", "toggle", "update_entity"} or (service != "update_entity" and entity["entity_id"].split(".")[0] not in ACTUATORS)):
                continue
            target = spec.get("target", {})
            # HA's supported entity-target actions, including integration-specific
            # actions, use the same target filters as its own action editor.
            selector = target.get("entity") if isinstance(target, dict) else None
            if selector is None:
                selector = (spec.get("fields", {}).get("entity_id", {}).get("selector") or {}).get("entity")
            binding = "entity_id"
            if selector is None or not matches(selector, entity, attrs):
                device_selector = target.get("device") if isinstance(target, dict) else None
                if device_selector is None:
                    device_selector = (spec.get("fields", {}).get("device_id", {}).get("selector") or {}).get("device")
                if device_selector is None or not matches_device(device_selector, entity, hardware or {}):
                    continue
                binding = "device_id"
            result.append({"id": f"{domain}.{service}", "domain": domain, "service": service,
                           "name": spec.get("name") or service.replace("_", " ").capitalize(),
                           "description": spec.get("description", ""),
                           "fields": flatten_fields(spec.get("fields"), entity, attrs), "binding": binding,
                           "response": spec.get("response")})
    return result


def full_snapshot(config, states, registries, installation_id, version):
    snapshot = minimal_snapshot(config, states, registries, installation_id, version)
    entities = {e["entity_id"]: e for e in registries.get("entities", []) if ENTITY.fullmatch(e.get("entity_id", ""))}
    state_map = {s["entity_id"]: s for s in states if ENTITY.fullmatch(s.get("entity_id", ""))}
    selected = {eid: e for eid, e in entities.items() if e.get("device_id")}
    for eid in state_map:
        if eid.split(".")[0] in ACTUATORS:
            selected.setdefault(eid, entities.get(eid, {"entity_id": eid}))
    used_devices = {e.get("device_id") for e in selected.values()}
    hardware = {d["id"]: d for d in registries.get("devices", []) if d.get("id")}
    details = {}
    for eid, entity in selected.items():
        key = entity.get("device_id") or eid
        if key not in details:
            omitted = []
            details[key] = {"hardware": clean(hardware.get(key, {}), omitted, "hardware"), "entities": [], "omitted": omitted}
        detail = details[key]
        state = state_map.get(eid, {"entity_id": eid, "state": "disabled" if entity.get("disabled_by") else "unavailable", "attributes": {}})
        attrs = state.get("attributes", {})
        actions = actions_for(entity, attrs, registries.get("services", {}), hardware.get(key, {})) if not entity.get("disabled_by") else []
        # Field names such as a lock's "code" are descriptors, never credentials.
        detail["entities"].append({"entityId": eid, "registry": clean(entity, detail["omitted"], eid + ".registry"),
                                   **clean({k: v for k, v in state.items() if k != "entity_id"}, detail["omitted"], eid), "actions": actions})
    for key, device in hardware.items():
        if key not in details:
            omitted = []
            details[key] = {"hardware": clean(device, omitted, "hardware"), "entities": [], "omitted": omitted}
    snapshot.update(schema_version=3, devices=[clean(d, [], "hardware") for d in hardware.values()],
                    entities=[clean(e, [], "entity") for e in selected.values()],
                    states=[clean(state_map.get(eid, {"entity_id": eid, "state": "disabled" if e.get("disabled_by") else "unavailable", "attributes": {}}), [], eid) for eid, e in selected.items()],
                    areas=[clean(a, []) for a in registries.get("areas", [])], details=details)
    return snapshot


def native_data(command, catalog, allow_locks=False, allow_camera=False):
    eid = command.get("entity_id")
    if not isinstance(eid, str) or not ENTITY.fullmatch(eid):
        raise ValueError("Invalid target")
    entity = catalog.get(eid)
    if not entity or (entity.get("state") in {"unavailable", "disabled"} or (entity.get("state") == "unknown" and not eid.startswith(("button.", "input_button.")))) or entity.get("registry", {}).get("disabled_by"):
        raise ValueError("Entity unavailable")
    action_id = f"{command.get('domain')}.{command.get('service')}"
    action = next((a for a in entity["actions"] if a["id"] == action_id), None)
    if not action or (eid.startswith("lock.") and not allow_locks) or (eid.startswith("camera.") and not allow_camera):
        raise ValueError("Action not enabled locally")
    params = command.get("parameters", {})
    if not isinstance(params, dict) or set(params) & RESERVED or set(params) - set(action["fields"]):
        raise ValueError("Unexpected action fields")
    for key, field in action["fields"].items():
        if field.get("required") and key not in params:
            raise ValueError("Missing required field")
        if key not in params:
            continue
        value = params[key]
        selector = field.get("selector") or {}
        number = selector.get("number") or selector.get("color_temp")
        if number is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or ("min" in number and value < number["min"]) or ("max" in number and value > number["max"]):
                raise ValueError("Numeric field out of range")
        if "boolean" in selector and type(value) is not bool:
            raise ValueError("Boolean field required")
        if "text" in selector and not isinstance(value, str):
            raise ValueError("Text field required")
        if "select" in selector and not selector["select"].get("custom_value"):
            options = [o.get("value") if isinstance(o, dict) else o for o in selector["select"].get("options", [])]
            if options and any(v not in options for v in (value if isinstance(value, list) else [value])):
                raise ValueError("Invalid option")
        if "color_rgb" in selector and (not isinstance(value, list) or len(value) != 3 or any(type(v) not in (int, float) or not 0 <= v <= 255 for v in value)):
            raise ValueError("Invalid RGB color")
    binding = action.get("binding", "entity_id")
    target = entity.get("registry", {}).get("device_id") if binding == "device_id" else eid
    if not target:
        raise ValueError("Device target no longer available")
    return {binding: target, **params}, bool(action.get("response"))
