from pathlib import Path
import sys
import unittest
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import official_building_hub_client  # noqa: E402


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


class OfficialBuildingHubClientTests(unittest.IsolatedAsyncioTestCase):
    def test_existing_building_hub_key_alias_is_recognized(self):
        with patch.dict(
            official_building_hub_client.os.environ,
            {"MOLIT_BUILDING_HUB_API_KEY": "server-only-key"},
            clear=True,
        ):
            self.assertTrue(official_building_hub_client.service_key_configured())
            self.assertEqual(
                official_building_hub_client._resolve_service_key(), "server-only-key"
            )

    def test_semantic_legacy_building_hub_key_alias_is_recognized(self):
        with patch.dict(
            official_building_hub_client.os.environ,
            {"MOLIT_BLDG_HUB_CREDENTIAL_KEY": "server-only-key"},
            clear=True,
        ):
            self.assertTrue(official_building_hub_client.service_key_configured())
            self.assertEqual(
                official_building_hub_client._resolve_service_key(), "server-only-key"
            )

    def test_encoded_data_go_service_key_is_normalized_before_request(self):
        normalized_key = official_building_hub_client._normalize_service_key(
            "abc%2Bdef%2Fghi%3D"
        )

        self.assertEqual(normalized_key, "abc+def/ghi=")

    def test_invalid_data_go_service_key_shape_is_rejected_without_echo(self):
        normalized_key = official_building_hub_client._normalize_service_key("invalid%ZZ key")

        self.assertIsNone(normalized_key)

    def test_building_hub_upstream_status_is_classified_without_response_body(self):
        for status in (401, 403, 502):
            with self.subTest(status=status):
                self.assertEqual(
                    official_building_hub_client._building_hub_upstream_status_reason(status),
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
                reason = official_building_hub_client._building_hub_failure_reason(403, body)
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
            official_building_hub_client.OfficialBuildingRegistryError,
            "molit_building_hub_key_unregistered",
        ):
            official_building_hub_client._as_records(payload)

    async def test_building_hub_200_xml_gateway_failure_keeps_specific_reason(self):
        response = FakeResponse(
            status_code=200,
            text=(
                "<resultMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</resultMsg>"
                "<secret>must-not-escape</secret>"
            ),
        )
        with patch.object(
            official_building_hub_client.httpx,
            "AsyncClient",
            return_value=FakeAsyncClient(response),
        ):
            with self.assertLogs(official_building_hub_client.LOGGER, level="WARNING") as logs:
                with self.assertRaisesRegex(
                    official_building_hub_client.OfficialBuildingRegistryError,
                    "molit_building_hub_key_unregistered",
                ):
                    await official_building_hub_client._fetch_title_records(
                        {}, "server-only-key"
                    )

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
            official_building_hub_client.httpx,
            "AsyncClient",
            return_value=FakeAsyncClient(response),
        ):
            records = await official_building_hub_client._fetch_title_records(
                {}, "server-only-key"
            )

        self.assertEqual(records, [{"bldNm": "INVALID_KEY PLAZA"}])

    def test_malformed_registry_payload_is_a_typed_failure(self):
        with self.assertRaisesRegex(
            official_building_hub_client.OfficialBuildingRegistryError,
            "molit_building_hub_invalid_response",
        ):
            official_building_hub_client._as_records("not-a-registry-object")


if __name__ == "__main__":
    unittest.main()
