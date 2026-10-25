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
