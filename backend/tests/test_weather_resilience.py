from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402


class WeatherResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main.WEATHER_CACHE.clear()
        main.WEATHER_LAST_GOOD_CACHE.clear()

    async def test_fetch_weather_safe_uses_fresh_kma_cache_during_timeout(self):
        cache_key = main._cache_key_for_latlon(37.5665, 126.9780)
        main._cache_set(
            main.WEATHER_CACHE,
            cache_key,
            {
                "wind_speed": 6.0,
                "gust_speed": 8.0,
                "wind_direction": 180,
                "visibility": 10.0,
                "precipitation_prob": 20,
                "weather_code": 0,
                "temperature": 22.0,
                "dew_point": 16.0,
                "humidity": 55,
                "cloud_cover": 10,
                "sunrise": "05:10",
                "sunset": "19:55",
                "source": "kma_surface_observation",
                "source_chain": ["kma_surface_observation"],
                "profile_source": "surface_only",
                "stale_cache": False,
            },
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
            "value": {
                "wind_speed": 6.0,
                "gust_speed": 8.0,
                "wind_direction": 180,
                "visibility": 10.0,
                "precipitation_prob": 20,
                "weather_code": 0,
                "temperature": 22.0,
                "dew_point": 16.0,
                "humidity": 55,
                "cloud_cover": 10,
                "sunrise": "05:10",
                "sunset": "19:55",
                "source": "kma_surface_observation",
                "source_chain": ["kma_surface_observation"],
                "profile_source": "surface_only",
                "stale_cache": False,
            },
        }

        with patch.object(main, "fetch_weather", AsyncMock(side_effect=TimeoutError("boom"))):
            payload = await main.fetch_weather_safe(37.5665, 126.9780)

        self.assertFalse(payload["available"])
        self.assertFalse(payload["authoritative"])
        self.assertEqual(payload["reason"], "surface_weather_timeout")
        self.assertEqual(payload["source_chain"], ["weather_unavailable", "surface_weather_timeout"])

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


if __name__ == "__main__":
    unittest.main()
