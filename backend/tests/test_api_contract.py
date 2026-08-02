from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402


class ApiContractCharacterizationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_listed_routes_are_fastapi_owned_json_surfaces(self):
        # Given: the isolated candidate application.
        expected_routes = {
            ("GET", "/api/building-footprint"),
            ("GET", "/api/building-height"),
            ("GET", "/api/road-width"),
            ("GET", "/api/canyon-width"),
            ("GET", "/api/weather"),
            ("POST", "/api/evaluate"),
            ("POST", "/api/building-footprint/cache"),
        }

        # When: route ownership is read from FastAPI's executable router.
        actual_routes = {
            (method, route.path)
            for route in main.app.routes
            for method in (getattr(route, "methods", None) or set())
        }

        # Then: every Todo 3 surface is backend-owned.
        self.assertTrue(expected_routes.issubset(actual_routes))

    def test_non_authoritative_weather_is_typed_estimated_json(self):
        # Given: display-only Open-Meteo weather and no KMA profile.
        weather = {
            "available": True,
            "authoritative": False,
            "authority_source": None,
            "source": "open_meteo_surface",
            "source_chain": ["open_meteo_surface"],
            "profile_source": "surface_only",
            "stale_cache": False,
        }
        with (
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather)),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
        ):
            # When: the weather route is requested.
            response = self.client.get("/api/weather?lat=37.5665&lon=126.9780")

        # Then: it remains JSON and is never promoted to authoritative evidence.
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertEqual(response.json()["weather_evidence"]["status"], "estimated")
        self.assertFalse(response.json()["weather_evidence"]["authoritative"])

    def test_runtime_config_does_not_publish_cache_write_token(self):
        # Given: a synthetic server-only cache token in the process environment.
        with patch.dict(main.os.environ, {"UAV_CACHE_WRITE_TOKEN": "synthetic-server-token"}, clear=True):
            # When: browser runtime configuration is rendered.
            response = self.client.get("/runtime-config.js")

        # Then: the token name and value are absent from browser-visible output.
        self.assertNotIn("UAV_CACHE_WRITE_TOKEN", response.text)
        self.assertNotIn("synthetic-server-token", response.text)

    def test_read_surfaces_echo_selection_and_expose_typed_top_level_status(self):
        # Given: typed unavailable evidence from each authoritative backend seam.
        unavailable = {
            "available": False,
            "official_available": False,
            "source_chain": ["contract_fixture"],
            "reason": "fixture_unavailable",
        }
        weather = {
            "available": True,
            "authoritative": False,
            "source": "open_meteo_surface",
            "source_chain": ["open_meteo_surface"],
            "stale_cache": False,
        }
        selection_id = "00000000-0000-4000-8000-000000000003"
        with (
            patch.object(main, "_lookup_building_selection", AsyncMock(return_value=unavailable)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=unavailable)),
            patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=unavailable)),
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather)),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
        ):
            # When: every read surface receives the same browser selection ID.
            responses = [
                self.client.get(path, params={"lat": 37.5665, "lon": 126.9780, "selection_id": selection_id})
                for path in (
                    "/api/building-footprint",
                    "/api/building-height",
                    "/api/road-width",
                    "/api/canyon-width",
                    "/api/weather",
                )
            ]

        # Then: each response is typed JSON and echoes without creating correlation.
        for response in responses:
            payload = response.json()
            self.assertTrue(response.headers["content-type"].startswith("application/json"))
            self.assertIsInstance(payload["status"], str)
            self.assertIsInstance(payload["official_available"], bool)
            self.assertEqual(payload["selection_id"], selection_id)
            self.assertNotIn("correlation_id", payload)

    def test_evaluate_rejects_client_supplied_correlation_id(self):
        # Given: a client attempts to forge the backend-only snapshot ID.
        request = {
            "latitude": 37.5665,
            "longitude": 126.9780,
            "selection_id": "00000000-0000-4000-8000-000000000003",
            "correlation_id": "client-forged-correlation",
        }

        # When: the request crosses the evaluation boundary.
        response = self.client.post("/api/evaluate", json=request)

        # Then: it is rejected as typed JSON before upstream evaluation.
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["status"], "unavailable")
        self.assertEqual(response.json()["reason"], "client_correlation_id_rejected")


class CacheWriteSecurityContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.valid_payload = {
            "lat": 37.5662952,
            "lon": 126.9779451,
            "geometry": [
                [126.9779, 37.5662],
                [126.9780, 37.5662],
                [126.9780, 37.5663],
                [126.9779, 37.5663],
                [126.9779, 37.5662],
            ],
            "properties": {"source_chain": ["vworld_wfs"]},
            "source": "vworld_wfs",
        }

    def test_unauthenticated_cache_write_is_rejected_as_typed_json(self):
        # Given: a configured synthetic server token and no Authorization header.
        with patch.dict(main.os.environ, {"UAV_CACHE_WRITE_TOKEN": "synthetic-cache-token"}, clear=False):
            # When: a public client submits an otherwise valid cache write.
            response = self.client.post("/api/building-footprint/cache", json=self.valid_payload)

        # Then: Render rejects it with a stable JSON reason and audit-safe receipt.
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertEqual(response.json()["status"], "unavailable")
        self.assertEqual(response.json()["reason"], "cache_write_auth_required")
        self.assertEqual(response.json()["audit_receipt"]["outcome"], "rejected")

    def test_unauthenticated_malformed_body_is_rejected_before_validation(self):
        # Given: a configured synthetic server token and malformed public input.
        with patch.dict(main.os.environ, {"UAV_CACHE_WRITE_TOKEN": "synthetic-cache-token"}, clear=False):
            # When: the unauthenticated body is not JSON.
            response = self.client.post(
                "/api/building-footprint/cache",
                content="not-json",
                headers={"Content-Type": "text/plain"},
            )

        # Then: the auth boundary rejects it without exposing parser details.
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["reason"], "cache_write_auth_required")

    def test_authenticated_cache_write_returns_audit_safe_receipt(self):
        # Given: a matching synthetic token and an in-memory cache result.
        cached = {
            "available": True,
            "official_footprint_available": True,
            "source_chain": ["footprint_cache", "vworld_wfs"],
        }
        with (
            patch.dict(main.os.environ, {"UAV_CACHE_WRITE_TOKEN": "synthetic-cache-token"}, clear=False),
            patch.object(main, "cache_building_footprint", return_value=cached),
        ):
            # When: the internal caller sends the dedicated bearer token.
            response = self.client.post(
                "/api/building-footprint/cache",
                json=self.valid_payload,
                headers={"Authorization": "Bearer synthetic-cache-token"},
            )

        # Then: the response is JSON, accepted, and never contains the token.
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "accepted")
        self.assertEqual(payload["reason"], "cache_write_recorded")
        self.assertTrue(payload["official_available"])
        self.assertEqual(payload["audit_receipt"]["geometry_points"], 5)
        self.assertNotIn("synthetic-cache-token", response.text)

    def test_authenticated_nested_credential_shape_is_rejected_without_a_write(self):
        # Given: an authenticated payload containing nested credential-shaped data.
        payload = dict(self.valid_payload)
        payload["properties"] = {"metadata": {"api_key": "synthetic-browser-value"}}
        with (
            patch.dict(main.os.environ, {"UAV_CACHE_WRITE_TOKEN": "synthetic-cache-token"}, clear=False),
            patch.object(main, "cache_building_footprint") as cache_write,
        ):
            # When: the malformed payload crosses the cache boundary.
            response = self.client.post(
                "/api/building-footprint/cache",
                json=payload,
                headers={"Authorization": "Bearer synthetic-cache-token"},
            )

        # Then: it is rejected before any cache mutation and no value is echoed.
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["reason"], "credential_shaped_data_rejected")
        self.assertNotIn("synthetic-browser-value", response.text)
        cache_write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
