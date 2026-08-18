# Smart Home AI Connector

The connector runs on the same Raspberry Pi as Home Assistant OS. Home Assistant starts it automatically after Home Assistant Core is ready and restarts it if its health check stops responding.

## What version 0.2.0 does

- Uses the Supervisor-provided `SUPERVISOR_TOKEN`; you never create or paste a Home Assistant long-lived token.
- Reads the area, device, entity, and state registries through Home Assistant's internal API proxy.
- Removes likely credential fields before a snapshot can leave the home.
- Keeps the Home Assistant and router ports closed.
- Exposes only an internal health endpoint for the Supervisor watchdog.
- Waits for a Smart Home AI pairing code before sending data outward.

## Install from the repository

1. Create a current Home Assistant backup.
2. In Home Assistant, open **Settings → Apps → App store**.
3. Open the menu in the top-right and choose **Repositories**.
4. Add `https://github.com/nikeblue8513/smart-home-ai-connector`.
5. Select and install **Smart Home AI Connector** from the App store.
6. Turn on **Start on boot** and **Watchdog**, then start the app.
7. In the private Smart Home AI dashboard, generate a one-time pairing code.
8. Open the connector's **Configuration** tab, enter the code, save, and restart the connector.
9. Open the app logs. A successful run reports the discovered counts and confirms that the snapshot synced.

The private Smart Home AI dashboard shows both the relay address and the pairing code. Pairing codes expire after 15 minutes and can be used only once.
