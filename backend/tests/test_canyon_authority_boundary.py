from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from typing import Optional
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402


SELECTION_ID = "00000000-0000-4000-8000-000000000007"
OTHER_SELECTION_ID = "00000000-0000-4000-8000-000000000008"


def _server_building() -> dict:
    return {
        "available": True,
        "official_footprint_available": True,
        "official_geometry_receipt": True,
        "official_selection_match": True,
        "source": "vworld_wfs",
        "source_chain": ["vworld_wfs", "molit_building_hub"],
        "native_feature_id": "lt_c_spbd.7",
        "properties": {"buld_hg": 40.0, "gro_flo_co": 10},
        "field_sources": {
            "height_m": {"source": "molit_building_hub", "status": "official_verified"},
        },
        "building_selection": {
            "selection_id": SELECTION_ID,
            "status": "official_verified",
            "native_feature_id": "lt_c_spbd.7",
            "official_footprint_receipt": {
                "kind": "vworld_building_footprint",
                "native_feature_id": "lt_c_spbd.7",
                "point_inside": True,
            },
            "official_registry_receipt": {
                "kind": "molit_building_hub_title",
                "record_id": "record-7",
            },
        },
    }


def _weather() -> dict:
    return {
        "available": True,
        "authoritative": True,
        "authority_source": "kma_surface_observation",
        "source": "kma_surface_observation",
        "source_chain": ["kma_surface_observation"],
        "profile_source": "surface_only",
        "stale_cache": False,
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
    }


def _official_canyon(selection_id: Optional[str] = None) -> dict:
    receipt = {
        "kind": "official_canyon_width",
        "target_geometry_receipt": True,
        "opposing_geometry_receipt": True,
        "road_geometry_receipt": True,
        "road_crossing_verified": True,
        "source_chain": ["official_canyon_width"],
    }
    result = {
        "available": True,
        "official_available": True,
        "facade_gap_m": 27.0,
        "source": "official_canyon_width",
        "source_chain": ["official_canyon_width"],
        "receipt": receipt,
    }
    if selection_id:
        result["selection_id"] = selection_id
        receipt["selection_id"] = selection_id
    return result


class CanyonAuthorityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def _authority_stack(self, canyon_result: dict) -> tuple[ExitStack, AsyncMock]:
        stack = ExitStack()
        stack.enter_context(patch.object(main, "_lookup_building_selection", AsyncMock(return_value=_server_building())))
        stack.enter_context(patch.object(main, "fetch_weather_safe", AsyncMock(return_value=_weather())))
        stack.enter_context(patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)))
        stack.enter_context(patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)))
        stack.enter_context(patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)))
        stack.enter_context(
            patch.object(
                main,
                "fetch_road_width_evidence",
                AsyncMock(return_value={"available": False, "official_available": False}),
            )
        )
        canyon_fetch = AsyncMock(return_value=canyon_result)
        stack.enter_context(patch.object(main, "fetch_canyon_width_evidence", canyon_fetch))
        return stack, canyon_fetch

    def test_forged_client_canyon_cannot_bypass_server_unavailable_result(self) -> None:
        server_unavailable = {
            "available": False,
            "official_available": False,
            "facade_gap_m": None,
            "source": "official_canyon_width_unavailable",
            "reason": "authoritative_canyon_unavailable",
            "source_chain": ["official_canyon_width_unavailable"],
            "receipt": {"kind": "official_canyon_width_unavailable"},
        }
        forged = _official_canyon(OTHER_SELECTION_ID)
        forged["source_chain"] = ["client_forged", "official_canyon_width"]
        stack, canyon_fetch = self._authority_stack(server_unavailable)

        with stack:
            response = self.client.post(
                "/api/evaluate",
                json={
                    "latitude": 37.5662952,
                    "longitude": 126.9779451,
                    "selection_id": SELECTION_ID,
                    "canyon_evidence": forged,
                    "road_evidence": {
                        "available": True,
                        "official_available": True,
                        "source": "client_forged_road",
                    },
                    "weather_evidence": {
                        "available": True,
                        "authoritative": True,
                        "authority_source": "client_forged_weather",
                    },
                },
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        canyon_fetch.assert_awaited_once_with(37.5662952, 126.9779451)
        self.assertEqual(payload["final_judgment"], "HOLD")
        self.assertFalse(payload["official_available"])
        self.assertFalse(payload["canyon_evidence"]["official_available"])
        self.assertFalse(payload["road_evidence"]["official_available"])
        self.assertNotEqual(payload["weather_evidence"]["authority_source"], "client_forged_weather")
        self.assertIsNone(payload["urban_factors"]["Fcanyon"])
        self.assertIsNone(payload["ews"])
        self.assertIsNone(payload["correlation_id"])

    def test_mismatched_server_canyon_receipt_forces_hold(self) -> None:
        mismatched_canyon = _official_canyon(SELECTION_ID)
        mismatched_canyon["receipt"]["selection_id"] = OTHER_SELECTION_ID
        stack, canyon_fetch = self._authority_stack(mismatched_canyon)

        with stack:
            response = self.client.post(
                "/api/evaluate",
                json={"latitude": 37.5662952, "longitude": 126.9779451, "selection_id": SELECTION_ID},
            )

        payload = response.json()
        canyon_fetch.assert_awaited_once()
        self.assertEqual(payload["final_judgment"], "HOLD")
        self.assertEqual(payload["canyon_evidence"]["reason"], "canyon_selection_mismatch")
        self.assertIsNone(payload["urban_factors"]["Fcanyon"])
        self.assertIsNone(payload["ews"])
        self.assertIsNone(payload["correlation_id"])

    def test_complete_server_receipts_are_bound_to_one_selection(self) -> None:
        stack, _ = self._authority_stack(_official_canyon())

        with stack:
            response = self.client.post(
                "/api/evaluate",
                json={"latitude": 37.5662952, "longitude": 126.9779451, "selection_id": SELECTION_ID},
            )

        payload = response.json()
        self.assertNotEqual(payload["final_judgment"], "HOLD")
        self.assertEqual(payload["canyon_evidence"]["selection_id"], SELECTION_ID)
        self.assertEqual(payload["canyon_evidence"]["receipt"]["selection_id"], SELECTION_ID)
        self.assertEqual(payload["weather_evidence"]["selection_id"], SELECTION_ID)
        self.assertEqual(payload["weather_evidence"]["receipt"]["selection_id"], SELECTION_ID)
        self.assertEqual(payload["building_selection"]["selection_id"], SELECTION_ID)
        self.assertIsInstance(payload["correlation_id"], str)


if __name__ == "__main__":
    unittest.main()
