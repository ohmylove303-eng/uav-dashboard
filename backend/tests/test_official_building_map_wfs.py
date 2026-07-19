import asyncio
import json
import unittest
from unittest.mock import patch

import building_footprint


class OfficialBuildingMapWfsTests(unittest.TestCase):
    def test_map_wfs_collection_uses_mercator_and_returns_wgs84_rings(self):
        payload = {
            "type": "FeatureCollection",
            "features": [{
                "id": "lt_c_spbd.1",
                "properties": {"bd_mgt_sn": "building-1", "buld_nm": "Official Building"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[14134600.0, 4518500.0], [14134620.0, 4518500.0], [14134620.0, 4518520.0], [14134600.0, 4518520.0], [14134600.0, 4518500.0]]],
                },
            }],
        }
        captured = {}

        def fetch_collection(params, *args, **kwargs):
            captured["params"] = params
            captured["kwargs"] = kwargs
            return json.dumps(payload)

        with (
            patch.object(building_footprint, "_resolve_vworld_api_key", return_value="server-only-key"),
            patch.object(building_footprint, "_fetch_text_with_retries_sync", side_effect=fetch_collection),
        ):
            result = asyncio.run(building_footprint.lookup_official_building_collection(37.5663, 126.9780))

        self.assertTrue(result["official_available"])
        self.assertEqual(captured["params"]["TYPENAME"], "lt_c_spbd")
        self.assertEqual(captured["params"]["SRSNAME"], "EPSG:3857")
        self.assertEqual(captured["params"]["APIKEY"], "server-only-key")
        self.assertEqual(captured["kwargs"]["endpoint"], building_footprint.VWORLD_MAP_WFS_ENDPOINT)
        ring = result["features"][0]["ring"]
        self.assertTrue(all(120.0 < point[0] < 130.0 and 30.0 < point[1] < 40.0 for point in ring))


class OfficialBuildingClickTests(unittest.IsolatedAsyncioTestCase):
    async def test_click_lookup_uses_map_wfs_and_requires_the_clicked_building(self):
        southwest = building_footprint._lonlat_to_web_mercator(126.9778, 37.5662)
        northeast = building_footprint._lonlat_to_web_mercator(126.9782, 37.5666)
        payload = {
            "type": "FeatureCollection",
            "features": [{
                "id": "lt_c_spbd.2",
                "properties": {"bd_mgt_sn": "building-2", "buld_nm": "Official Click Building"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        list(southwest),
                        [northeast[0], southwest[1]],
                        list(northeast),
                        [southwest[0], northeast[1]],
                        list(southwest),
                    ]],
                },
            }],
        }
        captured = {}

        def fetch_collection(params, *args, **kwargs):
            captured["params"] = params
            captured["kwargs"] = kwargs
            return json.dumps(payload)

        with (
            patch.dict(building_footprint.os.environ, {}, clear=True),
            patch.object(building_footprint, "_match_cached_footprint", return_value=None),
            patch.object(building_footprint, "_resolve_vworld_api_key", return_value="server-only-key"),
            patch.object(building_footprint, "_fetch_text_with_retries_sync", side_effect=fetch_collection),
            patch.object(building_footprint, "_store_footprint_cache_entry", return_value={}),
        ):
            result = await building_footprint.lookup_building_footprint(37.5664, 126.9780)

        self.assertTrue(result["available"])
        self.assertTrue(result["official_footprint_available"])
        self.assertTrue(result["official_selection_match"])
        self.assertEqual(result["display_name"], "Official Click Building")
        self.assertEqual(result["source_origin"], "vworld_map_wfs")
        self.assertEqual(captured["params"]["SRSNAME"], "EPSG:3857")
        self.assertEqual(captured["kwargs"]["endpoint"], building_footprint.VWORLD_MAP_WFS_ENDPOINT)

    async def test_road_click_does_not_become_the_nearest_building(self):
        southwest = building_footprint._lonlat_to_web_mercator(126.9790, 37.5662)
        northeast = building_footprint._lonlat_to_web_mercator(126.9794, 37.5666)
        payload = {
            "features": [{
                "properties": {"buld_nm": "Nearby Official Building"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        list(southwest),
                        [northeast[0], southwest[1]],
                        list(northeast),
                        [southwest[0], northeast[1]],
                        list(southwest),
                    ]],
                },
            }],
        }

        with (
            patch.dict(building_footprint.os.environ, {}, clear=True),
            patch.object(building_footprint, "_match_cached_footprint", return_value=None),
            patch.object(building_footprint, "_resolve_vworld_api_key", return_value="server-only-key"),
            patch.object(building_footprint, "_fetch_text_with_retries_sync", return_value=json.dumps(payload)),
            patch.object(building_footprint, "_lookup_osm_fallback_sync", side_effect=AssertionError("road clicks must not fall back to OSM buildings")),
        ):
            result = await building_footprint.lookup_building_footprint(37.5664, 126.9780)

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "no_official_building_at_click")


if __name__ == "__main__":
    unittest.main()
