import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("connector_utils.py")
SPEC = importlib.util.spec_from_file_location("connector_utils", MODULE_PATH)
connector_utils = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(connector_utils)


class ConnectorTests(unittest.TestCase):
    def test_sanitize_removes_nested_credentials(self):
        value = {
            "entity_id": "camera.entry",
            "attributes": {
                "access_token": "private",
                "friendly_name": "Entry",
                "nested": {"api_key": "private", "ok": True},
            },
        }
        self.assertEqual(
            connector_utils.sanitize(value),
            {
                "entity_id": "camera.entry",
                "attributes": {"friendly_name": "Entry", "nested": {"ok": True}},
            },
        )

    def test_installation_id_is_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            first = connector_utils.stable_installation_id(path)
            second = connector_utils.stable_installation_id(path)
            self.assertEqual(first, second)
            self.assertEqual(len(first), 32)

    def test_websocket_url_uses_home_assistant_origin(self):
        self.assertEqual(
            connector_utils.websocket_url("https://home.example/api"),
            "wss://home.example/websocket",
        )


if __name__ == "__main__":
    unittest.main()
