from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402
from tests.task7_evaluation_fixtures import (  # noqa: E402
    SELECTION_ID,
    authoritative_weather,
    official_canyon,
    server_building,
)


class FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("json payload missing")
        return self._json_data


class FakeAsyncClient:
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        return self._handler(url, params or {})


def open_meteo_display_payload() -> dict:
    return {
        "current": {
            "temperature_2m": 18.0,
            "relative_humidity_2m": 40,
            "dew_point_2m": 10.0,
            "weather_code": 0,
            "cloud_cover": 20,
            "wind_speed_10m": 15.12,
            "wind_direction_10m": 120,
            "wind_gusts_10m": 21.96,
            "visibility": 9500,
            "precipitation_probability": 5,
        },
        "daily": {
            "sunrise": ["2026-08-08T05:40"],
            "sunset": ["2026-08-08T19:33"],
        },
    }


def kma_surface_text(observed_at_kst: datetime) -> str:
    return "\n".join(
        [
            "# header",
            "# TM STN WD WS GST_WD GST_WS GST_TM PA PS TA TD HM RN CA_TOT VS WW",
            f"{observed_at_kst.strftime('%Y%m%d%H%M')} 108 9 5.0 10 7.8 1230 1008.4 1014.2 26.4 21.0 72 0.0 8 1500 맑음",
        ]
    )


class WeatherResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main.WEATHER_CACHE.clear()
        main.WEATHER_LAST_GOOD_CACHE.clear()

    async def test_fetch_weather_safe_uses_fresh_kma_cache_during_timeout(self):
        cache_key = main._cache_key_for_latlon(37.5665, 126.9780)
        main._cache_set(
            main.WEATHER_CACHE,
            cache_key,
            authoritative_weather(latitude=37.5665, longitude=126.9780),
        )

        with patch.object(main, "fetch_weather", AsyncMock(side_effect=TimeoutError("boom"))):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertTrue(payload["available"])
        self.assertTrue(payload["authoritative"])
        self.assertEqual(payload["authority_source"], "kma_surface_cache")
        self.assertFalse(payload["stale_cache"])

    async def test_fetch_weather_safe_returns_structured_unavailable_when_cache_is_expired(self):
        cache_key = main._cache_key_for_latlon(37.5665, 126.9780)
        main.WEATHER_CACHE[cache_key] = {
            "ts": 0.0,
            "value": authoritative_weather(latitude=37.5665, longitude=126.9780),
        }

        with patch.object(main, "fetch_weather", AsyncMock(side_effect=TimeoutError("boom"))):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_timeout")
        self.assertEqual(payload["source_chain"], ["weather_unavailable", "surface_weather_timeout"])

    async def test_fetch_weather_safe_returns_authoritative_kma_surface_receipt_bound_to_request(self):
        selection_id = "00000000-0000-4000-8000-000000000007"
        open_meteo_payload = open_meteo_display_payload()
        open_meteo_payload["daily"] = {
            "sunrise": ["2026-08-08T05:42"],
            "sunset": ["2026-08-08T19:31"],
        }
        kma_text = kma_surface_text(datetime.now(main.KST) - timedelta(minutes=5))

        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=200, text=kma_text)
            if "open-meteo.com" in url:
                return FakeResponse(status_code=200, json_data=open_meteo_payload)
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780, selection_id=selection_id)

        self.assertTrue(payload["available"])
        self.assertTrue(payload["authoritative"])
        self.assertEqual(payload["authority_source"], "kma_surface_observation")
        self.assertEqual(payload["source"], "kma_surface_observation")
        self.assertEqual(payload["source_chain"], ["kma_surface_observation"])
        self.assertEqual(payload["sunrise"], "05:42")
        self.assertEqual(payload["sunset"], "19:31")
        self.assertEqual(payload["receipt"]["selection_id"], selection_id)
        self.assertEqual(payload["receipt"]["latitude"], 37.5665)
        self.assertEqual(payload["receipt"]["longitude"], 126.9780)
        self.assertEqual(payload["receipt"]["source"], "kma_surface_observation")
        self.assertEqual(payload["receipt"]["source_chain"], ["kma_surface_observation"])
        self.assertEqual(payload["receipt"]["values"]["wind_speed"], 5.0)
        self.assertEqual(payload["receipt"]["values"]["gust_speed"], 7.8)
        self.assertEqual(payload["receipt"]["values"]["visibility"], 15.0)

    async def test_fetch_weather_safe_returns_open_meteo_display_when_kma_surface_is_unavailable(self):
        open_meteo_payload = open_meteo_display_payload()

        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=503, text="SERVICE DOWN")
            if "open-meteo.com" in url:
                return FakeResponse(status_code=200, json_data=open_meteo_payload)
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_upstream_unavailable")
        self.assertEqual(payload["source"], "weather_unavailable")
        self.assertEqual(payload["source_chain"], ["weather_unavailable", "surface_weather_upstream_unavailable"])
        self.assertEqual(payload["temperature"], 18.0)
        self.assertEqual(payload["sunrise"], "05:40")
        self.assertIsNone(payload["authority_source"])

    async def test_fetch_weather_safe_returns_typed_reason_for_surface_http_failure(self):
        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=502, text="bad gateway")
            if "open-meteo.com" in url:
                raise main.httpx.ConnectError("display offline")
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_upstream_unavailable")

    async def test_fetch_weather_safe_classifies_surface_auth_failure(self):
        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=403, text="FORBIDDEN")
            if "open-meteo.com" in url:
                raise main.httpx.ConnectError("display offline")
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_auth_denied")

    async def test_fetch_weather_safe_classifies_200_surface_auth_body(self):
        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=200, text="INVALID AUTH KEY")
            if "open-meteo.com" in url:
                raise main.httpx.ConnectError("display offline")
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_auth_denied")

    async def test_valid_surface_row_may_contain_auth_marker_text(self):
        kma_text = kma_surface_text(datetime.now(main.KST) - timedelta(minutes=5)).replace(
            "맑음",
            "FORBIDDEN",
        )

        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=200, text=kma_text)
            if "open-meteo.com" in url:
                return FakeResponse(status_code=200, json_data=open_meteo_display_payload())
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertTrue(payload["available"])
        self.assertTrue(payload["authoritative"])

    def test_surface_http_status_takes_precedence_over_conflicting_body_marker(self):
        self.assertEqual(
            main._surface_weather_http_reason(429, "AUTHENTICATION"),
            "surface_weather_quota_exceeded",
        )
        self.assertEqual(
            main._surface_weather_http_reason(503, "FORBIDDEN"),
            "surface_weather_upstream_unavailable",
        )

    async def test_fetch_weather_safe_returns_typed_reason_for_surface_parse_failure(self):
        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=200, text="# header only\n")
            if "open-meteo.com" in url:
                raise main.httpx.ConnectError("display offline")
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_parse_error")

    async def test_fetch_weather_safe_returns_typed_reason_for_malformed_surface_timestamp(self):
        malformed_kma_text = "\n".join(
            [
                "# header",
                "# TM STN WD WS GST_WD GST_WS GST_TM PA PS TA TD HM RN CA_TOT VS WW",
                "not-a-timestamp 108 9 5.0 10 7.8 1230 1008.4 1014.2 26.4 21.0 72 0.0 8 1500 맑음",
            ]
        )

        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=200, text=malformed_kma_text)
            if "open-meteo.com" in url:
                return FakeResponse(status_code=200, json_data=open_meteo_display_payload())
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_parse_error")

    async def test_fetch_weather_safe_returns_typed_reason_for_stale_surface_timestamp(self):
        stale_kma_text = kma_surface_text(
            datetime.now(main.KST) - main.SURFACE_WEATHER_RECEIPT_TTL - timedelta(minutes=5)
        )

        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=200, text=stale_kma_text)
            if "open-meteo.com" in url:
                return FakeResponse(status_code=200, json_data=open_meteo_display_payload())
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_stale")
        self.assertEqual(payload["temperature"], 18.0)

    async def test_fetch_weather_safe_returns_typed_reason_for_future_surface_timestamp(self):
        future_kma_text = kma_surface_text(datetime.now(main.KST) + timedelta(hours=2))

        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                return FakeResponse(status_code=200, text=future_kma_text)
            if "open-meteo.com" in url:
                return FakeResponse(status_code=200, json_data=open_meteo_display_payload())
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_freshness_invalid")

    async def test_fetch_weather_safe_returns_typed_reason_for_surface_timeout_failure(self):
        def handler(url: str, params: dict):
            if "kma_sfctm2.php" in url:
                raise main.httpx.TimeoutException("surface timeout")
            if "open-meteo.com" in url:
                raise main.httpx.ConnectError("display offline")
            raise AssertionError(f"unexpected url: {url}")

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(handler)),
        ):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_timeout")

    def test_weather_route_marks_open_meteo_only_weather_as_non_authoritative(self):
        client = TestClient(main.app)
        weather_payload = {
            "wind_speed": 4.2,
            "gust_speed": 6.1,
            "wind_direction": 120,
            "visibility": 9.5,
            "precipitation_prob": 5,
            "weather_code": 0,
            "temperature": 18,
            "dew_point": 10,
            "humidity": 40,
            "cloud_cover": 20,
            "sunrise": "06:00",
            "sunset": "18:00",
            "source": "open_meteo_surface",
            "source_chain": ["open_meteo_surface"],
            "profile_source": "surface_only",
            "stale_cache": False,
            "available": True,
            "authoritative": False,
            "authority_source": "open_meteo_surface",
        }

        with (
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather_payload)),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
        ):
            response = client.get("/api/weather", params={"lat": 37.5665, "lon": 126.9780})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["weather_evidence"]["authoritative"])
        self.assertEqual(payload["weather_evidence"]["status"], "estimated")
        self.assertEqual(payload["weather_evidence"]["source_chain"], ["open_meteo_surface"])

    def test_evaluate_route_uses_real_kma_adapter_for_normal_and_hold_decisions(self):
        client = TestClient(main.app)
        request_payload = {
            "latitude": 37.5665,
            "longitude": 126.9780,
            "selection_id": SELECTION_ID,
            "building_height": 999.0,
            "street_width": 1.0,
            "building_source": "browser_estimate",
            "building_evidence": {"official_available": True, "source": "browser_estimate"},
            "wind_alignment": "직각",
            "mission_altitude": 30,
            "no_fly_zone": False,
            "crowd_area": False,
            "gps_locked": 12,
            "glonass_locked": 6,
            "drone_model": main.DroneModel.MAVIC_3.value,
        }
        cache_key = main._cache_key_for_latlon(37.5665, 126.9780)
        fresh_kma_text = kma_surface_text(datetime.now(main.KST) - timedelta(minutes=5))

        def make_handler(kma_text: str):
            def handler(url: str, params: dict):
                if "kma_sfctm2.php" in url:
                    return FakeResponse(status_code=200, text=kma_text)
                if "open-meteo.com" in url:
                    return FakeResponse(status_code=200, json_data=open_meteo_display_payload())
                raise AssertionError(f"unexpected url: {url}")
            return handler

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(make_handler(fresh_kma_text))),
            patch.object(main, "_lookup_building_selection", AsyncMock(return_value=server_building())),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
            patch.object(
                main,
                "fetch_road_width_evidence",
                AsyncMock(
                    return_value={
                        "available": True,
                        "official_available": True,
                        "width_m": 49.7,
                        "source": "official_road_right_of_way",
                        "source_chain": ["vworld_wfs", "official_road_right_of_way"],
                    }
                ),
            ),
            patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=official_canyon(SELECTION_ID, SELECTION_ID))),
        ):
            fresh_response = client.post("/api/evaluate", json=request_payload)

        self.assertEqual(fresh_response.status_code, 200)
        fresh_payload = fresh_response.json()
        self.assertIn(fresh_payload["final_judgment"], {"GO", "RESTRICT", "NO_GO"})
        self.assertNotEqual(fresh_payload["final_judgment"], "HOLD")
        self.assertIsInstance(fresh_payload["urban_factors"]["Fcanyon"], float)
        self.assertIsInstance(fresh_payload["ews"], float)
        self.assertTrue(fresh_payload["correlation_id"])
        self.assertTrue(fresh_payload["weather_evidence"]["authoritative"])
        self.assertEqual(fresh_payload["weather_evidence"]["receipt"]["source"], "kma_surface_observation")

        stale_observed_at = datetime.now(timezone.utc) - main.SURFACE_WEATHER_RECEIPT_TTL - timedelta(minutes=5)
        stale_expires_at = stale_observed_at + main.SURFACE_WEATHER_RECEIPT_TTL
        cached_weather = dict(main.WEATHER_CACHE[cache_key]["value"])
        cached_weather["observed_at_utc"] = stale_observed_at.isoformat()
        cached_weather["expires_at_utc"] = stale_expires_at.isoformat()
        cached_receipt = dict(cached_weather.get("receipt") or {})
        cached_receipt["observed_at_utc"] = stale_observed_at.isoformat()
        cached_receipt["expires_at_utc"] = stale_expires_at.isoformat()
        cached_weather["receipt"] = cached_receipt
        main.WEATHER_CACHE[cache_key]["value"] = cached_weather

        with (
            patch.object(main, "KMA_API_KEY", "test-kma-key"),
            patch.object(main.httpx, "AsyncClient", side_effect=lambda *args, **kwargs: FakeAsyncClient(make_handler(fresh_kma_text))),
            patch.object(main, "_lookup_building_selection", AsyncMock(return_value=server_building())),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
            patch.object(
                main,
                "fetch_road_width_evidence",
                AsyncMock(
                    return_value={
                        "available": True,
                        "official_available": True,
                        "width_m": 49.7,
                        "source": "official_road_right_of_way",
                        "source_chain": ["vworld_wfs", "official_road_right_of_way"],
                    }
                ),
            ),
            patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=official_canyon(SELECTION_ID, SELECTION_ID))),
        ):
            stale_response = client.post("/api/evaluate", json=request_payload)

        self.assertEqual(stale_response.status_code, 200)
        stale_payload = stale_response.json()
        self.assertEqual(stale_payload["final_judgment"], "HOLD")
        self.assertIn("weather:surface_weather_stale", stale_payload["input_quality"]["reasons"])
        self.assertEqual(stale_payload["weather_evidence"]["reason"], "surface_weather_stale")
        self.assertIsNone(stale_payload["urban_factors"]["Fcanyon"])
        self.assertIsNone(stale_payload["ews"])
        self.assertIsNone(stale_payload["correlation_id"])


if __name__ == "__main__":
    unittest.main()
