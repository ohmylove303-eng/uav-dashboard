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

    def test_building_hub_height_is_used_only_after_the_verified_click_is_enriched(self):
        vworld_footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "source": "vworld_wfs",
            "source_chain": ["vworld_wfs"],
            "display_name": "서울특별시청",
            "properties": {"bd_mgt_sn": "1114010300100310000019224", "buld_nm": "서울특별시청"},
        }
        enriched = {
            **vworld_footprint,
            "source_chain": ["vworld_wfs", "molit_building_hub"],
            "properties": {
                **vworld_footprint["properties"],
                "buld_hg": 41.65,
                "gro_flo_co": 6,
                "far_percent": 318.7,
                "bcr_percent": 52.4,
            },
            "field_sources": {
                "height_m": {"source": "molit_building_hub", "status": "official_verified", "property_key": "heit", "value": 41.65},
                "floor_count": {"source": "molit_building_hub", "status": "official_verified", "property_key": "grndFlrCnt", "value": 6},
                "far_percent": {"source": "molit_building_hub", "status": "official_verified", "property_key": "vlRat", "value": 318.7},
                "bcr_percent": {"source": "molit_building_hub", "status": "official_verified", "property_key": "bcRat", "value": 52.4},
            },
        }

        with (
            patch.object(main, "lookup_building_footprint", AsyncMock(return_value=vworld_footprint)),
            patch.object(main, "enrich_verified_footprint", AsyncMock(return_value=enriched)),
        ):
            response = self.client.get("/api/building-height", params={"lat": 37.5665, "lon": 126.9780})

        payload = response.json()
        self.assertEqual(payload["estimated_height_m"], 41.6)
        self.assertEqual(payload["estimated_floors"], 6)
        self.assertEqual(payload["far_percent"], 318.7)
        self.assertEqual(payload["bcr_percent"], 52.4)
        self.assertEqual(payload["source_chain"], ["vworld_wfs", "molit_building_hub", "official_building_height"])


if __name__ == "__main__":
    unittest.main()
