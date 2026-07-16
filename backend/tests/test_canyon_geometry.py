from pathlib import Path
import sys
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from urban_canyon import measure_facade_gap  # noqa: E402


class UrbanCanyonGeometryTests(unittest.TestCase):
    def test_measurement_uses_opposing_building_facades_not_official_road_inventory_width(self):
        target_ring = [[0.0, -42.0], [20.0, -42.0], [20.0, -12.0], [0.0, -12.0], [0.0, -42.0]]
        road_path = [[-40.0, 0.0], [80.0, 0.0]]
        buildings = [
            {
                "id": "same-side",
                "name": "same side",
                "ring": [[30.0, -40.0], [50.0, -40.0], [50.0, -15.0], [30.0, -15.0], [30.0, -40.0]],
            },
            {
                "id": "opposite-side",
                "name": "opposite side",
                "ring": [[2.0, 15.0], [22.0, 15.0], [22.0, 44.0], [2.0, 44.0], [2.0, 15.0]],
            },
        ]

        measurement = measure_facade_gap(target_ring, road_path, buildings)

        self.assertTrue(measurement["available"])
        self.assertTrue(measurement["road_crossing_verified"])
        self.assertEqual(measurement["opposing_building_id"], "opposite-side")
        self.assertAlmostEqual(measurement["facade_gap_m"], 27.0, places=1)
        self.assertNotEqual(measurement["facade_gap_m"], 49.7)

    def test_missing_opposing_facade_returns_unavailable_instead_of_a_road_width_substitute(self):
        target_ring = [[0.0, -42.0], [20.0, -42.0], [20.0, -12.0], [0.0, -12.0], [0.0, -42.0]]
        road_path = [[-40.0, 0.0], [80.0, 0.0]]

        measurement = measure_facade_gap(target_ring, road_path, [])

        self.assertFalse(measurement["available"])
        self.assertEqual(measurement["facade_gap_m"], None)
        self.assertEqual(measurement["reason"], "opposing_official_building_not_matched")


if __name__ == "__main__":
    unittest.main()
