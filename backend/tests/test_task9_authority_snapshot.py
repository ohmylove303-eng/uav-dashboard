from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tests.task7_evaluation_fixtures import (  # noqa: E402
    OTHER_SELECTION_ID,
    SELECTION_ID,
    official_canyon,
    server_building,
)
from tests.task9_authority_fixtures import (  # noqa: E402
    AuthoritySnapshotTestCase,
    verified_kma_weather,
    verified_kma_weather_with,
)


class Task9AuthoritySnapshotTests(AuthoritySnapshotTestCase):

    def test_verified_snapshot_binds_one_backend_correlation_to_every_receipt(self) -> None:
        response = self._post(verified_kma_weather())

        payload = response.json()
        correlation_id = payload["correlation_id"]
        self.assertIn(payload["final_judgment"], {"GO", "RESTRICT", "NO_GO"})
        self.assertIsInstance(payload["urban_factors"]["Fcanyon"], float)
        self.assertIsInstance(payload["ews"], float)
        self.assertTrue(correlation_id)
        self.assertEqual(payload["building_evidence"]["correlation_id"], correlation_id)
        self.assertEqual(payload["building_evidence"]["receipt"]["correlation_id"], correlation_id)
        self.assertEqual(payload["road_evidence"]["correlation_id"], correlation_id)
        self.assertEqual(payload["canyon_evidence"]["correlation_id"], correlation_id)
        self.assertEqual(payload["canyon_evidence"]["receipt"]["correlation_id"], correlation_id)
        self.assertEqual(payload["weather_evidence"]["correlation_id"], correlation_id)
        self.assertEqual(payload["weather_evidence"]["receipt"]["correlation_id"], correlation_id)
        self.assertEqual(payload["weather_evidence"]["receipt"]["selection_id"], SELECTION_ID)
        self.assertEqual(payload["weather_evidence"]["receipt"]["source"], "kma_surface_observation")
        self.assertEqual(payload["building_selection"]["correlation_id"], correlation_id)

    def test_kma_label_without_receipt_forces_hold(self) -> None:
        weather = verified_kma_weather()
        weather.pop("receipt")

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_receipt_missing")

    def test_missing_weather_freshness_forces_hold(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"].pop("observed_at_utc")

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:weather_freshness_missing")

    def test_expired_kma_receipt_forces_hold(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"]["expires_at_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:weather_receipt_expired")

    def test_kma_timeout_forces_hold(self) -> None:
        weather = verified_kma_weather()
        weather.update(
            {
                "available": False,
                "authoritative": False,
                "authority_source": None,
                "reason": "surface_weather_timeout",
                "source": "weather_unavailable",
                "source_chain": ["weather_unavailable", "surface_weather_timeout"],
            }
        )

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:surface_weather_timeout")

    def test_missing_weather_source_forces_hold(self) -> None:
        weather = verified_kma_weather()
        weather["authority_source"] = None
        weather["source"] = None
        weather["source_chain"] = []

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_untrusted")

    def test_open_meteo_client_estimate_cannot_become_authority(self) -> None:
        weather = verified_kma_weather()
        weather.update(
            {
                "authoritative": False,
                "authority_source": "open_meteo_surface",
                "source": "open_meteo_surface",
                "source_chain": ["open_meteo_surface"],
            }
        )
        weather["receipt"]["authority_source"] = "open_meteo_surface"
        weather["receipt"]["source_chain"] = ["open_meteo_surface"]

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_untrusted")
        self.assertEqual(payload["urban_factors"]["H"], 40.0)

    def test_weather_selection_correlation_mismatch_forces_hold(self) -> None:
        weather = verified_kma_weather(selection_id=OTHER_SELECTION_ID)

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:selection_snapshot_mismatch")

    def test_weather_value_receipt_mismatch_forces_hold(self) -> None:
        weather = verified_kma_weather()
        weather["wind_speed"] = 1.0

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_receipt_mismatch")

    def test_verified_snapshot_preserves_no_fly_rule(self) -> None:
        payload = self._post(verified_kma_weather(), payload_update={"no_fly_zone": True}).json()

        self.assertEqual(payload["final_judgment"], "NO_GO")
        self.assertEqual(payload["gates"][0]["status"], "NO_GO")

    def test_verified_snapshot_preserves_gps_rule(self) -> None:
        payload = self._post(
            verified_kma_weather(),
            payload_update={"gps_locked": 3, "glonass_locked": 1},
        ).json()

        self.assertEqual(payload["final_judgment"], "NO_GO")
        self.assertEqual(payload["gates"][1]["status"], "NO_GO")

    def test_verified_snapshot_preserves_visibility_rule(self) -> None:
        payload = self._post(verified_kma_weather_with(visibility=0.5)).json()

        self.assertEqual(payload["final_judgment"], "NO_GO")
        self.assertEqual(payload["gates"][2]["status"], "NO_GO")

    def test_verified_snapshot_preserves_drone_wind_rule(self) -> None:
        payload = self._post(verified_kma_weather_with(wind_speed=20.0)).json()

        self.assertEqual(payload["final_judgment"], "NO_GO")
        self.assertEqual(payload["gates"][3]["status"], "NO_GO")

    def test_verified_snapshot_preserves_drone_gust_rule(self) -> None:
        payload = self._post(verified_kma_weather_with(gust_speed=20.0)).json()

        self.assertEqual(payload["final_judgment"], "NO_GO")
        self.assertEqual(payload["gates"][4]["status"], "NO_GO")

    def test_missing_registry_height_forces_hold(self) -> None:
        payload = self._post(verified_kma_weather(), building=server_building(official_height=False)).json()

        self.assert_hold_without_exact_outputs(payload, "building:official_registry_height_missing")

    def test_invalid_facade_gap_receipt_forces_hold(self) -> None:
        canyon = official_canyon(SELECTION_ID, SELECTION_ID)
        canyon["receipt"]["receipt_ids"]["facade_gap"] = ""

        payload = self._post(verified_kma_weather(), canyon=canyon).json()

        self.assert_hold_without_exact_outputs(payload, "canyon_width:canyon_receipt_incomplete")

    def test_manual_weather_request_forces_hold_even_with_client_weather_evidence(self) -> None:
        payload = self._post(
            verified_kma_weather(),
            payload_update={
                "wind_speed": 1.0,
                "weather_evidence": {
                    "available": True,
                    "authoritative": True,
                    "source": "client_kma_estimate",
                },
            },
        ).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_untrusted")


if __name__ == "__main__":
    unittest.main()
