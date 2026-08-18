# Smart Home AI Connector

The connector runs on the same Raspberry Pi as Home Assistant OS. Home Assistant starts it automatically after Home Assistant Core is ready and restarts it if its health check stops responding.

## What version 0.1.0 does

- Uses the Supervisor-provided `SUPERVISOR_TOKEN`; you never create or paste a Home Assistant long-lived token.
- Reads the area, device, entity, and state registries through Home Assistant's internal API proxy.
- Removes likely credential fields before a snapshot can leave the home.
- Keeps the Home Assistant and router ports closed.
- Exposes only an internal health endpoint for the Supervisor watchdog.
- Waits for a Smart Home AI pairing code before sending data outward.

## Local install

1. Create a current Home Assistant backup.
2. Copy the `smart_home_ai_connector` folder to `/addons` using the Samba or SSH app.
3. In Home Assistant, open **Settings → Apps → App store**.
4. Open the menu and select **Check for updates**.
5. Install **Smart Home AI Connector**.
6. Leave the relay URL and pairing code blank for the first local discovery run.
7. Turn on **Start on boot** and **Watchdog**, then start the app.
8. Open the app logs. A successful run reports the counts of discovered areas, devices, entities, and states.

The next connector milestone supplies a short-lived pairing code from the private Smart Home AI dashboard and enables the outbound relay.

