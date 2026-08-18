# Security

Smart Home AI Connector 0.1.0 is a local-discovery release.

- Home Assistant supplies a temporary internal API credential to the running app through `SUPERVISOR_TOKEN`.
- No Home Assistant long-lived token is created, pasted into a website, or committed to GitHub.
- The app exposes no router port. Its health endpoint is internal and used by the Home Assistant watchdog.
- The connector uses read operations to list configuration, areas, devices, entities, and states. It does not send Home Assistant control commands.
- The default relay URL and pairing code are blank. In that default state, no home snapshot is transmitted outside the device.
- Snapshot sanitization removes fields whose names indicate tokens, API keys, passwords, authorization values, or secrets before future relay transmission.

Do not place credentials, exported Home Assistant data, logs containing personal data, or local network addresses in this repository or in public issue reports.

