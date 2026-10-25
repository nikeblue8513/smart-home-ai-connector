import asyncio
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from connector_protocol import minimal_snapshot, relay_address, service_data, command_expiry
from connector_relay import RelayClient, PairingRequired, RelayError
from connector_commands import CommandProcessor
from connector_utils import read_json, write_private_json
from connector import retry_delay


class Response:
    def __init__(self, status=200, body=None, content_type="application/json"):
        self.status, self.content_type = status, content_type
        self.body = json.dumps(body or {}).encode()
        self.content = self
    async def __aenter__(self):
        await asyncio.sleep(0)
        return self
    async def __aexit__(self, *args):
        pass
    async def read(self, size):
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk


class Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)
    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def command(**extra):
    now = datetime.now(timezone.utc)
    return {"id": str(uuid.uuid4()), "domain": "light", "service": "turn_on", "entity_id": "light.lamp", "parameters": {"brightnessPct": 42}, "created_at": now.isoformat(), "expires_at": (now + timedelta(seconds=60)).isoformat(), **extra}


class ProtocolTests(unittest.TestCase):
    def test_https_base_url(self):
        self.assertEqual(relay_address(" https://EXAMPLE.com:443/relay/ "), "https://example.com/relay")
        for value in ["http://example.com", "https://user:pass@example.com", "https://example.com?token=secret", "https://example.com#fragment", "https://example.com:8443", "https://example.com/%2e%2e/path", "https://example.com/../path"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                relay_address(value)

    def test_expiry_requires_short_aware_lifetime(self):
        command_expiry(command())
        for update in [{"expires_at": "2020-01-01T00:00:00Z"}, {"expires_at": "2030-01-01T00:00:00Z"}, {"created_at": "not a date"}, {"expires_at": datetime.now().isoformat()}]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                command_expiry(command(**update))

    def test_only_exact_light_targets_and_parameters(self):
        self.assertEqual(service_data("light", "turn_on", "light.lamp", {"brightnessPct": 42, "hue": 180}), {"entity_id": "light.lamp", "brightness_pct": 42, "hs_color": [180, 100]})
        for entity in ["light.lamp,light.other", "light.*", "light.", "light.lamp/path", "switch.lamp"]:
            with self.subTest(entity=entity), self.assertRaises(ValueError):
                service_data("light", "turn_on", entity, {})
        for value in [True, "20", float("nan"), float("inf"), 0, 101]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                service_data("light", "turn_on", "light.lamp", {"brightnessPct": value})
        for params in [[], {"entity_id": "light.other"}]:
            with self.assertRaises(ValueError):
                service_data("light", "turn_on", "light.lamp", params)

    def test_lock_control_requires_separate_local_setting(self):
        with self.assertRaises(ValueError):
            service_data("lock", "unlock", "lock.front", {})
        self.assertEqual(service_data("lock", "lock", "lock.front", {}, True), {"entity_id": "lock.front"})

    def test_snapshot_minimizes_data_and_registry(self):
        states = [
            {"entity_id": "light.lamp", "state": "on", "attributes": {"friendly_name": "Lamp", "brightness": 200, "entity_picture": "https://private/image?token=x", "nested": {"token": "secret"}, "access_token": "secret"}},
            {"entity_id": "camera.entry", "state": "idle", "attributes": {"access_token": "secret", "stream_source": "rtsp://private", "friendly_name": "Entry"}},
            {"entity_id": "sensor.battery", "state": "50", "attributes": {"device_class": "battery", "unit_of_measurement": "%"}},
            {"entity_id": "sensor.address", "state": "private address", "attributes": {}},
            {"entity_id": "sensor.fake_temperature", "state": "private address", "attributes": {"device_class": "temperature"}},
            {"entity_id": "person.owner", "state": "home", "attributes": {"latitude": 10}},
            {"entity_id": "calendar.private", "state": "on", "attributes": {"message": "private appointment"}},
            {"entity_id": "light.hidden", "state": "on", "attributes": {}},
        ]
        registries = {"entities": [{"entity_id": "light.lamp", "device_id": "dev1", "area_id": "living", "unique_id": "private-mac"}, {"entity_id": "light.hidden", "hidden_by": "user"}], "devices": [{"id": "dev1", "name": "Lamp", "manufacturer": "Example", "connections": [["mac", "private-mac"]]}, {"id": "private-phone", "name": "Phone"}], "areas": [{"area_id": "living", "name": "Living room", "picture": "secret"}, {"area_id": "private", "name": "Excluded"}]}
        result = minimal_snapshot({"location_name": "Home", "latitude": 10, "longitude": 20}, states, registries, "installation", "0.5.0")
        self.assertEqual([s["entity_id"] for s in result["states"]], ["light.lamp", "camera.entry", "sensor.battery"])
        serialized = json.dumps(result)
        for secret in ["private", "secret", "latitude", "longitude", "unique_id", "connections", "stream_source", "entity_picture", "calendar."]:
            self.assertNotIn(secret, serialized)
        self.assertEqual(len(result["areas"]), 1)
        self.assertEqual(len(result["devices"]), 1)

    def test_private_files_and_retry_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            write_private_json(path, {"access_token": "secret"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
        self.assertTrue(15 <= retry_delay(15, 0) <= 18)
        self.assertTrue(3600 <= retry_delay(3600, 0) <= 3605)
        self.assertTrue(300 <= retry_delay(15, 99) <= 303)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "credentials.json"
    def client(self, session, url="https://relay.example/bridge", code="PAIR-ONE"):
        return RelayClient(session, url, code, self.path, "install", "0.5.0")

    async def test_pairing_is_serialized_and_redirects_disabled(self):
        session = Session([Response(body={"access_token": "a" * 48})])
        relay = self.client(session)
        tokens = await asyncio.gather(relay._access_token("Home"), relay._access_token("Home"))
        self.assertEqual(tokens, ["a" * 48] * 2)
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(session.calls[0][2]["allow_redirects"])

    async def test_credentials_are_not_reused_for_new_url_or_code(self):
        for url, code in [("https://other.example", "PAIR-ONE"), ("https://relay.example/bridge", "PAIR-TWO")]:
            session = Session([Response(body={"access_token": "a" * 48}), Response(body={"access_token": "b" * 48})])
            first = self.client(session)
            write_private_json(self.path, {})
            await first._access_token("Home")
            second = self.client(session, url, code)
            self.assertEqual(await second._access_token("Home"), "b" * 48)
            self.assertEqual(len(session.calls), 2)
            self.assertNotIn("headers", session.calls[1][2])

    async def test_legacy_unbound_credentials_are_never_sent(self):
        write_private_json(self.path, {"access_token": "old-secret"})
        session = Session([Response(body={"access_token": "a" * 48})])
        await self.client(session)._access_token("Home")
        self.assertNotIn("old-secret", json.dumps(session.calls))

    async def test_revocation_clears_token_and_stops_repair_loop(self):
        session = Session([Response(body={"access_token": "a" * 48}), Response(status=401)])
        relay = self.client(session)
        with self.assertRaises(PairingRequired):
            await relay.commands("Home")
        self.assertNotIn("access_token", read_json(self.path, {}))
        with self.assertRaises(PairingRequired):
            await self.client(session).commands("Home")
        self.assertEqual(len(session.calls), 2)

    async def test_invalid_pairing_requires_new_code(self):
        session = Session([Response(status=403)])
        relay = self.client(session)
        for _ in range(2):
            with self.assertRaises(PairingRequired):
                await relay.commands("Home")
        self.assertEqual(len(session.calls), 1)

    async def test_transient_pairing_is_rate_limited(self):
        session = Session([Response(status=503)])
        relay = self.client(session)
        with self.assertRaises(RelayError):
            await relay.commands("Home")
        with self.assertRaises(PairingRequired):
            await relay.commands("Home")
        self.assertEqual(len(session.calls), 1)

    async def test_relay_redirect_does_not_send_credentials(self):
        session = Session([Response(body={"access_token": "a" * 48}), Response(status=302)])
        with self.assertRaises(RelayError):
            await self.client(session).commands("Home")
        self.assertFalse(session.calls[-1][2]["allow_redirects"])

    async def test_invalid_response_format_and_large_batch(self):
        for response in [Response(content_type="text/html"), Response(body={"commands": [None]}), Response(body={"commands": [{}] * 21}), Response(body={"commands": "no"})]:
            write_private_json(self.path, {})
            session = Session([Response(body={"access_token": "a" * 48}), response])
            with self.assertRaises(RuntimeError):
                await self.client(session).commands("Home")


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "receipts.json"
        self.ha, self.relay = AsyncMock(), AsyncMock()

    async def test_ack_failure_retries_receipt_without_repeating_action_after_restart(self):
        item = command()
        self.relay.command_result.side_effect = [RuntimeError("offline"), None]
        with self.assertRaises(RuntimeError):
            await CommandProcessor(self.path, True).process(item, self.ha, self.relay, "Home")
        await CommandProcessor(self.path, True).process(item, self.ha, self.relay, "Home")
        self.ha.call_service.assert_awaited_once()
        self.assertEqual(self.relay.command_result.await_args.args[2], True)

    async def test_pending_receipt_does_not_repeat_unknown_action(self):
        item = command()
        self.ha.call_service.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await CommandProcessor(self.path, True).process(item, self.ha, self.relay, "Home")
        await CommandProcessor(self.path, True).process(item, self.ha, self.relay, "Home")
        self.ha.call_service.assert_awaited_once()
        self.assertFalse(self.relay.command_result.await_args.args[2])

    async def test_reused_id_with_new_contents_is_rejected(self):
        item = command()
        processor = CommandProcessor(self.path, True)
        await processor.process(item, self.ha, self.relay, "Home")
        await processor.process({**item, "entity_id": "light.other"}, self.ha, self.relay, "Home")
        self.ha.call_service.assert_awaited_once()
        self.assertFalse(self.relay.command_result.await_args.args[2])

    async def test_expired_commands_and_disabled_control_never_reach_ha(self):
        await CommandProcessor(self.path).process(command(), self.ha, self.relay, "Home")
        await CommandProcessor(self.path, True).process(command(expires_at="2020-01-01T00:00:00Z"), self.ha, self.relay, "Home")
        self.ha.call_service.assert_not_awaited()

    async def test_corrupt_receipt_store_stops_control(self):
        self.path.write_text("not json")
        with self.assertRaises(ValueError):
            CommandProcessor(self.path, True)

    async def test_ha_error_is_recorded_without_secrets(self):
        self.ha.call_service.side_effect = RuntimeError("secret-address-and-token")
        item = command()
        await CommandProcessor(self.path, True).process(item, self.ha, self.relay, "Home")
        self.assertNotIn("secret-address-and-token", self.path.read_text())
        self.assertFalse(self.relay.command_result.await_args.args[2])


if __name__ == "__main__":
    unittest.main()
