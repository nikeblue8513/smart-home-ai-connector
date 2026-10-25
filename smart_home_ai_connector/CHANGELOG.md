# 0.8.0

- Optional Full device access exports all registered device entities, nested attributes, equipment metadata, diagnostics, configuration and disabled-entity records.
- Discovers entity- and device-targeted actions from Home Assistant's service catalog, including feature and field restrictions.
- Revalidates commands locally against the catalog and current entity state. Existing light, lock and camera permissions remain enforced.
- Credentials are redacted with visible coverage information. Full access defaults off during an upgrade; existing light and camera behavior is preserved.

# 0.7.0

- Add separately enabled, on-demand camera WebRTC signaling for HomeCore. Video/audio stay on the camera provider connection; no recordings or snapshots are relayed. Sessions close on disconnect and are limited to five minutes.

# 0.6.1

- Preserve Home Assistant and light defaults for fades, transitions, and unspecified turn-on values.
- Compatible with continuous HomeCore dimming and color selection.

# 0.6.0

- Live command notifications with durable polling recovery.
- Immediate state updates from Home Assistant.
- Shared dimming, color saturation, and white temperature controls.
- No new inbound ports, permissions, or pairing required.
