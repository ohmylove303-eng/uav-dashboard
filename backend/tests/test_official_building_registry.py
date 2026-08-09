from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import official_building_registry  # noqa: E402


class FakeResponse:
    def __init__(self, *, status_code: int, text: str, json_data=None):
        self.status_code = status_code
        self.text = text
        self.json_data = json_data

    def json(self):
        if self.json_data is None:
            raise ValueError("not JSON")
        return self.json_data


class FakeAsyncClient:
    def __init__(self, response: FakeResponse):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self.response


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

    def test_existing_building_hub_key_alias_is_recognized(self):
        with patch.dict(
            official_building_registry.os.environ,
            {"MOLIT_BUILDING_HUB_API_KEY": "server-only-key"},
            clear=True,
        ):
            self.assertTrue(official_building_registry.service_key_configured())
            self.assertEqual(
                official_building_registry._resolve_service_key(), "server-only-key"
            )

    def test_semantic_legacy_building_hub_key_alias_is_recognized(self):
        with patch.dict(
            official_building_registry.os.environ,
            {"MOLIT_BLDG_HUB_CREDENTIAL_KEY": "server-only-key"},
            clear=True,
        ):
            self.assertTrue(official_building_registry.service_key_configured())
            self.assertEqual(
                official_building_registry._resolve_service_key(), "server-only-key"
            )

    def test_encoded_data_go_service_key_is_normalized_before_request(self):
        encoded_key = "abc%2Bdef%2Fghi%3D"

        normalized_key = official_building_registry._normalize_service_key(encoded_key)

        self.assertEqual(normalized_key, "abc+def/ghi=")

    def test_invalid_data_go_service_key_shape_is_rejected_without_echo(self):
        invalid_key = "invalid%ZZ key"

        normalized_key = official_building_registry._normalize_service_key(invalid_key)

        self.assertIsNone(normalized_key)

    def test_building_hub_upstream_status_is_classified_without_response_body(self):
        for status in (401, 403, 502):
            with self.subTest(status=status):
                self.assertEqual(
                    official_building_registry._building_hub_upstream_status_reason(status),
                    f"molit_building_hub_upstream_http_{status}",
                )

    def test_building_hub_gateway_codes_are_classified_without_echoing_body(self):
        cases = {
            "SERVICE_ACCESS_DENIED_ERROR": "molit_building_hub_access_denied",
            "SERVICE_KEY_IS_NOT_REGISTERED_ERROR": "molit_building_hub_key_unregistered",
            "DEADLINE_HAS_EXPIRED_ERROR": "molit_building_hub_key_expired",
            "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": "molit_building_hub_quota_exceeded",
        }
        for marker, expected in cases.items():
            with self.subTest(marker=marker):
                body = f"<resultMsg>{marker}</resultMsg><secret>must-not-escape</secret>"
                reason = official_building_registry._building_hub_failure_reason(403, body)
                self.assertEqual(reason, expected)
                self.assertNotIn("must-not-escape", reason)

    def test_building_hub_json_header_rejection_keeps_specific_gateway_reason(self):
        payload = {
            "response": {
                "header": {
                    "resultCode": "30",
                    "resultMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
                }
            }
        }
        with self.assertRaisesRegex(
            official_building_registry.OfficialBuildingRegistryError,
            "molit_building_hub_key_unregistered",
        ):
            official_building_registry._as_records(payload)

    async def test_building_hub_200_xml_gateway_failure_keeps_specific_reason(self):
        response = FakeResponse(
            status_code=200,
            text=(
                "<resultMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</resultMsg>"
                "<secret>must-not-escape</secret>"
            ),
        )
        with patch.object(
            official_building_registry.httpx,
            "AsyncClient",
            return_value=FakeAsyncClient(response),
        ):
            with self.assertLogs(official_building_registry.LOGGER, level="WARNING") as logs:
                with self.assertRaisesRegex(
                    official_building_registry.OfficialBuildingRegistryError,
                    "molit_building_hub_key_unregistered",
                ):
                    await official_building_registry._fetch_title_records({}, "server-only-key")

        logged = "\n".join(logs.output)
        self.assertNotIn("must-not-escape", logged)
        self.assertNotIn("server-only-key", logged)

    async def test_building_hub_valid_json_record_may_contain_error_marker_text(self):
        payload = {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                "body": {"items": {"item": [{"bldNm": "INVALID_KEY PLAZA"}]}},
            }
        }
        response = FakeResponse(status_code=200, text=str(payload), json_data=payload)
        with patch.object(
            official_building_registry.httpx,
            "AsyncClient",
            return_value=FakeAsyncClient(response),
        ):
            records = await official_building_registry._fetch_title_records({}, "server-only-key")

        self.assertEqual(records, [{"bldNm": "INVALID_KEY PLAZA"}])

    def test_malformed_registry_payload_is_a_typed_failure(self):
        with self.assertRaisesRegex(
            official_building_registry.OfficialBuildingRegistryError,
            "molit_building_hub_invalid_response",
        ):
            official_building_registry._as_records("not-a-registry-object")

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
                official_building_registry.os.environ,
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
                official_building_registry.os.environ,
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
