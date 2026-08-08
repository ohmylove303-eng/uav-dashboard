import json
from pathlib import Path

from tests.task7_evaluation_fixtures import OTHER_SELECTION_ID, SELECTION_ID, server_building
from tests.task9_authority_fixtures import AuthoritySnapshotTestCase, main, verified_kma_weather


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Task9WeatherReceiptAuthorityTests(AuthoritySnapshotTestCase):
    def test_canonical_api_enum_has_one_documented_display_boundary(self) -> None:
        contract_path = BACKEND_ROOT / "tests" / "uav_api_contract_matrix.json"
        contract = json.loads(contract_path.read_text())

        self.assertEqual([status.value for status in main.JudgmentLevel], ["HOLD", "GO", "RESTRICT", "NO_GO"])
        self.assertEqual(contract["evaluation_statuses"], ["GO", "RESTRICT", "NO_GO", "HOLD"])
        self.assertEqual(contract["presentation_boundary"]["api_field"], "final_judgment")
        self.assertEqual(contract["presentation_boundary"]["api_values"], ["GO", "RESTRICT", "NO_GO", "HOLD"])
        self.assertEqual(contract["presentation_boundary"]["display_labels"]["NO_GO"], "NO-GO")

    def test_missing_weather_receipt_selection_id_never_synthesizes_a_snapshot(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"].pop("selection_id")

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:weather_selection_id_missing")
        self.assertNotIn("selection_id", payload["weather_evidence"]["receipt"])

    def test_malformed_weather_receipt_selection_id_never_issues_a_correlation(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"]["selection_id"] = "not-a-uuid"

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:weather_selection_id_invalid")

    def test_weather_receipt_identity_absent_from_server_selection_snapshot_forces_hold(self) -> None:
        building = server_building()
        building["building_selection"]["selection_id"] = OTHER_SELECTION_ID

        payload = self._post(verified_kma_weather(), building=building).json()

        self.assert_hold_without_exact_outputs(payload, "weather:selection_snapshot_mismatch")

    def test_missing_weather_receipt_source_forces_hold(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"].pop("source")

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_receipt_source_missing")

    def test_missing_weather_receipt_source_chain_forces_hold(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"].pop("source_chain")

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_receipt_source_chain_missing")

    def test_open_meteo_weather_receipt_source_cannot_match_kma_envelope(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"]["source"] = "open_meteo_surface"

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_receipt_mismatch")

    def test_open_meteo_weather_receipt_chain_cannot_match_kma_envelope(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"]["source_chain"] = ["open_meteo_surface"]

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_receipt_mismatch")

    def test_non_authoritative_kma_envelope_cannot_reach_a_normal_decision(self) -> None:
        weather = verified_kma_weather()
        weather["authoritative"] = False

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:authoritative_weather_untrusted")

    def test_stale_weather_receipt_cannot_reach_a_normal_decision(self) -> None:
        weather = verified_kma_weather()
        weather["receipt"]["stale_cache"] = True

        payload = self._post(weather).json()

        self.assert_hold_without_exact_outputs(payload, "weather:weather_receipt_stale")
