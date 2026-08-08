import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi.testclient import TestClient
import httpx


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402


def _lonlat_ring(points):
    return [list(main._mercator_to_lonlat(x, y)) for x, y in points]


class _BridgeTransportFailureClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        raise httpx.ConnectError("bridge unavailable")


class _BridgeStatusResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class _BridgeStatusClient:
    def __init__(self, status_code):
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return _BridgeStatusResponse(self.status_code)


class CanyonWidthRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        main.CANYON_EVIDENCE_CACHE.clear()
        self.target_ring = _lonlat_ring(
            [[0.0, -42.0], [20.0, -42.0], [20.0, -12.0], [0.0, -12.0], [0.0, -42.0]]
        )
        self.opposing_ring = _lonlat_ring(
            [[2.0, 15.0], [22.0, 15.0], [22.0, 44.0], [2.0, 44.0], [2.0, 15.0]]
        )
        self.same_side_ring = _lonlat_ring(
            [[30.0, -40.0], [50.0, -40.0], [50.0, -15.0], [30.0, -15.0], [30.0, -40.0]]
        )
        self.road = {
            "available": True,
            "official_available": True,
            "width_m": 49.7,
            "lane_count": 10,
            "road_name": "세종대로",
            "source": "official_road_right_of_way",
            "source_chain": ["vworld_wfs", "official_road_right_of_way", "lt_l_n3a0020000"],
            "geometry_paths": [[[-40.0, 0.0], [80.0, 0.0]]],
            "geometry_receipt": True,
        }
        self.target = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "geometry": self.target_ring,
            "properties": {"bd_mgt_sn": "target", "buld_nm": "대상건물"},
            "display_name": "대상건물",
            "source_chain": ["vworld_wfs"],
        }
        self.target_lon, self.target_lat = main._mercator_to_lonlat(10.0, -20.0)
        self.selection_id = "9d88e3aa-17c7-4b75-b7a0-a6db69498ca4"
        self.selection_lookup = patch.object(
            main,
            "_lookup_building_selection",
            AsyncMock(
                return_value={
                    "building_selection": {
                        "selection_id": self.selection_id,
                        "status": "official_verified",
                        "native_feature_id": "target",
                    }
                }
            ),
        )
        self.selection_lookup.start()
        self.addCleanup(self.selection_lookup.stop)

    def _params(self):
        return {
            "lat": self.target_lat,
            "lon": self.target_lon,
            "selection_id": self.selection_id,
        }

    def _assert_unavailable_receipt_bound_to_selection(self, payload):
        self.assertFalse(payload["available"])
        self.assertFalse(payload["official_available"])
        self.assertIsNone(payload["facade_gap_m"])
        self.assertIsNone(payload["effective_canyon_width_m"])
        self.assertEqual(payload["selection_id"], self.selection_id)
        self.assertEqual(payload["receipt"]["selection_id"], self.selection_id)

    def test_route_returns_verified_facade_gap_separately_from_official_right_of_way(self):
        collection = {
            "available": True,
            "official_available": True,
            "source_chain": ["vworld_wfs"],
            "features": [
                {"id": "target", "name": "대상건물", "ring": self.target_ring},
                {"id": "same-side", "name": "같은편", "ring": self.same_side_ring},
                {"id": "opposite-side", "name": "맞은편", "ring": self.opposing_ring},
            ],
        }

        with (
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=self.road)),
            patch.object(main, "lookup_building_footprint", AsyncMock(side_effect=AssertionError("canyon lookup must use the official collection only"))),
            patch.object(main, "lookup_official_building_collection", AsyncMock(return_value=collection)),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["available"])
        self.assertTrue(payload["official_available"])
        self.assertEqual(payload["facade_gap_m"], 27.0)
        self.assertNotEqual(payload["facade_gap_m"], 49.7)
        self.assertEqual(payload["official_road_right_of_way_width_m"], 49.7)
        self.assertEqual(payload["opposing_building"]["id"], "opposite-side")
        self.assertEqual(payload["receipt"]["kind"], "official_canyon_width")
        self.assertTrue(payload["receipt"]["road_crossing_verified"])
        self.assertEqual(payload["source"], "direct_vworld_official_receipt")
        self.assertEqual(payload["selection_id"], self.selection_id)
        self.assertEqual(payload["receipt"]["selection_id"], self.selection_id)
        self.assertEqual(
            set(payload["receipt"]["receipt_sources"].values()),
            {"direct_vworld_official_receipt"},
        )
        self.assertEqual(set(payload["receipt"]["receipt_ids"]), {
            "facade_gap",
            "opposing_geometry",
            "road_crossing",
            "road_geometry",
            "target_geometry",
        })
        for receipt_id in payload["receipt"]["receipt_ids"].values():
            UUID(receipt_id)

    def test_route_uses_only_a_fully_receipted_official_gis_bridge_result(self):
        bridge_result = {
            "available": True,
            "official_available": True,
            "facade_gap_m": 27.0,
            "effective_canyon_width_m": 27.0,
            "official_road_right_of_way_width_m": 49.7,
            "source": "official_gis_bridge_receipt",
            "source_chain": ["vworld_wfs", "official_building_collection", "official_canyon_width", "official_gis_bridge_receipt"],
            "selection_id": self.selection_id,
            "road_crossing_verified": True,
            "target_building": {
                "id": "target",
                "geometry_receipt": True,
                "native_feature_id": "target",
            },
            "opposing_building": {"id": "opposite-side", "geometry_receipt": True},
            "receipt": {
                "kind": "official_canyon_width",
                "selection_id": self.selection_id,
                "target_native_feature_id": "target",
                "target_geometry_receipt": True,
                "opposing_geometry_receipt": True,
                "road_geometry_receipt": True,
                "road_crossing_verified": True,
                "receipt_ids": {
                    "target_geometry": "d09405f8-c168-5ba7-b928-5102ed0a0d44",
                    "opposing_geometry": "ec3eff5c-dda4-55b5-b659-865865b8c3b6",
                    "road_geometry": "1c342204-fb71-5448-ad27-e7298cf93647",
                    "road_crossing": "8bf5b12e-436a-5889-a72f-4ff6d950f98c",
                    "facade_gap": "4cce213b-98ba-5118-ad95-8e52c084c72b",
                },
                "receipt_sources": {
                    "target_geometry": "official_gis_bridge_receipt",
                    "opposing_geometry": "official_gis_bridge_receipt",
                    "road_geometry": "official_gis_bridge_receipt",
                    "road_crossing": "official_gis_bridge_receipt",
                    "facade_gap": "official_gis_bridge_receipt",
                },
            },
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=bridge_result)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(side_effect=AssertionError("verified bridge result must avoid duplicate road lookup"))),
            patch.object(main, "lookup_official_building_collection", AsyncMock(side_effect=AssertionError("verified bridge result must avoid duplicate building lookup"))),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self.assertTrue(payload["official_available"])
        self.assertEqual(payload["facade_gap_m"], 27.0)
        self.assertEqual(payload["official_road_right_of_way_width_m"], 49.7)
        self.assertIn("official_gis_bridge", payload["source_chain"])
        self.assertEqual(payload["selection_id"], self.selection_id)
        self.assertEqual(payload["receipt"]["selection_id"], self.selection_id)
        self.assertEqual(payload["source"], "official_gis_bridge_receipt")

    def test_route_rejects_an_incomplete_bridge_receipt_without_direct_promotion(self):
        incomplete_bridge_result = {
            "available": True,
            "official_available": True,
            "facade_gap_m": 49.7,
            "source": "official_gis_bridge_receipt",
            "source_chain": ["vworld_wfs"],
            "selection_id": self.selection_id,
            "road_crossing_verified": True,
            "target_building": {
                "id": "target",
                "geometry_receipt": True,
                "native_feature_id": "target",
            },
            "opposing_building": {"id": "opposite-side", "geometry_receipt": True},
            "receipt": {
                "kind": "official_canyon_width",
                "selection_id": self.selection_id,
                "target_native_feature_id": "target",
                "target_geometry_receipt": True,
                "opposing_geometry_receipt": False,
                "road_geometry_receipt": True,
                "road_crossing_verified": True,
            },
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=incomplete_bridge_result)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(side_effect=AssertionError("incomplete bridge receipts must not be upgraded"))),
            patch.object(main, "lookup_official_building_collection", AsyncMock(side_effect=AssertionError("incomplete bridge receipts must not be upgraded"))),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self._assert_unavailable_receipt_bound_to_selection(payload)
        self.assertEqual(payload["reason"], "official_gis_bridge_incomplete_receipt_set")

    def test_route_holds_when_a_bridge_receipt_names_a_different_target_under_the_same_uuid(self):
        bridge_result = {
            "available": True,
            "official_available": True,
            "facade_gap_m": 27.0,
            "effective_canyon_width_m": 27.0,
            "source": "official_gis_bridge_receipt",
            "source_chain": ["vworld_wfs", "official_building_collection", "official_canyon_width", "official_gis_bridge_receipt"],
            "selection_id": self.selection_id,
            "road_crossing_verified": True,
            "target_building": {
                "id": "different-target",
                "geometry_receipt": True,
                "native_feature_id": "different-target",
            },
            "opposing_building": {"id": "opposite-side", "geometry_receipt": True},
            "receipt": {
                "kind": "official_canyon_width",
                "selection_id": self.selection_id,
                "target_native_feature_id": "different-target",
                "target_geometry_receipt": True,
                "opposing_geometry_receipt": True,
                "road_geometry_receipt": True,
                "road_crossing_verified": True,
                "receipt_ids": {
                    "target_geometry": "d09405f8-c168-5ba7-b928-5102ed0a0d44",
                    "opposing_geometry": "ec3eff5c-dda4-55b5-b659-865865b8c3b6",
                    "road_geometry": "1c342204-fb71-5448-ad27-e7298cf93647",
                    "road_crossing": "8bf5b12e-436a-5889-a72f-4ff6d950f98c",
                    "facade_gap": "4cce213b-98ba-5118-ad95-8e52c084c72b",
                },
                "receipt_sources": {
                    "target_geometry": "official_gis_bridge_receipt",
                    "opposing_geometry": "official_gis_bridge_receipt",
                    "road_geometry": "official_gis_bridge_receipt",
                    "road_crossing": "official_gis_bridge_receipt",
                    "facade_gap": "official_gis_bridge_receipt",
                },
            },
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=bridge_result)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(side_effect=AssertionError("mismatched target must not use direct fallback"))),
            patch.object(main, "lookup_official_building_collection", AsyncMock(side_effect=AssertionError("mismatched target must not use direct fallback"))),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self._assert_unavailable_receipt_bound_to_selection(payload)
        self.assertEqual(payload["reason"], "canyon_target_identifier_mismatch")

    def test_route_uses_direct_official_fallback_after_bridge_vworld_upstream_failure(self):
        collection = {
            "available": True,
            "official_available": True,
            "source_chain": ["vworld_wfs"],
            "features": [
                {"id": "target", "name": "대상건물", "ring": self.target_ring},
                {"id": "opposite-side", "name": "맞은편", "ring": self.opposing_ring},
            ],
        }
        bridge_hold = {
            "available": False,
            "official_available": False,
            "facade_gap_m": None,
            "source": "official_canyon_width_unavailable",
            "source_chain": ["vworld_wfs", "official_canyon_width_unavailable"],
            "reason": "building_upstream_status_502",
            "receipt": {
                "kind": "official_canyon_width_unavailable",
                "target_geometry_receipt": False,
                "opposing_geometry_receipt": False,
                "road_geometry_receipt": False,
                "road_crossing_verified": False,
            },
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=bridge_hold)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=self.road)),
            patch.object(main, "lookup_official_building_collection", AsyncMock(return_value=collection)),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["official_available"])
        self.assertEqual(payload["facade_gap_m"], 27.0)
        self.assertEqual(payload["bridge_fallback_reason"], "building_upstream_status_502")
        self.assertEqual(payload["bridge_provider"], "official_gis_bridge")
        self.assertEqual(payload["source"], "direct_vworld_official_receipt")
        self.assertEqual(payload["receipt"]["selection_id"], self.selection_id)
        self.assertEqual(
            set(payload["receipt"]["receipt_sources"].values()),
            {"direct_vworld_official_receipt"},
        )

    def test_route_holds_when_bridge_crossing_evidence_mismatches_receipt(self):
        crossing_mismatch = {
            "available": True,
            "official_available": True,
            "facade_gap_m": 49.7,
            "effective_canyon_width_m": 49.7,
            "source": "official_gis_bridge_receipt",
            "source_chain": ["vworld_wfs", "official_canyon_width"],
            "selection_id": self.selection_id,
            "road_crossing_verified": False,
            "target_building": {
                "id": "target",
                "geometry_receipt": True,
                "native_feature_id": "target",
            },
            "opposing_building": {"id": "opposite-side", "geometry_receipt": True},
            "receipt": {
                "kind": "official_canyon_width",
                "selection_id": self.selection_id,
                "target_native_feature_id": "target",
                "target_geometry_receipt": True,
                "opposing_geometry_receipt": True,
                "road_geometry_receipt": True,
                "road_crossing_verified": True,
                "receipt_ids": {
                    "target_geometry": "d09405f8-c168-5ba7-b928-5102ed0a0d44",
                    "opposing_geometry": "ec3eff5c-dda4-55b5-b659-865865b8c3b6",
                    "road_geometry": "1c342204-fb71-5448-ad27-e7298cf93647",
                    "road_crossing": "8bf5b12e-436a-5889-a72f-4ff6d950f98c",
                    "facade_gap": "4cce213b-98ba-5118-ad95-8e52c084c72b",
                },
                "receipt_sources": {
                    "target_geometry": "official_gis_bridge_receipt",
                    "opposing_geometry": "official_gis_bridge_receipt",
                    "road_geometry": "official_gis_bridge_receipt",
                    "road_crossing": "official_gis_bridge_receipt",
                    "facade_gap": "official_gis_bridge_receipt",
                },
            },
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=crossing_mismatch)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(side_effect=AssertionError("crossing mismatch must not be upgraded"))),
            patch.object(main, "lookup_official_building_collection", AsyncMock(side_effect=AssertionError("crossing mismatch must not be upgraded"))),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self._assert_unavailable_receipt_bound_to_selection(payload)
        self.assertEqual(payload["reason"], "official_gis_bridge_crossing_mismatch")

    def test_route_preserves_bridge_failure_when_direct_fallback_is_unavailable(self):
        bridge_hold = {
            "available": False,
            "official_available": False,
            "facade_gap_m": None,
            "source": "official_canyon_width_unavailable",
            "source_chain": ["vworld_wfs", "official_canyon_width_unavailable"],
            "reason": "road_upstream_status_502",
            "upstream_attempts": [
                {"source_origin": "vworld_map_wfs", "outcome": "upstream_status_502"},
                {"source_origin": "vworld_api_wfs", "outcome": "upstream_status_502"},
            ],
            "receipt": {
                "kind": "official_canyon_width_unavailable",
                "target_geometry_receipt": False,
                "opposing_geometry_receipt": False,
                "road_geometry_receipt": False,
                "road_crossing_verified": False,
            },
        }
        direct_road_hold = {
            "available": False,
            "official_available": False,
            "width_m": None,
            "lane_count": None,
            "road_name": None,
            "source": "official_road_right_of_way_unavailable",
            "source_chain": ["vworld_wfs", "official_road_right_of_way_unavailable"],
            "reason": "network_error",
        }
        collection_hold = {
            "available": False,
            "official_available": False,
            "features": [],
            "source": "vworld_wfs",
            "source_chain": ["vworld_wfs"],
            "reason": "official_building_collection_request_failed",
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=bridge_hold)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=direct_road_hold)),
            patch.object(main, "lookup_official_building_collection", AsyncMock(return_value=collection_hold)),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["official_available"])
        self.assertIsNone(payload["effective_canyon_width_m"])
        self.assertEqual(payload["reason"], "network_error")
        self.assertEqual(payload["bridge_fallback_reason"], "road_upstream_status_502")
        self.assertEqual(payload["bridge_provider"], "official_gis_bridge")
        self.assertEqual(
            payload["bridge_upstream_attempts"],
            [
                {"source_origin": "vworld_map_wfs", "outcome": "upstream_status_502"},
                {"source_origin": "vworld_api_wfs", "outcome": "upstream_status_502"},
            ],
        )

    def test_route_holds_when_the_configured_bridge_transport_fails(self):
        bridge_hold = {
            "available": False,
            "official_available": False,
            "facade_gap_m": None,
            "effective_canyon_width_m": None,
            "official_road_right_of_way_width_m": None,
            "road_crossing_verified": False,
            "source": "official_gis_bridge_unavailable",
            "source_chain": ["official_gis_bridge_unavailable"],
            "reason": "official_gis_bridge_transport_error",
            "receipt": {
                "kind": "official_gis_bridge_unavailable",
                "target_geometry_receipt": False,
                "opposing_geometry_receipt": False,
                "road_geometry_receipt": False,
                "road_crossing_verified": False,
            },
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=bridge_hold)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(side_effect=AssertionError("configured bridge failure must not fall through"))),
            patch.object(main, "lookup_official_building_collection", AsyncMock(side_effect=AssertionError("configured bridge failure must not fall through"))),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["official_available"])
        self.assertIsNone(payload["facade_gap_m"])
        self.assertEqual(payload["reason"], "official_gis_bridge_transport_error")
        self.assertEqual(payload["bridge_provider"], "official_gis_bridge")
        self.assertEqual(payload["receipt"]["selection_id"], self.selection_id)

    def test_bridge_client_returns_a_safe_hold_when_transport_fails(self):
        with (
            patch.object(main, "OFFICIAL_GIS_BRIDGE_URL", "https://bridge.example.test/api/canyon-width"),
            patch.object(main, "OFFICIAL_GIS_BRIDGE_TOKEN", "server-only-token"),
            patch.object(main.httpx, "AsyncClient", _BridgeTransportFailureClient),
        ):
            payload = asyncio.run(main.fetch_official_gis_bridge_canyon_evidence(self.target_lat, self.target_lon))

        self.assertFalse(payload["available"])
        self.assertFalse(payload["official_available"])
        self.assertIsNone(payload["facade_gap_m"])
        self.assertEqual(payload["source"], "official_gis_bridge_unavailable")
        self.assertEqual(payload["reason"], "official_gis_bridge_transport_error")
        self.assertEqual(payload["receipt"]["kind"], "official_gis_bridge_unavailable")

    def test_bridge_http_401_and_502_return_typed_selection_bound_unavailable_evidence(self):
        for status_code in (401, 502):
            with self.subTest(status_code=status_code):
                main.CANYON_EVIDENCE_CACHE.clear()
                with (
                    patch.object(main, "OFFICIAL_GIS_BRIDGE_URL", "https://bridge.example.test/api/canyon-width"),
                    patch.object(main, "OFFICIAL_GIS_BRIDGE_TOKEN", "server-only-token"),
                    patch.object(
                        main.httpx,
                        "AsyncClient",
                        side_effect=lambda *args, **kwargs: _BridgeStatusClient(status_code),
                    ),
                    patch.object(main, "fetch_road_width_evidence", AsyncMock(side_effect=AssertionError("bridge HTTP failure must not fall through"))),
                    patch.object(main, "lookup_official_building_collection", AsyncMock(side_effect=AssertionError("bridge HTTP failure must not fall through"))),
                ):
                    response = self.client.get("/api/canyon-width", params=self._params())

                payload = response.json()
                self._assert_unavailable_receipt_bound_to_selection(payload)
                self.assertEqual(payload["reason"], f"official_gis_bridge_http_{status_code}")
                self.assertNotIn("server-only-token", response.text)

    def test_dedicated_bridge_requires_its_server_only_token(self):
        with patch.object(main, "OFFICIAL_GIS_BRIDGE_INBOUND_TOKEN", "bridge-secret"):
            denied = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})
            with patch.object(main, "fetch_canyon_width_evidence", AsyncMock(return_value={"available": False})):
                accepted = self.client.get(
                    "/api/canyon-width",
                    params={"lat": self.target_lat, "lon": self.target_lon},
                    headers={"Authorization": "Bearer bridge-secret"},
                )

        self.assertEqual(denied.status_code, 401)
        self.assertEqual(accepted.status_code, 200)

    def test_route_holds_when_an_opposing_official_footprint_is_not_found(self):
        collection = {
            "available": True,
            "official_available": True,
            "source_chain": ["vworld_wfs"],
            "features": [{"id": "target", "name": "대상건물", "ring": self.target_ring}],
        }

        with (
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=self.road)),
            patch.object(main, "lookup_building_footprint", AsyncMock(side_effect=AssertionError("canyon lookup must use the official collection only"))),
            patch.object(main, "lookup_official_building_collection", AsyncMock(return_value=collection)),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self.assertFalse(payload["available"])
        self.assertFalse(payload["official_available"])
        self.assertEqual(payload["reason"], "opposing_official_building_not_matched")
        self.assertIsNone(payload["facade_gap_m"])
        self.assertIsNone(payload["effective_canyon_width_m"])
        self.assertEqual(payload["official_road_right_of_way_width_m"], 49.7)
        self.assertEqual(payload["receipt"]["selection_id"], self.selection_id)
        self.assertNotEqual(payload["facade_gap_m"], 49.7)

    def test_road_inventory_only_is_context_and_never_official_canyon_width(self):
        unavailable_collection = {
            "available": False,
            "official_available": False,
            "features": [],
            "source_chain": ["vworld_wfs", "official_building_collection_unavailable"],
            "reason": "official_building_collection_not_matched",
        }
        with (
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=self.road)),
            patch.object(main, "lookup_official_building_collection", AsyncMock(return_value=unavailable_collection)),
        ):
            response = self.client.get("/api/canyon-width", params=self._params())

        payload = response.json()
        self._assert_unavailable_receipt_bound_to_selection(payload)
        self.assertEqual(payload["official_road_right_of_way_width_m"], 49.7)
        self.assertEqual(payload["reason"], "official_building_collection_not_matched")
        self.assertNotEqual(payload["facade_gap_m"], 49.7)

    def test_route_caches_only_the_verified_official_facade_gap(self):
        collection = {
            "available": True,
            "official_available": True,
            "source_chain": ["vworld_wfs"],
            "features": [
                {"id": "target", "name": "대상건물", "ring": self.target_ring},
                {"id": "opposite-side", "name": "맞은편", "ring": self.opposing_ring},
            ],
        }
        road_lookup = AsyncMock(return_value=self.road)
        building_lookup = AsyncMock(return_value=collection)

        with (
            patch.object(main, "fetch_road_width_evidence", road_lookup),
            patch.object(main, "lookup_official_building_collection", building_lookup),
        ):
            first = self.client.get("/api/canyon-width", params=self._params())
            second = self.client.get("/api/canyon-width", params=self._params())

        self.assertTrue(first.json()["official_available"])
        self.assertEqual(second.json()["facade_gap_m"], 27.0)
        self.assertEqual(road_lookup.await_count, 1)
        self.assertEqual(building_lookup.await_count, 1)


if __name__ == "__main__":
    unittest.main()
