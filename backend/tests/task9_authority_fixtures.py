from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
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
from tests.task7_evaluation_fixtures import (  # noqa: E402
    SELECTION_ID,
    official_canyon,
    server_building,
)


LATITUDE = 37.5665
LONGITUDE = 126.9780


def verified_kma_weather(*, selection_id: Optional[str] = SELECTION_ID) -> dict:
    observed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    values = {
        "wind_speed": 5.0,
        "gust_speed": 7.5,
        "visibility": 12.0,
        "precipitation_prob": 0,
        "weather_code": 0,
    }
    receipt = {
        "kind": "kma_weather_observation",
        "receipt_id": "task9-independent-kma-fixture",
        "authority_source": "kma_surface_observation",
        "source": "kma_surface_observation",
        "source_chain": ["kma_surface_observation"],
        "stale_cache": False,
        "observed_at_utc": observed_at.isoformat(),
        "expires_at_utc": (observed_at + timedelta(hours=1)).isoformat(),
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "values": dict(values),
    }
    if selection_id is not None:
        receipt["selection_id"] = selection_id
    return {
        "available": True,
        "authoritative": True,
        "authority_source": "kma_surface_observation",
        "source": "kma_surface_observation",
        "source_chain": ["kma_surface_observation"],
        "profile_source": "surface_only",
        "stale_cache": False,
        "wind_direction": 90,
        "temperature": 20,
        "dew_point": 14,
        "humidity": 50,
        "cloud_cover": 20,
        "sunrise": "06:00",
        "sunset": "18:00",
        **values,
        "receipt": receipt,
    }


def verified_kma_weather_with(**values) -> dict:
    weather = verified_kma_weather()
    weather.update(values)
    weather["receipt"]["values"].update(values)
    return weather


class AuthoritySnapshotTestCase(unittest.TestCase):
    """HTTP evaluation harness with server-owned verified building and canyon seams."""

    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.request_payload = {
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "selection_id": SELECTION_ID,
            "building_height": 999.0,
            "street_width": 1.0,
            "building_source": "browser_estimate",
            "building_evidence": {
                "official_available": True,
                "source": "browser_estimate",
            },
        }

    def _post(
        self,
        weather: dict,
        *,
        building: Optional[dict] = None,
        canyon: Optional[dict] = None,
        payload_update: Optional[dict] = None,
    ):
        server_canyon = canyon if canyon is not None else official_canyon(SELECTION_ID, SELECTION_ID)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    main,
                    "_lookup_building_selection",
                    AsyncMock(return_value=building if building is not None else server_building()),
                )
            )
            stack.enter_context(patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather)))
            stack.enter_context(patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)))
            stack.enter_context(patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)))
            stack.enter_context(patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)))
            stack.enter_context(
                patch.object(
                    main,
                    "fetch_road_width_evidence",
                    AsyncMock(
                        return_value={
                            "available": True,
                            "official_available": True,
                            "width_m": 49.7,
                            "source": "official_road_right_of_way",
                            "source_chain": ["vworld_wfs", "official_road_right_of_way"],
                        }
                    ),
                )
            )
            stack.enter_context(patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=server_canyon)))
            payload = dict(self.request_payload)
            payload.update(payload_update or {})
            return self.client.post("/api/evaluate", json=payload)

    def assert_hold_without_exact_outputs(self, payload: dict, reason_code: str) -> None:
        self.assertEqual(payload["final_judgment"], "HOLD")
        self.assertIsNone(payload["urban_factors"]["Fcanyon"])
        self.assertIsNone(payload["urban_factors"]["Fcanyon_raw"])
        self.assertIsNone(payload["ews"])
        self.assertIsNone(payload["correlation_id"])
        self.assertIn(reason_code, payload["input_quality"]["reasons"])
