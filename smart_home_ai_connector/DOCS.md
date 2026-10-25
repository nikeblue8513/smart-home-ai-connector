# HomeCore connector 0.8.0

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `relay_url` | blank | HTTPS base address supplied by HomeCore. No query string, credentials, or fragment. |
| `pairing_code` | blank | Short-lived pairing code. Keep it configured; its fingerprint binds the saved connector token to this pairing. |
| `sync_interval_seconds` | 60 | Device snapshots; allowed range 15–3600 seconds. |
| `command_poll_seconds` | 15 | Command polling; allowed range 5–300 seconds, with exponential backoff on failures. |
| `allow_control` | false | Enables remote light commands. Observation continues when off. |
| `allow_camera` | false | Enables on-demand receive-only WebRTC camera viewing for the paired HomeCore admin. |
| `allow_locks` | false | Enables lock/unlock only when remote control is also enabled. |

With no relay/code, discovery remains local. Pairing does not expose Home Assistant to the Internet. Generate a new code after revocation, a changed relay URL, or upgrading from 0.4.x. A failed initial pairing response may also require a new code. Existing local Home Assistant automations and the separate Core Connector interface continue independently.

## Relay contract

Requests use `<relay_url>/v1/connectors/...`. Only pairing omits the connector bearer credential.

- `POST pair`: `{ pairing_code, installation_id, home_name, connector_version }` → 200 `{ access_token }`.
- `POST snapshot`: explicit schema-2 snapshot from `connector_protocol.py` → 202. `capabilities.light_control` and `capabilities.lock_control` reflect local policy.
- `GET commands`: 200 `{ commands: [...] }`, maximum 20 per batch. Each command has `id` (UUID), `created_at`, `expires_at` (timezone-aware timestamps, at most 120 seconds apart), `domain`, `service`, `entity_id`, and `parameters` (object).
- `POST commands/result`: `{ id, success, error }` → 200. The relay must accept repeated acknowledgements idempotently.

Allowed commands: `light.turn_on`, `light.turn_off`; optionally `lock.lock`, `lock.unlock`. `light.turn_on` accepts `brightnessPct` 1–100 and `hue` 0–360. Unknown parameters and wildcard/multiple entity targets are rejected. Other discovered device types are observation-only until their control adapters are implemented.

No untrusted response body is written to logs. 401/403 revokes the saved credential and stops requests until re-pairing. Other errors use bounded backoff. Health endpoints report discovery/sync timestamps and pairing status; process health alone does not prove cloud connectivity.

## Data and persistence

`/data/connector-credentials.json` stores the installation ID and pairing credential. `/data/command-receipts.json` stores short-lived execution receipts. Keep both in an app backup. Do not copy them into GitHub or chat. Never remove the receipt store while commands are being delivered.

Device export includes supported domains and known numeric/binary sensor classes. Unknown sensor classes, hidden/disabled entities, phone locations, calendar entries, camera/media URLs, and registry hardware identifiers are excluded. See `connector_protocol.py` for the exact allowlists.

### Live delivery

HomeCore sends empty wake notifications through the existing Supabase Realtime service. Commands and snapshots still require the paired credential; notifications never contain device data or actions. Home Assistant state events trigger a coalesced snapshot, with registry discovery cached for up to 60 seconds. Poll and sync settings are recovery intervals when delivery is interrupted. Updates keep the current pairing and control permissions.

Light commands leave transition, effects, and unspecified brightness/color values to Home Assistant and the device. HomeCore does not force an instant fade or a default turn-on brightness.

## On-demand camera viewing

Enable **Allow camera live view** only if you want the paired HomeCore administrator to view exported cameras. This permission is separate from light control and defaults off on updates. No new pairing is needed.

Open a camera in HomeCore and choose **Open live view**. The browser negotiates WebRTC through Home Assistant's native camera integration. The connector relays only short-lived SDP/ICE messages through the private HomeCore database; media flows using the camera integration's WebRTC route, which may include the provider's existing TURN servers. HomeCore does not record, request your microphone, or send camera content to Jules/OpenAI.

At most two sessions may be active. Leaving the page closes the session, a missing browser heartbeat expires it after 60 seconds, and every view ends after five minutes. Disabling permission or revoking pairing stops sessions. Home Assistant and the camera provider must support WebRTC live view; verify the same camera opens in Home Assistant first. A still image or recording thumbnail is not used as a substitute for live video.

Authenticated relay endpoints: `GET camera/sessions` returns the bounded session/offer/candidate set; `POST camera/event` accepts configuration, session, answer, candidate or error signals for an existing session. Session IDs never grant authority by themselves. No signaling content, media URLs or credentials are logged.

## Full device access

Enable **Full device access** to send all registered device entities, attributes and equipment metadata to the paired HomeCore account. This includes diagnostic and configuration entities and mobile-device telemetry when a phone is registered in Home Assistant. Passwords and tokens remain local; omitted fields are listed in HomeCore. Disabled entities remain visible but do not have active readings.

With **Allow remote device control** also enabled, HomeCore can use the actions advertised by Home Assistant for those device/entity targets. Locks and cameras still require their separate permissions. Global administration and arbitrary scripts are not exposed. HomeCore uses the exact service fields and local validation; the connector never accepts a different target injected into parameters.

The initial view contains current state, not Home Assistant's historical database or integration diagnostics downloads. Unsupported custom service target schemas remain unavailable rather than being widened to all devices.
