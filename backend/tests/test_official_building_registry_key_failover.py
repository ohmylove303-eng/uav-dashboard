from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import official_building_registry  # noqa: E402
import official_building_hub_client  # noqa: E402


class OfficialBuildingRegistryKeyFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_click_uses_an_approved_alias_when_the_primary_alias_is_stale(self):
        # Given: Render contains both a stale preferred alias and an older approved alias.
        footprint = {
            "available": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "source": "vworld_wfs",
            "source_chain": ["vworld_wfs"],
            "display_name": "서울특별시청",
            "properties": {
                "bd_mgt_sn": "1114010300100310000019224",
                "buld_nm": "서울특별시청",
            },
        }
        registry_row = {
            "bldNm": "서울특별시청",
            "heit": "41.65",
            "grndFlrCnt": "6",
            "bcRat": "52.4",
            "vlRat": "318.7",
        }

        async def fetch_records(_query, service_key):
            if service_key == "stale-key":
                raise official_building_registry.OfficialBuildingRegistryError(
                    "molit_building_hub_key_unregistered"
                )
            return [registry_row]

        with (
            patch.dict(
                official_building_hub_client.os.environ,
                {
                    "MOLIT_BUILDING_HUB_SERVICE_KEY": "stale-key",
                    "MOLIT_BUILDING_HUB_API_KEY": "approved-key",
                },
                clear=True,
            ),
            patch.object(
                official_building_registry,
                "_fetch_title_records",
                AsyncMock(side_effect=fetch_records),
            ) as fetch,
        ):
            # When: a verified building click is enriched.
            result = await official_building_registry.enrich_verified_footprint(footprint)

        # Then: the approved configured alias supplies the official registry values.
        self.assertEqual(result["registry_status"], "official_verified")
        self.assertEqual(result["properties"]["buld_hg"], 41.65)
        self.assertEqual(
            [call.args[1] for call in fetch.await_args_list],
            ["stale-key", "approved-key"],
        )


if __name__ == "__main__":
    unittest.main()
