# Smart Home AI Connector

An installable Home Assistant app that runs locally beside Home Assistant on your Home Assistant OS device.

## Version 0.1.0

This first release performs local, read-only discovery of Home Assistant areas, devices, entities, and states. It starts automatically after Home Assistant, uses Home Assistant's Supervisor-provided internal API credential at runtime, and never asks for a long-lived Home Assistant token.

Cloud pairing is intentionally disabled by default. With the default blank relay settings, no home snapshot leaves the device.

## Install

Add this URL as a custom repository in Home Assistant:

`https://github.com/nikeblue8513/smart-home-ai-connector`

Then install **Smart Home AI Connector** from the Home Assistant App store, leave the relay fields blank, enable **Start on boot** and **Watchdog**, and start the app. Its log reports the number of locally discovered areas, devices, entities, and states.

## Privacy

This repository contains generic software only. It does not contain a Home Assistant address, IP address, device list, room names, access token, pairing code, or user data. Runtime credentials remain inside the Home Assistant app container and are never written into this repository.

See [SECURITY.md](SECURITY.md) for the connector's security boundaries.

