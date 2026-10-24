# Smart Home AI Connector

HomeCore's outbound Home Assistant bridge. Runs beside Home Assistant on Home Assistant OS and uses its Supervisor-provided credential locally. No long-lived Home Assistant token or public router port is needed.

## Version 0.5.0

- HTTPS-only pairing with credentials bound to the exact relay address and pairing code.
- Revoked access stops until a new pairing code is configured. Failed requests back off instead of polling twice a second.
- Explicit device-data export replaces raw Home Assistant records. Camera streams, coordinates, people, calendars, credentials, and hardware network identifiers are excluded.
- Durable command receipts prevent a lost acknowledgement or connector restart from repeating a physical action. Commands expire within two minutes.
- Remote control is off by default. Light control and lock control have separate local settings.

**Upgrade:** 0.4.x credentials cannot be safely associated with a relay, so 0.5.0 requires a fresh HomeCore pairing code. The relay must support snapshot schema 2 and expiring commands. Do not point this release at an old prototype relay. The separately installed **Smart Home Core Connector 0.9.0** local interface is not replaced or modified by this app.

## Install

Add `https://github.com/nikeblue8513/smart-home-ai-connector` as a custom repository in Home Assistant. Install **Smart Home AI Connector**, enable Start on boot and Watchdog, and use the relay URL and short-lived pairing code shown by HomeCore's Home Assistant integration screen. Save the configuration and restart the app.

See [configuration and protocol documentation](smart_home_ai_connector/DOCS.md) and [security boundaries](SECURITY.md).

## Verify changes

With Python 3.12 and `aiohttp` installed, run `python -m unittest discover -s smart_home_ai_connector -v`. Tests use synthetic data and mocked transports; they do not operate real devices.
