from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402
import building_height  # noqa: E402
import building_footprint  # noqa: E402


class DataPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_weather_api_exposes_source_chain(self):
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
        }

        with (
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather_payload)),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=4.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value={"layers": [], "stale_cache": False})),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value={"layers": [], "mode": "L", "stale_cache": False})),
        ):
            response = await main.get_weather_api()

        weather = response["weather"]
        self.assertEqual(weather["profile_source"], "kma_radiosonde_wind_profiler")
        self.assertCountEqual(
            weather["source_chain"],
            ["open_meteo_surface", "kma_radiosonde", "kma_wind_profiler"],
        )
        self.assertIn("kma_radiosonde", weather["source"])
        self.assertIn("kma_wind_profiler", weather["source"])

    async def test_evaluate_flight_applies_building_provenance(self):
        weather_payload = {
            "wind_speed": 5.0,
            "gust_speed": 7.5,
            "wind_direction": 90,
            "visibility": 12.0,
            "precipitation_prob": 0,
            "weather_code": 0,
            "temperature": 20,
            "dew_point": 14,
            "humidity": 50,
            "cloud_cover": 20,
            "sunrise": "06:00",
            "sunset": "18:00",
            "source": "open_meteo_surface",
            "source_chain": ["open_meteo_surface"],
            "profile_source": "surface_only",
            "stale_cache": False,
        }

        request = main.EvaluationRequest(
            latitude=37.5665,
            longitude=126.9780,
            building_height=40,
            street_width=10,
            wind_alignment="직각",
            mission_altitude=30,
            no_fly_zone=False,
            crowd_area=False,
            gps_locked=12,
            glonass_locked=6,
            drone_model=main.DroneModel.MAVIC_3,
            wind_speed=None,
            building_source="osm_fallback",
            building_profile_source="coordinate_based",
            building_source_chain=["osm_fallback", "coordinate_based"],
            building_confidence=0.42,
        )

        with (
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather_payload)),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
        ):
            response = await main.evaluate_flight(request)

        self.assertEqual(response.profile_source, "surface_only")
        self.assertEqual(response.building_source, "osm_fallback")
        self.assertEqual(response.building_profile_source, "coordinate_based")
        self.assertCountEqual(response.building_source_chain, ["osm_fallback", "coordinate_based"])
        self.assertAlmostEqual(response.building_confidence, 0.42, places=2)
        self.assertEqual(response.final_judgment, main.JudgmentLevel.HOLD)
        self.assertEqual(response.input_quality["status"], "hold")
        self.assertCountEqual(
            response.input_quality["missing_prerequisites"],
            ["building", "road_width", "weather"],
        )
        self.assertIsNone(response.urban_factors["Fcanyon"])
        self.assertIsNone(response.urban_factors["Fcanyon_raw"])
        self.assertIsNone(response.urban_factors["building_canyon_weight"])
        self.assertIn("open_meteo_surface", response.source_chain)
        self.assertIn("osm_fallback", response.source_chain)
        self.assertCountEqual(response.weather["source_chain"], ["open_meteo_surface"])

    async def test_corridor_analysis_returns_segment_provenance(self):
        weather_payload = {
            "wind_speed": 4.8,
            "gust_speed": 6.2,
            "wind_direction": 140,
            "visibility": 8.5,
            "precipitation_prob": 0,
            "weather_code": 0,
            "temperature": 19,
            "dew_point": 11,
            "humidity": 45,
            "cloud_cover": 20,
            "sunrise": "06:00",
            "sunset": "18:00",
            "source": "open_meteo_surface",
            "source_chain": ["open_meteo_surface"],
            "profile_source": "surface_only",
            "stale_cache": False,
        }

        request = main.CorridorAnalysisRequest(
            point_a=main.RoutePoint(lat=37.5665, lon=126.9780),
            point_b=main.RoutePoint(lat=37.5700, lon=126.9850),
            altitude=50,
            segment_count=3,
            drone_type=main.DroneModel.MAVIC_3.value,
        )

        with patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather_payload)):
            response = await main.analyze_corridor(request)

        self.assertCountEqual(response["weather_source_chain"], ["open_meteo_surface"])
        self.assertTrue(response["building_source_chain"])
        first_segment = response["segments"][0]
        self.assertIn("building_source", first_segment)
        self.assertIn("building_confidence", first_segment)
        self.assertIn("weather_source_chain", first_segment)

    def test_building_height_wrapper_exposes_provenance(self):
        result = building_height.predict_building_height(37.5665, 126.9780)
        self.assertIn("source_chain", result)
        self.assertIn("profile_source", result)
        self.assertIn("building_confidence", result)
        self.assertFalse(result["stale_cache"])
        self.assertEqual(result["source"], "building_height_heuristic")

    async def test_building_footprint_fallback_exposes_provenance(self):
        mock_osm = {
            "available": True,
            "source": "osm_fallback",
            "geometry": [[126.9780, 37.5665], [126.9785, 37.5665], [126.9785, 37.5670], [126.9780, 37.5665]],
            "properties": {"building": "yes"},
        }

        with (
            patch.dict(building_footprint.os.environ, {}, clear=True),
            patch.object(building_footprint, "_match_cached_footprint", return_value=None),
            patch.object(building_footprint, "_lookup_osm_fallback_sync", return_value=mock_osm),
            patch.object(building_footprint, "_resolve_vworld_api_key", return_value=None),
        ):
            result = await building_footprint.lookup_building_footprint(37.5665, 126.9780)

        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "osm_fallback")
        self.assertEqual(result["profile_source"], "fallback")
        self.assertEqual(result["source_chain"], ["osm_fallback"])
        self.assertAlmostEqual(result["building_confidence"], result["confidence"], places=2)


if __name__ == "__main__":
    unittest.main()
