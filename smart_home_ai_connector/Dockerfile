FROM ghcr.io/home-assistant/base:latest

ARG BUILD_VERSION
ARG BUILD_ARCH

LABEL \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

RUN apk add --no-cache python3 py3-aiohttp

COPY connector.py /opt/smart-home-ai/connector.py
COPY connector_utils.py connector_protocol.py connector_relay.py connector_commands.py connector_live.py connector_camera.py connector_catalog.py /opt/smart-home-ai/
COPY run.sh /run.sh

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
