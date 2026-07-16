from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402


class OfficialBuildingHeightRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_official_height_field_overrides_coordinate_heuristic(self):
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "source": "vworld_wfs",
            "source_chain": ["vworld_wfs"],
            "display_name": "검증건물",
            "properties": {"buld_nm": "검증건물", "buld_hg": 21.4, "gro_flo_co": 6},
        }

        with patch.object(main, "lookup_building_footprint", AsyncMock(return_value=footprint)):
            response = self.client.get("/api/building-height", params={"lat": 37.5665, "lon": 126.9780})

        payload = response.json()
        self.assertEqual(payload["estimated_height_m"], 21.4)
        self.assertEqual(payload["estimated_floors"], 6)
        self.assertEqual(payload["source"], "official_building_height")
        self.assertTrue(payload["official_available"])
        self.assertEqual(payload["receipt"]["kind"], "official_building_height")

    def test_official_floor_count_is_labeled_as_derived_not_exact_height(self):
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "source": "vworld_wfs",
            "source_chain": ["vworld_wfs"],
            "display_name": "층수확인건물",
            "properties": {"buld_nm": "층수확인건물", "gro_flo_co": 6},
        }

        with patch.object(main, "lookup_building_footprint", AsyncMock(return_value=footprint)):
            response = self.client.get("/api/building-height", params={"lat": 37.5665, "lon": 126.9780})

        payload = response.json()
        self.assertEqual(payload["estimated_height_m"], 19.8)
        self.assertEqual(payload["source"], "official_floor_count_derived")
        self.assertFalse(payload["official_available"])
        self.assertEqual(payload["receipt"]["kind"], "official_floor_count_derived")


if __name__ == "__main__":
    unittest.main()
