from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402


def _lonlat_ring(points):
    return [list(main._mercator_to_lonlat(x, y)) for x, y in points]


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
