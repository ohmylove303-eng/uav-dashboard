from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402
from tests.task7_evaluation_fixtures import (  # noqa: E402
    CANYON_RECEIPT_IDS,
    CANYON_RECEIPT_SOURCES,
)


SELECTION_ID = "00000000-0000-4000-8000-000000000007"


class BuildingSelectionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def test_read_contract_builds_selection_from_verified_server_receipts(self) -> None:
        # Given: a server-selected containing VWorld feature enriched by one registry row.
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "source": "vworld_wfs", "source_origin": "vworld_map_wfs",
            "source_chain": ["vworld_wfs", "vworld_map_wfs"],
            "native_feature_id": "lt_c_spbd.7",
            "geometry": [
                [126.9778, 37.5662], [126.9781, 37.5662],
                [126.9781, 37.5665], [126.9778, 37.5662],
            ],
            "properties": {
                "bd_mgt_sn": "1114010300100310000019224",
                "buld_nm": "서울특별시청", "road_nm_addr": "서울특별시 중구 세종대로 110",
            },
            "display_name": "서울특별시청",
            "confidence": 0.96,
        }
        enriched = {
            **footprint,
            "registry_status": "official_verified",
            "registry_receipt": {
                "kind": "molit_building_hub_title",
                "record_id": "official-title-record",
                "record_count": 1, "selection": "single_record",
            },
            "field_sources": {
                "building_name": {
                    "source": "molit_building_hub",
                    "status": "official_verified",
                    "property_key": "bldNm",
                    "value": "서울특별시청",
                }
            },
        }
        with (
            patch.object(main, "lookup_building_footprint", AsyncMock(return_value=footprint)),
            patch.object(main, "enrich_verified_footprint", AsyncMock(return_value=enriched)),
        ):
            # When: the browser supplies its valid immutable selection UUID.
            response = self.client.get(
                "/api/building-footprint",
                params={"lat": 37.5662952, "lon": 126.9779451, "selection_id": SELECTION_ID},
            )

        # Then: the UUID is echoed and the selection is built only from server receipts.
        self.assertEqual(response.status_code, 200)
        selection = response.json()["building_selection"]
        self.assertEqual(selection["selection_id"], SELECTION_ID)
        self.assertEqual(selection["native_feature_id"], "lt_c_spbd.7")
        self.assertEqual(selection["bd_mgt_sn"], "1114010300100310000019224")
        self.assertEqual(selection["address"], "서울특별시 중구 세종대로 110")
        self.assertTrue(selection["official_footprint_receipt"]["point_inside"])
        self.assertEqual(selection["official_registry_receipt"]["record_id"], "official-title-record")
        self.assertEqual(selection["field_sources"]["building_name"]["source"], "molit_building_hub")

    def test_evaluate_refetches_selection_and_cannot_promote_browser_building(self) -> None:
        # Given: a browser claims official building data while the server re-fetch is unavailable.
        server_selection = {
            "available": False,
            "official_available": False,
            "reason": "no_official_building_at_click",
            "source_chain": ["vworld_wfs"],
            "selection_id": SELECTION_ID,
        }
        browser_claim = {
            "latitude": 37.5662952, "longitude": 126.9779451,
            "selection_id": SELECTION_ID,
            "building_height": 999.0,
            "building_source": "official_verified",
            "building_profile_source": "official_verified",
            "building_source_chain": ["official_verified"],
            "building_confidence": 1.0,
            "building_evidence": {
                "available": True,
                "official_available": True,
                "source_chain": ["official_verified"],
                "receipt": {
                    "kind": "official_building_height",
                    "geometry_receipt": True,
                    "selection_match": True,
                    "source_chain": ["official_verified"],
                },
            },
            "canyon_evidence": {"available": False, "official_available": False},
        }
        weather = {
            "available": False,
            "authoritative": False,
            "source": "weather_unavailable",
            "source_chain": ["weather_unavailable"],
            "stale_cache": False,
        }
        unavailable = {"available": False, "official_available": False, "source_chain": ["fixture"]}
        with (
            patch.object(main, "_lookup_building_selection", AsyncMock(return_value=server_selection)) as lookup,
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather)),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=unavailable)),
            patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=unavailable)),
        ):
            # When: evaluation receives the forged browser provenance.
            response = self.client.post("/api/evaluate", json=browser_claim)

        # Then: server lookup wins, exact outputs remain unavailable, and no correlation is issued.
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["selection_id"], SELECTION_ID)
        self.assertEqual(payload["final_judgment"], "HOLD")
        self.assertFalse(payload["building_evidence"]["official_available"])
        self.assertIsNone(payload["urban_factors"]["Fcanyon"])
        self.assertIsNone(payload["ews"])
        self.assertIsNone(payload["correlation_id"])
        lookup.assert_awaited_once_with(37.5662952, 126.9779451, SELECTION_ID)

    def test_coordinate_fallback_cannot_populate_official_selection_fields(self) -> None:
        # Given: coordinate-derived diagnostics carrying plausible exact-looking values.
        footprint = {
            "available": True,
            "official_footprint_available": False,
            "official_geometry_receipt": False,
            "official_selection_match": False,
            "source": "coordinate_based",
            "source_chain": ["coordinate_based", "osm_fallback", "overpass"],
            "confidence": 0.99,
            "properties": {
                "buld_nm": "Plausible but unverified", "buld_hg": 42.0,
                "gro_flo_co": 11, "far_percent": 318.7, "bcr_percent": 52.4,
            },
            "field_sources": {
                "building_name": {"source": "osm_fallback", "status": "estimated"},
                "height_m": {"source": "coordinate_based", "status": "estimated"},
                "floor_count": {"source": "coordinate_based", "status": "estimated"},
                "far_percent": {"source": "overpass", "status": "estimated"},
                "bcr_percent": {"source": "overpass", "status": "estimated"},
            },
        }

        # When: the backend normalizes it into the immutable selection contract.
        selection = main._build_building_selection(SELECTION_ID, footprint).model_dump()

        # Then: diagnostics remain contextual and cannot become official values or receipts.
        self.assertEqual(selection["status"], "unavailable")
        self.assertEqual(selection["fields"], {})
        self.assertEqual(selection["field_sources"], {})
        self.assertEqual(selection["confidence"], 0.0)
        self.assertIsNone(selection["official_footprint_receipt"])
        self.assertIsNone(selection["official_registry_receipt"])

    def test_registry_401_keeps_official_selection_fields_unavailable(self) -> None:
        # Given: verified geometry but a typed registry authorization failure.
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "official_footprint_receipt": {
                "kind": "vworld_building_footprint",
                "point_inside": True,
            },
            "registry_status": "unavailable",
            "registry_reason": "molit_building_hub_upstream_http_401",
            "source_chain": ["vworld_wfs"],
            "confidence": 0.96,
            "properties": {"buld_nm": "Unconfirmed", "buld_hg": 41.65},
            "field_sources": {
                "building_name": {"source": "vworld_wfs", "status": "official_verified"},
                "height_m": {"source": "vworld_wfs", "status": "official_verified"},
            },
        }

        # When: the backend creates the public selection contract.
        selection = main._build_building_selection(SELECTION_ID, footprint).model_dump()

        # Then: registry-owned exact fields stay null/unavailable despite plausible inputs.
        self.assertEqual(selection["status"], "unavailable")
        self.assertEqual(selection["reason"], "molit_building_hub_upstream_http_401")
        self.assertEqual(selection["fields"], {})
        self.assertIsNone(selection["official_registry_receipt"])

    def test_evaluate_issues_correlation_only_for_complete_server_selection(self) -> None:
        # Given: complete server-side geometry, registry, canyon, and weather receipts.
        server_footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "source": "vworld_wfs",
            "source_chain": ["vworld_wfs", "molit_building_hub"],
            "properties": {"buld_hg": 40.0, "gro_flo_co": 10},
            "field_sources": {
                "height_m": {"source": "molit_building_hub", "status": "official_verified"},
            },
            "building_selection": {
                "selection_id": SELECTION_ID,
                "status": "official_verified",
                "native_feature_id": "lt_c_spbd.7",
                "official_footprint_receipt": {
                    "kind": "vworld_building_footprint", "point_inside": True,
                },
                "official_registry_receipt": {
                    "kind": "molit_building_hub_title", "record_id": "record-7",
                },
            },
        }
        weather = {
            "available": True,
            "authoritative": True,
            "authority_source": "kma_surface_observation",
            "source": "kma_surface_observation",
            "source_chain": ["kma_surface_observation"],
            "profile_source": "surface_only",
            "stale_cache": False,
            "wind_speed": 5.0, "gust_speed": 7.5, "wind_direction": 90,
            "visibility": 12.0, "precipitation_prob": 0, "weather_code": 0,
            "temperature": 20, "dew_point": 14, "humidity": 50, "cloud_cover": 20,
            "sunrise": "06:00", "sunset": "18:00",
        }
        road = {
            "available": True, "official_available": True, "width_m": 49.7,
            "source": "official_road_right_of_way",
            "source_chain": ["vworld_wfs", "official_road_right_of_way"],
        }
        canyon = {
            "available": True, "official_available": True, "facade_gap_m": 27.0,
            "effective_canyon_width_m": 27.0,
            "selection_id": SELECTION_ID, "source": "direct_vworld_official_receipt",
            "road_crossing_verified": True,
            "source_chain": ["official_canyon_width", "direct_vworld_official_receipt"],
            "target_building": {
                "id": "target-7",
                "geometry_receipt": True,
                "native_feature_id": "lt_c_spbd.7",
            },
            "opposing_building": {"id": "opposing-7", "geometry_receipt": True},
            "receipt": {
                "kind": "official_canyon_width", "selection_id": SELECTION_ID,
                "target_native_feature_id": "lt_c_spbd.7",
                "target_geometry_receipt": True,
                "opposing_geometry_receipt": True,
                "road_geometry_receipt": True,
                "road_crossing_verified": True,
                "source_chain": ["official_canyon_width", "direct_vworld_official_receipt"],
                "receipt_ids": dict(CANYON_RECEIPT_IDS),
                "receipt_sources": dict(CANYON_RECEIPT_SOURCES),
            },
        }
        with (
            patch.object(main, "_lookup_building_selection", AsyncMock(return_value=server_footprint)),
            patch.object(main, "fetch_weather_safe", AsyncMock(return_value=weather)),
            patch.object(main, "fetch_kp_index_safe", AsyncMock(return_value=3.0)),
            patch.object(main, "fetch_kma_upper_air_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_kma_wind_profiler_profile_safe", AsyncMock(return_value=None)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=road)),
            patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value=canyon)),
        ):
            # When: evaluation re-fetches every server-owned prerequisite.
            response = self.client.post(
                "/api/evaluate",
                json={"latitude": 37.5662952, "longitude": 126.9779451, "selection_id": SELECTION_ID},
            )

        # Then: only the complete snapshot receives a backend correlation ID.
        payload = response.json()
        self.assertNotEqual(payload["final_judgment"], "HOLD")
        self.assertEqual(payload["selection_id"], SELECTION_ID)
        self.assertIsInstance(payload["correlation_id"], str)
        self.assertTrue(payload["correlation_id"])
        self.assertEqual(payload["building_selection"]["official_registry_receipt"]["record_id"], "record-7")


if __name__ == "__main__":
    unittest.main()
