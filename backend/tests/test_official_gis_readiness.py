from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

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
            patch.object(main, "molit_building_hub_key_configured", return_value=False),
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
                "molit_building_hub_service_key",
            ],
        )
        self.assertEqual(payload["facade_gap_policy"], "verified_official_geometry_only")
        self.assertEqual(payload["building_hub_key_resolution"], "semantic_aliases_v2")
        self.assertNotIn("browser-key", str(payload))

    def test_readiness_reports_configuration_without_claiming_provider_authorization(self):
        with (
            patch.object(main, "OFFICIAL_GIS_BRIDGE_URL", "https://bridge.example.test/api/canyon-width"),
            patch.object(main, "OFFICIAL_GIS_BRIDGE_TOKEN", "server-only-token"),
            patch.object(main, "_vworld_api_key", return_value="server-only-vworld-key"),
            patch.object(main, "molit_building_hub_key_configured", return_value=True),
        ):
            response = self.client.get("/api/official-gis/readiness")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "configured")
        self.assertEqual(payload["configuration_status"], "configured")
        self.assertEqual(payload["provider_authorization_status"], "not_checked")
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

    def test_health_reports_the_running_render_revision_without_secrets(self):
        with patch.dict(
            main.os.environ,
            {"RENDER_GIT_COMMIT": "c6753982221de785b1f56d04a425e27fb27795ab"},
            clear=False,
        ):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["deployment_revision"], "c6753982221d")

    def test_weather_credentials_are_reported_by_capability_without_values(self):
        with patch.dict(
            main.os.environ,
            {
                "KMA_API_KEY": "legacy-kma-key",
                "KMA_SURFACE_API_KEY": "surface-kma-key",
                "KMA_UPPER_AIR_API_KEY": "upper-air-kma-key",
            },
            clear=False,
        ):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["kma_surface_configured"])
        self.assertTrue(payload["kma_upper_air_configured"])
        self.assertTrue(payload["kma_wind_profiler_configured"])
        self.assertNotIn("surface-kma-key", str(payload))
        self.assertNotIn("upper-air-kma-key", str(payload))

    def test_surface_kma_key_accepts_copied_auth_key_query_value_without_leaking_it(self):
        with patch.dict(
            main.os.environ,
            {"KMA_SURFACE_API_KEY": "authKey=encoded%2Fsurface%2Bkey"},
            clear=True,
        ):
            self.assertEqual(
                main._kma_api_key_for("surface"),
                "encoded/surface+key",
            )

    def test_kma_status_requires_surface_and_low_altitude_profile_for_authoritative_availability(self):
        with (
            patch.object(
                main,
                "fetch_weather_safe",
                AsyncMock(return_value={
                    "available": False,
                    "authoritative": False,
                    "reason": "surface_weather_auth_denied",
                }),
            ),
            patch.object(
                main,
                "fetch_kma_upper_air_profile_safe",
                AsyncMock(return_value={
                    "station_id": 47102,
                    "station_name": "upper-air-station",
                    "observed_at_utc": "202608160000",
                    "layers": [{"height_m": 200}],
                    "stale_cache": False,
                }),
            ),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
        ):
            response = self.client.get("/api/kma/status")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative_flight_weather_available"])
        self.assertEqual(payload["reason"], "surface_weather_auth_denied")
        self.assertFalse(payload["surface"]["available"])
        self.assertTrue(payload["upper_air"]["available"])
        self.assertFalse(payload["wind_profiler"]["available"])
        self.assertNotIn("kma-key", str(payload))


if __name__ == "__main__":
    unittest.main()
