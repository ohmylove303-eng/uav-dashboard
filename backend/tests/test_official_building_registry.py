from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import official_building_registry  # noqa: E402
import official_building_hub_client  # noqa: E402


class OfficialBuildingRegistryTests(unittest.IsolatedAsyncioTestCase):
    def test_building_management_number_maps_to_building_hub_query(self):
        query = official_building_registry.building_hub_query_from_management_number(
            "1114010300100310000019224"
        )

        self.assertEqual(
            query,
            {
                "sigunguCd": "11140",
                "bjdongCd": "10300",
                "platGbCd": "0",
                "bun": "0031",
                "ji": "0000",
            },
        )

    async def test_verified_click_is_enriched_from_single_official_registry_record(self):
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "source": "vworld_wfs",
            "source_chain": ["vworld_wfs"],
            "display_name": "서울특별시청",
            "properties": {
                "bd_mgt_sn": "1114010300100310000019224",
                "buld_nm": "서울특별시청",
                "gro_flo_co": 6,
            },
        }
        registry_row = {
            "bldNm": "서울특별시청",
            "dongNm": "본관동",
            "heit": "41.65",
            "grndFlrCnt": "6",
            "ugrndFlrCnt": "1",
            "bcRat": "52.4",
            "vlRat": "318.7",
            "mainPurpsCdNm": "업무시설",
            "mgmBldrgstPk": "official-title-record",
        }

        with (
            patch.dict(
                official_building_hub_client.os.environ,
                {"MOLIT_BUILDING_HUB_SERVICE_KEY": "server-only-key"},
                clear=True,
            ),
            patch.object(
                official_building_registry,
                "_fetch_title_records",
                AsyncMock(return_value=[registry_row]),
            ),
        ):
            result = await official_building_registry.enrich_verified_footprint(footprint)

        self.assertEqual(result["source_chain"], ["vworld_wfs", "molit_building_hub"])
        self.assertEqual(result["display_name"], "서울특별시청")
        self.assertEqual(result["properties"]["buld_hg"], 41.65)
        self.assertEqual(result["properties"]["gro_flo_co"], 6)
        self.assertEqual(result["properties"]["far_percent"], 318.7)
        self.assertEqual(result["properties"]["bcr_percent"], 52.4)
        self.assertEqual(result["field_sources"]["height_m"]["source"], "molit_building_hub")
        self.assertEqual(result["field_sources"]["far_percent"]["status"], "official_verified")
        self.assertTrue(result["official_building_data"])
        self.assertNotIn("server-only-key", str(result))

    async def test_ambiguous_registry_records_do_not_overwrite_click_selection(self):
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "source": "vworld_wfs",
            "source_chain": ["vworld_wfs"],
            "display_name": "대상 건물",
            "properties": {
                "bd_mgt_sn": "1114010300100310000019224",
                "buld_nm": "대상 건물",
            },
        }
        records = [
            {"bldNm": "서관", "heit": "18.0"},
            {"bldNm": "동관", "heit": "26.0"},
        ]

        with (
            patch.dict(
                official_building_hub_client.os.environ,
                {"MOLIT_BUILDING_HUB_SERVICE_KEY": "server-only-key"},
                clear=True,
            ),
            patch.object(
                official_building_registry,
                "_fetch_title_records",
                AsyncMock(return_value=records),
            ),
        ):
            result = await official_building_registry.enrich_verified_footprint(footprint)

        self.assertEqual(result["properties"], footprint["properties"])
        self.assertEqual(result["registry_status"], "unavailable")
        self.assertEqual(result["registry_reason"], "official_registry_ambiguous")
        self.assertNotIn("molit_building_hub", result["source_chain"])

    async def test_unverified_click_never_queries_the_official_registry(self):
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": False,
            "official_selection_match": False,
            "properties": {"bd_mgt_sn": "1114010300100310000019224"},
        }

        with patch.object(official_building_registry, "_fetch_title_records", AsyncMock()) as fetch:
            result = await official_building_registry.enrich_verified_footprint(footprint)

        fetch.assert_not_called()
        self.assertEqual(result["registry_reason"], "official_geometry_not_verified")

    async def test_mismatched_geometry_identifier_never_queries_registry(self):
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "native_feature_id": "lt_c_spbd.selected",
            "official_footprint_receipt": {
                "kind": "vworld_building_footprint",
                "native_feature_id": "lt_c_spbd.different",
                "point_inside": True,
            },
            "verified_properties": {
                "bd_mgt_sn": "1114010300100310000019224",
            },
            "properties": {
                "bd_mgt_sn": "1114010300100310000019224",
            },
        }

        with patch.object(official_building_registry, "_fetch_title_records", AsyncMock()) as fetch:
            result = await official_building_registry.enrich_verified_footprint(footprint)

        fetch.assert_not_called()
        self.assertEqual(result["registry_status"], "unavailable")
        self.assertEqual(result["registry_reason"], "official_geometry_identifier_mismatch")


if __name__ == "__main__":
    unittest.main()
