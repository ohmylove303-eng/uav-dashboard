from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402


class OfficialGisReadinessTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_server_data_key_lookup_supports_legacy_render_key_without_browser_prefixed_keys(self):
        with patch.dict(
            main.os.environ,
            {
                "VITE_VWORLD_API_KEY": "browser-key",
                "NEXT_PUBLIC_VWORLD_API_KEY": "browser-key",
                "VWORLD_API_KEY": "legacy-render-server-key",
            },
            clear=True,
        ):
            self.assertEqual(main._vworld_api_key(), "legacy-render-server-key")

    def test_readiness_reports_missing_server_prerequisites_without_secret_values(self):
        with (
            patch.object(main, "OFFICIAL_GIS_BRIDGE_URL", ""),
            patch.object(main, "OFFICIAL_GIS_BRIDGE_TOKEN", ""),
            patch.object(main, "_vworld_api_key", return_value=None),
        ):
            response = self.client.get("/api/official-gis/readiness")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "hold")
        self.assertEqual(
            payload["missing_prerequisites"],
            [
                "vworld_server_data_api_key",
                "official_gis_bridge_url",
                "official_gis_bridge_token",
            ],
        )
        self.assertEqual(payload["facade_gap_policy"], "verified_official_geometry_only")
        self.assertNotIn("browser-key", str(payload))

    def test_readiness_reports_ready_only_for_server_side_bridge_configuration(self):
        with (
            patch.object(main, "OFFICIAL_GIS_BRIDGE_URL", "https://bridge.example.test/api/canyon-width"),
            patch.object(main, "OFFICIAL_GIS_BRIDGE_TOKEN", "server-only-token"),
            patch.object(main, "_vworld_api_key", return_value="server-only-vworld-key"),
        ):
            response = self.client.get("/api/official-gis/readiness")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["missing_prerequisites"], [])
        self.assertNotIn("server-only", str(payload))

    def test_runtime_config_does_not_publish_server_named_vworld_key(self):
        with patch.dict(
            main.os.environ,
            {
                "VITE_VWORLD_API_KEY": "browser-key",
                "VWORLD_API_KEY": "server-named-key",
                "VWORLD_DATA_API_KEY": "server-data-key",
            },
            clear=True,
        ):
            response = self.client.get("/runtime-config.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn("browser-key", response.text)
        self.assertNotIn("server-named-key", response.text)
        self.assertNotIn("server-data-key", response.text)


if __name__ == "__main__":
    unittest.main()
