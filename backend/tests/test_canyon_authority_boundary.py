import asyncio
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402
from tests.task7_evaluation_fixtures import (  # noqa: E402
    OTHER_SELECTION_ID,
    SELECTION_ID,
    authoritative_weather,
    forged_client_building_fields,
    official_canyon,
    server_building,
)


class CanyonAuthorityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def _authority_stack(self, canyon_result: dict) -> tuple[ExitStack, AsyncMock]:
        stack = ExitStack()
        stack.enter_context(patch.object(main, "_lookup_building_selection", AsyncMock(return_value=server_building())))
        stack.enter_context(
            patch.object(
                main,
                "fetch_weather_safe",
                AsyncMock(return_value=authoritative_weather(selection_id=SELECTION_ID)),
            )
        )
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
        forged = official_canyon(OTHER_SELECTION_ID, OTHER_SELECTION_ID)
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
        canyon_fetch.assert_awaited_once_with(
            37.5662952,
            126.9779451,
            selection_id=SELECTION_ID,
            target_identifier={"kind": "native_feature_id", "value": "lt_c_spbd.7"},
        )
        self.assertEqual(payload["final_judgment"], "HOLD")
        self.assertFalse(payload["official_available"])
        self.assertFalse(payload["canyon_evidence"]["official_available"])
        self.assertFalse(payload["road_evidence"]["official_available"])
        self.assertNotEqual(payload["weather_evidence"]["authority_source"], "client_forged_weather")
        self.assertIsNone(payload["urban_factors"]["Fcanyon"])
        self.assertIsNone(payload["ews"])
        self.assertIsNone(payload["correlation_id"])

    def test_evaluation_request_requires_valid_selection_id_directly(self) -> None:
        invalid_cases = (
            ("absent", {}),
            ("null", {"selection_id": None}),
            ("empty", {"selection_id": ""}),
            ("invalid", {"selection_id": "not-a-uuid"}),
        )

        for case_name, selection_field in invalid_cases:
            with self.subTest(case=case_name):
                with self.assertRaises(ValidationError):
                    main.EvaluationRequest.model_validate(
                        {
                            "latitude": 37.5662952,
                            "longitude": 126.9779451,
                            **selection_field,
                        }
                    )

        request = main.EvaluationRequest.model_validate(
            {
                "latitude": 37.5662952,
                "longitude": 126.9779451,
                "selection_id": SELECTION_ID,
            }
        )
        self.assertEqual(request.selection_id, SELECTION_ID)
        stack, canyon_fetch = self._authority_stack(
            official_canyon(SELECTION_ID, SELECTION_ID)
        )
        with stack:
            response = asyncio.run(main.evaluate_flight(request))

        canyon_fetch.assert_awaited_once_with(
            37.5662952,
            126.9779451,
            selection_id=SELECTION_ID,
            target_identifier={"kind": "native_feature_id", "value": "lt_c_spbd.7"},
        )
        self.assertEqual(response.selection_id, SELECTION_ID)
        self.assertTrue(response.official_available)
        self.assertIsNotNone(response.urban_factors["Fcanyon"])
        self.assertIsNotNone(response.ews)
        self.assertIsInstance(response.correlation_id, str)

    def test_http_evaluate_rejects_missing_or_invalid_selection_id_before_fetch(self) -> None:
        invalid_cases = (
            ("absent", {}),
            ("null", {"selection_id": None}),
            ("empty", {"selection_id": ""}),
            ("invalid", {"selection_id": "not-a-uuid"}),
        )

        for case_name, selection_field in invalid_cases:
            with self.subTest(case=case_name):
                stack, canyon_fetch = self._authority_stack(official_canyon(None, None))
                with stack:
                    response = self.client.post(
                        "/api/evaluate",
                        json={
                            "latitude": 37.5662952,
                            "longitude": 126.9779451,
                            **forged_client_building_fields(),
                            "canyon_evidence": official_canyon(None, None),
                            "weather_evidence": {
                                "available": True,
                                "authoritative": True,
                                "authority_source": "client_forged_weather",
                            },
                            **selection_field,
                        },
                    )

                payload = response.json()
                self.assertEqual(response.status_code, 422)
                self.assertEqual(payload["status"], "unavailable")
                self.assertEqual(payload["reason"], "invalid_selection_id")
                self.assertFalse(payload["official_available"])
                self.assertNotIn("correlation_id", payload)
                canyon_fetch.assert_not_awaited()

    def test_server_canyon_ids_must_be_complete_and_matching(self) -> None:
        cases = (
            ("missing_object_id", None, SELECTION_ID, "canyon_selection_id_missing"),
            ("missing_receipt_id", SELECTION_ID, None, "canyon_selection_id_missing"),
            ("both_ids_missing", None, None, "canyon_selection_id_missing"),
            ("mismatched_receipt_id", SELECTION_ID, OTHER_SELECTION_ID, "canyon_selection_mismatch"),
            ("matching_ids", SELECTION_ID, SELECTION_ID, None),
        )

        for case_name, object_id, receipt_id, unavailable_reason in cases:
            with self.subTest(case=case_name):
                canyon = official_canyon(object_id, receipt_id)
                stack, canyon_fetch = self._authority_stack(canyon)

                with stack:
                    response = self.client.post(
                        "/api/evaluate",
                        json={
                            "latitude": 37.5662952,
                            "longitude": 126.9779451,
                            "selection_id": SELECTION_ID,
                        },
                    )

                payload = response.json()
                canyon_fetch.assert_awaited_once_with(
                    37.5662952,
                    126.9779451,
                    selection_id=SELECTION_ID,
                    target_identifier={"kind": "native_feature_id", "value": "lt_c_spbd.7"},
                )
                if unavailable_reason is not None:
                    self.assertEqual(payload["final_judgment"], "HOLD")
                    self.assertFalse(payload["official_available"])
                    self.assertFalse(payload["canyon_evidence"]["official_available"])
                    self.assertEqual(payload["canyon_evidence"]["reason"], unavailable_reason)
                    self.assertIsNone(payload["canyon_evidence"]["selection_id"])
                    self.assertNotIn("selection_id", payload["canyon_evidence"]["receipt"])
                    self.assertIsNone(payload["urban_factors"]["Fcanyon"])
                    self.assertIsNone(payload["ews"])
                    self.assertIsNone(payload["correlation_id"])
                else:
                    self.assertNotEqual(payload["final_judgment"], "HOLD")
                    self.assertEqual(payload["canyon_evidence"]["selection_id"], SELECTION_ID)
                    self.assertEqual(payload["canyon_evidence"]["receipt"]["selection_id"], SELECTION_ID)
                    self.assertEqual(payload["weather_evidence"]["selection_id"], SELECTION_ID)
                    self.assertEqual(payload["weather_evidence"]["receipt"]["selection_id"], SELECTION_ID)
                    self.assertEqual(payload["building_selection"]["selection_id"], SELECTION_ID)
                    self.assertIsInstance(payload["correlation_id"], str)

    def test_same_selection_uuid_with_a_different_target_building_without_an_official_identifier_holds(self) -> None:
        canyon = official_canyon(
            SELECTION_ID,
            SELECTION_ID,
            target_building_id="different-target-building",
            target_native_feature_id=None,
        )
        stack, _ = self._authority_stack(canyon)

        with stack:
            response = self.client.post(
                "/api/evaluate",
                json={
                    "latitude": 37.5662952,
                    "longitude": 126.9779451,
                    "selection_id": SELECTION_ID,
                },
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["final_judgment"], "HOLD")
        self.assertFalse(payload["canyon_evidence"]["official_available"])
        self.assertEqual(payload["canyon_evidence"]["reason"], "canyon_target_identifier_missing")
        self.assertIsNone(payload["urban_factors"]["Fcanyon"])
        self.assertIsNone(payload["correlation_id"])

    def test_missing_target_identifier_holds_even_when_the_selection_uuid_matches(self) -> None:
        canyon = official_canyon(
            SELECTION_ID,
            SELECTION_ID,
            target_native_feature_id=None,
        )
        stack, _ = self._authority_stack(canyon)

        with stack:
            response = self.client.post(
                "/api/evaluate",
                json={
                    "latitude": 37.5662952,
                    "longitude": 126.9779451,
                    "selection_id": SELECTION_ID,
                },
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["final_judgment"], "HOLD")
        self.assertFalse(payload["canyon_evidence"]["official_available"])
        self.assertEqual(payload["canyon_evidence"]["reason"], "canyon_target_identifier_missing")
        self.assertIsNone(payload["urban_factors"]["Fcanyon"])
        self.assertIsNone(payload["correlation_id"])

    def test_matching_target_identifier_allows_the_verified_facade_gap(self) -> None:
        stack, _ = self._authority_stack(official_canyon(SELECTION_ID, SELECTION_ID))

        with stack:
            response = self.client.post(
                "/api/evaluate",
                json={
                    "latitude": 37.5662952,
                    "longitude": 126.9779451,
                    "selection_id": SELECTION_ID,
                },
            )

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(payload["final_judgment"], "HOLD")
        self.assertTrue(payload["canyon_evidence"]["official_available"])
        self.assertEqual(payload["canyon_evidence"]["facade_gap_m"], 27.0)
        self.assertIsInstance(payload["correlation_id"], str)


if __name__ == "__main__":
    unittest.main()
