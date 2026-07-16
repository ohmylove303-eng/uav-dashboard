from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import building_footprint  # noqa: E402
import building_height  # noqa: E402


class BuildingFootprintProvenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_frontend_seeded_vworld_cache_stays_unverified(self) -> None:
        cache_entry = {
            "center": {"lat": 37.5665, "lon": 126.9780},
            "geometry": [
                [126.9780, 37.5665],
                [126.9785, 37.5665],
                [126.9785, 37.5670],
                [126.9780, 37.5665],
            ],
            "properties": {"name": "서울특별시청"},
            "source": "vworld_wfs",
            "source_origin": "frontend_seed",
        }

        with (
            patch.dict(building_footprint.os.environ, {}, clear=True),
            patch.object(building_footprint, "_load_footprint_cache", return_value=[cache_entry]),
            patch.object(building_footprint, "_lookup_osm_fallback_sync", return_value=None),
            patch.object(building_footprint, "_resolve_vworld_api_key", return_value=None),
        ):
            result = await building_footprint.lookup_building_footprint(37.5665, 126.9780)

        self.assertEqual(result["source_origin"], "frontend_seed")
        self.assertEqual(result["source_status"], "unverified_cache")
        self.assertFalse(result["official_footprint_available"])

    async def test_verified_vworld_cache_receipt_is_official(self) -> None:
        cache_entry = {
            "center": {"lat": 37.5665, "lon": 126.9780},
            "geometry": [
                [126.9780, 37.5665],
                [126.9785, 37.5665],
                [126.9785, 37.5670],
                [126.9780, 37.5665],
            ],
            "properties": {
                "buld_nm": "서울특별시청",
                "buld_hg": 42.0,
                "gro_flo_co": 11,
                "zoning_type": "중심상업",
                "far_percent": 1000,
                "bcr_percent": 72,
            },
            "source": "vworld_wfs",
            "source_origin": "vworld_wfs",
        }

        with (
            patch.dict(building_footprint.os.environ, {}, clear=True),
            patch.object(building_footprint, "_load_footprint_cache", return_value=[cache_entry]),
            patch.object(building_footprint, "_lookup_osm_fallback_sync", return_value=None),
            patch.object(building_footprint, "_resolve_vworld_api_key", return_value=None),
        ):
            result = await building_footprint.lookup_building_footprint(37.5665, 126.9780)

        self.assertEqual(result["source_status"], "official_verified")
        self.assertTrue(result["official_footprint_available"])
        self.assertTrue(result["official_building_data"])
        self.assertEqual(result["field_sources"]["height_m"]["status"], "official_verified")
        self.assertEqual(result["field_sources"]["far_percent"]["status"], "official_verified")

    async def test_live_vworld_keeps_unverified_cached_far_bcr_labeled(self) -> None:
        cache_entry = {
            "center": {"lat": 37.5665, "lon": 126.9780},
            "geometry": [
                [126.9780, 37.5665],
                [126.9785, 37.5665],
                [126.9785, 37.5670],
                [126.9780, 37.5665],
            ],
            "properties": {
                "far_percent": 700,
                "bcr_percent": 55,
                "zoning_type": "중심상업",
            },
            "source": "vworld_wfs",
            "source_origin": "frontend_context_seed",
        }
        feature_payload = {
            "features": [
                {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [126.9780, 37.5665],
                            [126.9786, 37.5665],
                            [126.9786, 37.5671],
                            [126.9780, 37.5665],
                        ]],
                    },
                    "properties": {
                        "buld_nm": "서울특별시청",
                        "buld_hg": 42,
                        "gro_flo_co": 11,
                    },
                }
            ]
        }

        with (
            patch.object(building_footprint, "_load_footprint_cache", return_value=[cache_entry]),
            patch.object(building_footprint, "_lookup_osm_fallback_sync", return_value=None) as osm_lookup,
            patch.object(building_footprint, "_resolve_vworld_api_key", return_value="test-key"),
            patch.object(
                building_footprint,
                "_fetch_text_with_retries_sync",
                return_value=json.dumps(feature_payload),
            ),
            patch.object(building_footprint, "_store_footprint_cache_entry", return_value={}),
        ):
            result = await building_footprint.lookup_building_footprint(37.5665, 126.9780)

        self.assertEqual(result["source_status"], "official_verified")
        self.assertTrue(result["official_footprint_available"])
        self.assertFalse(result["official_building_data"])
        self.assertEqual(result["field_sources"]["height_m"]["status"], "official_verified")
        self.assertEqual(result["field_sources"]["far_percent"]["status"], "unverified_cache")
        self.assertEqual(result["field_sources"]["bcr_percent"]["status"], "unverified_cache")
        osm_lookup.assert_not_called()

    async def test_osm_fallback_stays_estimated(self) -> None:
        mock_osm = {
            "available": True,
            "source": "osm_fallback",
            "geometry": [
                [126.9780, 37.5665],
                [126.9785, 37.5665],
                [126.9785, 37.5670],
                [126.9780, 37.5665],
            ],
            "properties": {"building": "yes"},
        }

        with (
            patch.dict(building_footprint.os.environ, {}, clear=True),
            patch.object(building_footprint, "_load_footprint_cache", return_value=[]),
            patch.object(building_footprint, "_lookup_osm_fallback_sync", return_value=mock_osm),
            patch.object(building_footprint, "_resolve_vworld_api_key", return_value=None),
        ):
            result = await building_footprint.lookup_building_footprint(37.5665, 126.9780)

        self.assertEqual(result["source_status"], "estimated")
        self.assertFalse(result["official_footprint_available"])
        self.assertFalse(result["official_building_data"])


class BuildingHeightSemanticsTests(unittest.TestCase):
    def test_coordinate_based_height_never_becomes_official(self) -> None:
        result = building_height.predict_building_height(37.5665, 126.9780)

        self.assertFalse(result["official_building_data"])
        self.assertEqual(result["source_status"], "estimated")
        self.assertEqual(result["profile_source"], "coordinate_based")
        self.assertEqual(result["field_sources"]["estimated_height_m"]["status"], "estimated")
        self.assertEqual(result["field_sources"]["estimated_floors"]["status"], "estimated")
        self.assertEqual(result["field_sources"]["zoning_type"]["status"], "estimated")
        self.assertEqual(result["field_sources"]["far_percent"]["status"], "estimated")
        self.assertEqual(result["field_sources"]["bcr_percent"]["status"], "estimated")


if __name__ == "__main__":
    unittest.main()
