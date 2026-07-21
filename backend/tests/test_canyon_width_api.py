import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

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
            response = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})

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

    def test_route_uses_only_a_fully_receipted_official_gis_bridge_result(self):
        bridge_result = {
            "available": True,
            "official_available": True,
            "facade_gap_m": 27.0,
            "effective_canyon_width_m": 27.0,
            "official_road_right_of_way_width_m": 49.7,
            "source": "official_canyon_width",
            "source_chain": ["vworld_wfs", "official_building_collection", "official_canyon_width"],
            "receipt": {
                "kind": "official_canyon_width",
                "target_geometry_receipt": True,
                "opposing_geometry_receipt": True,
                "road_geometry_receipt": True,
                "road_crossing_verified": True,
            },
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=bridge_result)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(side_effect=AssertionError("verified bridge result must avoid duplicate road lookup"))),
            patch.object(main, "lookup_official_building_collection", AsyncMock(side_effect=AssertionError("verified bridge result must avoid duplicate building lookup"))),
        ):
            response = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})

        payload = response.json()
        self.assertTrue(payload["official_available"])
        self.assertEqual(payload["facade_gap_m"], 27.0)
        self.assertEqual(payload["official_road_right_of_way_width_m"], 49.7)
        self.assertIn("official_gis_bridge", payload["source_chain"])

    def test_route_rejects_an_incomplete_bridge_receipt_and_uses_direct_evidence(self):
        collection = {
            "available": True,
            "official_available": True,
            "source_chain": ["vworld_wfs"],
            "features": [
                {"id": "target", "name": "대상건물", "ring": self.target_ring},
                {"id": "opposite-side", "name": "맞은편", "ring": self.opposing_ring},
            ],
        }
        incomplete_bridge_result = {
            "available": True,
            "official_available": True,
            "facade_gap_m": 49.7,
            "source": "official_canyon_width",
            "source_chain": ["vworld_wfs"],
            "receipt": {
                "kind": "official_canyon_width",
                "target_geometry_receipt": True,
                "opposing_geometry_receipt": False,
                "road_geometry_receipt": True,
                "road_crossing_verified": True,
            },
        }
        with (
            patch.object(main, "fetch_official_gis_bridge_canyon_evidence", AsyncMock(return_value=incomplete_bridge_result)),
            patch.object(main, "fetch_road_width_evidence", AsyncMock(return_value=self.road)),
            patch.object(main, "lookup_official_building_collection", AsyncMock(return_value=collection)),
        ):
            response = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})

        payload = response.json()
        self.assertEqual(payload["facade_gap_m"], 27.0)
        self.assertNotEqual(payload["facade_gap_m"], incomplete_bridge_result["facade_gap_m"])

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
            response = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["official_available"])
        self.assertEqual(payload["facade_gap_m"], 27.0)
        self.assertEqual(payload["bridge_fallback_reason"], "building_upstream_status_502")
        self.assertEqual(payload["bridge_provider"], "official_gis_bridge")

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
            response = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["official_available"])
        self.assertIsNone(payload["facade_gap_m"])
        self.assertEqual(payload["reason"], "official_gis_bridge_transport_error")
        self.assertEqual(payload["bridge_provider"], "official_gis_bridge")

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
            response = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})

        payload = response.json()
        self.assertFalse(payload["available"])
        self.assertFalse(payload["official_available"])
        self.assertEqual(payload["reason"], "opposing_official_building_not_matched")
        self.assertIsNone(payload["facade_gap_m"])

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
            first = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})
            second = self.client.get("/api/canyon-width", params={"lat": self.target_lat, "lon": self.target_lon})

        self.assertTrue(first.json()["official_available"])
        self.assertEqual(second.json()["facade_gap_m"], 27.0)
        self.assertEqual(road_lookup.await_count, 1)
        self.assertEqual(building_lookup.await_count, 1)


if __name__ == "__main__":
    unittest.main()
