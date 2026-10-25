# Security boundaries

Version 0.5.0 reads Home Assistant using its runtime `SUPERVISOR_TOKEN`. That credential stays on the Home Assistant host; the cloud receives only a separate revocable connector credential.

- No inbound router port, Home Assistant password, or long-lived Home Assistant token is required. The health port is disabled externally by default.
- Relay connections require HTTPS on port 443. Redirects are not followed. Credentials are bound to the canonical relay address and pairing code, and saved atomically with owner-only permissions.
- Pairing is serialized. A rejected/revoked credential is removed and requires a new pairing code. Logs contain error categories, not request headers, response bodies, or snapshots.
- Export includes supported device states, selected control capabilities, room mapping, manufacturer/model, and health readings. It excludes person/device-tracker/calendar entities, coordinates, raw registry connections/identifiers, camera images/streams, media URLs, and arbitrary nested attributes. Unknown/free-text sensors are excluded. Home and room names are included so HomeCore can organize the devices.
- Remote control defaults off. The current command allowlist supports exact light entity IDs and bounded brightness/hue values. Locks require both remote control and the separate lock setting. Other devices are observation-only in this release.
- Commands must have a UUID and a lifetime of at most 120 seconds. A receipt is persisted before execution. If a crash makes the outcome uncertain, it is not executed again; the user must check device state. Acknowledgements can be retried without repeating the action. A corrupted receipt store stops remote processing.
- This bridge is not a life-safety system. Home Assistant and the devices remain responsible for local alarms and automations.

Never commit runtime credentials, home snapshots, personal logs, addresses, or pairing codes. Report security issues privately to the repository owner.
