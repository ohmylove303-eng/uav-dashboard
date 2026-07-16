from pathlib import Path
import sys
import unittest
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import building_footprint  # noqa: E402


class BuildingKeyResolutionTests(unittest.TestCase):
    def test_server_only_data_key_is_preferred_for_official_wfs_lookup(self):
        with patch.dict(
            building_footprint.os.environ,
            {
                "VITE_VWORLD_API_KEY": "browser-key",
                "VWORLD_DATA_API_KEY": "server-only-key",
            },
            clear=True,
        ):
            self.assertEqual(building_footprint._resolve_vworld_api_key(), "server-only-key")


if __name__ == "__main__":
    unittest.main()
