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
    CANYON_RECEIPT_IDS,
    CANYON_RECEIPT_SOURCES,
    SELECTION_ID,
    authoritative_weather,
    server_building,
)


class EvaluationQualityGateTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.base_payload = {
            "latitude": 37.5665,
            "longitude": 126.9780,
            "selection_id": SELECTION_ID,
            "building_height": 40.0,
            "street_width": 27.0,
            "wind_alignment": "직각",
            "mission_altitude": 30.0,
            "no_fly_zone": False,
            "crowd_area": False,
            "gps_locked": 12,
            "glonass_locked": 6,
            "drone_model": main.DroneModel.MAVIC_3.value,
            "building_source": "vworld_wfs_live",
            "building_profile_source": "official_verified",
            "building_source_chain": ["vworld_wfs_live", "official_verified"],
            "building_confidence": 0.96,
            "building_evidence": {
                "available": True,
                "official_available": True,
                "status": "official_verified",
                "source_chain": ["vworld_wfs_live", "official_verified"],
                "receipt": {
                    "kind": "official_building_height",
                    "geometry_receipt": True,
                    "selection_match": True,
                    "source_chain": ["vworld_wfs_live", "official_verified"],
                },
            },
            "road_evidence": {
                "available": True,
                "official_available": True,
                "width_m": 49.7,
                "lane_count": 4,
                "road_name": "Sejong-daero",
                "source": "official_road_right_of_way",
                "source_chain": ["vworld_wfs", "official_road_right_of_way", "lt_l_n3a0020000"],
            },
            "canyon_evidence": {
                "available": True,
                "official_available": True,
                "selection_id": SELECTION_ID,
                "facade_gap_m": 27.0,
                "effective_canyon_width_m": 27.0,
                "official_road_right_of_way_width_m": 49.7,
                "road_crossing_verified": True,
                "source": "direct_vworld_official_receipt",
                "source_chain": ["vworld_wfs", "official_canyon_width", "direct_vworld_official_receipt"],
                "target_building": {
                    "id": "target-7",
                    "geometry_receipt": True,
                    "native_feature_id": "lt_c_spbd.7",
                },
                "opposing_building": {"id": "opposing-7", "geometry_receipt": True},
                "receipt": {
                    "kind": "official_canyon_width",
                    "selection_id": SELECTION_ID,
                    "target_native_feature_id": "lt_c_spbd.7",
                    "target_geometry_receipt": True,
                    "opposing_geometry_receipt": True,
                    "road_geometry_receipt": True,
                    "road_crossing_verified": True,
                    "source_chain": ["vworld_wfs", "official_canyon_width", "direct_vworld_official_receipt"],
                    "receipt_ids": dict(CANYON_RECEIPT_IDS),
                    "receipt_sources": dict(CANYON_RECEIPT_SOURCES),
                },
            },
        }
        self.authoritative_weather = authoritative_weather(latitude=37.5665, longitude=126.9780)

    def test_all_official_inputs_allow_a_normal_verdict(self):
        with (
            patch.object(main, "_lookup_building_selection", AsyncMock(return_value=server_building())),
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=dict(self.authoritative_weather))),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=self.base_payload["road_evidence"])),
            patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=self.base_payload["canyon_evidence"])),
        ):
            response = self.client.post("/api/evaluate", json=self.base_payload)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotEqual(payload["final_judgment"], "HOLD")
        self.assertEqual(payload["input_quality"]["status"], "ready")
        self.assertIsInstance(payload["urban_factors"]["Fcanyon"], float)
        self.assertEqual(payload["urban_factors"]["W"], 27.0)
        self.assertEqual(payload["urban_factors"]["official_road_right_of_way_width_m"], 49.7)
        self.assertEqual(payload["selection_id"], SELECTION_ID)
        self.assertIsInstance(payload["correlation_id"], str)

    def test_official_road_right_of_way_without_a_verified_facade_gap_forces_hold(self):
        payload = dict(self.base_payload)
        payload.pop("canyon_evidence")

        with (
            patch.object(main, "_lookup_building_selection", AsyncMock(return_value=server_building())),
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=dict(self.authoritative_weather))),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=self.base_payload["road_evidence"])),
            patch.object(
                main,
                "fetch_canyon_width_evidence",
                AsyncMock(
                    return_value={
                        "available": False,
                        "official_available": False,
                        "facade_gap_m": None,
                        "source": "official_canyon_width_unavailable",
                        "reason": "opposing_official_building_not_matched",
                        "source_chain": ["vworld_wfs", "official_canyon_width_unavailable"],
                        "receipt": {"kind": "official_canyon_width_unavailable"},
                    }
                ),
            ),
        ):
            response = self.client.post("/api/evaluate", json=payload)

        body = response.json()
        self.assertEqual(body["final_judgment"], "HOLD")
        self.assertEqual(body["input_quality"]["missing_prerequisites"], ["canyon_width"])
        self.assertIsNone(body["urban_factors"]["Fcanyon"])

    def test_official_floor_count_derived_height_forces_hold_even_with_verified_facade_gap(self):
        payload = dict(self.base_payload)

        with (
            patch.object(
                main,
                "_lookup_building_selection",
                AsyncMock(return_value=server_building(official_height=False)),
            ),
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=dict(self.authoritative_weather))),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=self.base_payload["road_evidence"])),
            patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=self.base_payload["canyon_evidence"])),
        ):
            response = self.client.post("/api/evaluate", json=payload)

        body = response.json()
        self.assertEqual(body["final_judgment"], "HOLD")
        self.assertEqual(body["input_quality"]["status"], "hold")
        self.assertEqual(body["input_quality"]["missing_prerequisites"], ["building"])
        self.assertIsNone(body["urban_factors"]["Fcanyon"])

    def test_missing_or_unverified_inputs_force_hold_and_remove_exact_fcanyon(self):
        cases = [
            (
                "road unavailable",
                {
                    "road_evidence": {
                        "available": False,
                        "official_available": False,
                        "source": "official_road_right_of_way_unavailable",
                        "source_chain": ["vworld_wfs", "official_road_right_of_way_unavailable"],
                    }
                    ,
                    "canyon_evidence": {
                        "available": False,
                        "official_available": False,
                        "source": "official_canyon_width_unavailable",
                        "source_chain": ["vworld_wfs", "official_canyon_width_unavailable"],
                        "reason": "official_road_geometry_not_matched",
                        "receipt": {"kind": "official_canyon_width_unavailable"},
                    },
                },
                dict(self.authoritative_weather),
                ["canyon_width"],
                server_building(),
            ),
            (
                "building estimated",
                {},
                dict(self.authoritative_weather),
                ["building"],
                server_building(official_height=False),
            ),
            (
                "weather non authoritative",
                {},
                {
                    **dict(self.authoritative_weather),
                    "authoritative": False,
                    "authority_source": "open_meteo_surface",
                    "source": "open_meteo_surface",
                    "source_chain": ["open_meteo_surface"],
                },
                ["weather"],
                server_building(),
            ),
        ]

        for case_name, payload_override, weather_payload, expected_missing, server_building_result in cases:
            with self.subTest(case_name=case_name):
                payload = dict(self.base_payload)
                payload.update(payload_override)
                server_road = payload_override.get("road_evidence", self.base_payload["road_evidence"])
                server_canyon = payload_override.get("canyon_evidence", self.base_payload["canyon_evidence"])
                with (
                    patch.object(
                        main,
                        "_lookup_building_selection",
                        AsyncMock(return_value=server_building_result),
                    ),
                    patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather_payload)),
                    patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
                    patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
                    patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
                    patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=server_road)),
                    patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=server_canyon)),
                ):
                    response = self.client.post("/api/evaluate", json=payload)

                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["final_judgment"], "HOLD")
                self.assertEqual(body["input_quality"]["status"], "hold")
                self.assertEqual(body["input_quality"]["missing_prerequisites"], expected_missing)
                self.assertIsNone(body["urban_factors"]["Fcanyon"])
                self.assertIsNone(body["urban_factors"]["Fcanyon_raw"])
                self.assertIsNone(body["ews"])


if __name__ == "__main__":
    unittest.main()
