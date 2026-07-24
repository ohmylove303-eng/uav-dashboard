from pathlib import Path
import json
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import main  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    calls = []
    responses = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        self.__class__.calls.append({"url": str(url), "headers": dict(headers or {})})
        response = self.__class__.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RoadWidthRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_api_wfs_request_uses_registered_host_without_scheme_or_path(self):
        request_url = main._build_vworld_wfs_request_url(
            {"url": "https://api.vworld.kr/req/wfs", "mode": "api"},
            "1,2,3,4",
            "test-key",
            "https://uav-vercel.pages.dev/",
        )
        query = parse_qs(urlparse(request_url).query)

        self.assertEqual(query["key"], ["test-key"])
        self.assertEqual(query["domain"], ["uav-vercel.pages.dev"])

    def test_route_builds_vworld_request_and_selects_closest_official_feature(self):
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.responses = [
            _FakeResponse(
                200,
                json.dumps(
                    {
                        "response": {
                            "result": {
                                "featureCollection": {
                                    "features": [
                                        {
                                            "geometry": {
                                                "type": "LineString",
                                                "coordinates": [
                                                    [14135126.3, 4518366.4],
                                                    [14135132.3, 4518372.4],
                                                ],
                                            },
                                            "properties": {
                                                "rvwd": "14.5",
                                                "rdln": "4",
                                                "rdnm": "Sejong-daero",
                                            },
                                        },
                                        {
                                            "geometry": {
                                                "type": "LineString",
                                                "coordinates": [
                                                    [14136026.3, 4519366.4],
                                                    [14136032.3, 4519372.4],
                                                ],
                                            },
                                            "properties": {
                                                "rvwd": "21.0",
                                                "rdln": "6",
                                                "rdnm": "Far Road",
                                            },
                                        },
                                    ]
                                }
                            }
                        }
                    }
                ),
            )
        ]

        with patch.dict(
            main.os.environ,
            {"VWORLD_DATA_API_KEY": "test-key", "VWORLD_REFERER": "https://uav-vercel.pages.dev/"},
            clear=False,
        ), patch.object(main.httpx, "AsyncClient", _FakeAsyncClient):
            response = self.client.get("/api/road-width", params={"lat": 37.5665, "lon": 126.9780})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["available"])
        self.assertTrue(payload["official_available"])
        self.assertEqual(payload["width_m"], 14.5)
        self.assertEqual(payload["lane_count"], 4)
        self.assertEqual(payload["road_name"], "Sejong-daero")
        self.assertEqual(payload["source_chain"][:2], ["vworld_wfs", "official_road_right_of_way"])
        self.assertEqual(payload["official_road_right_of_way_width_m"], 14.5)
        self.assertTrue(payload["geometry_receipt"])
        self.assertEqual(payload["query_meta"]["layer"], "lt_l_n3a0020000")
        self.assertIn("propertyname=rvwd%2Crdln%2Crdnm%2Cag_geom", _FakeAsyncClient.calls[0]["url"])
        self.assertIn("BBOX=", _FakeAsyncClient.calls[0]["url"].upper())
        self.assertEqual(_FakeAsyncClient.calls[0]["headers"]["Referer"], "https://uav-vercel.pages.dev/")

    def test_route_returns_typed_unavailable_payload_for_upstream_failure(self):
        _FakeAsyncClient.calls = []
        _FakeAsyncClient.responses = [_FakeResponse(502, "bad gateway")] * 6

        with patch.dict(
            main.os.environ,
            {"VWORLD_DATA_API_KEY": "test-key", "VWORLD_REFERER": "https://uav-vercel.pages.dev/"},
            clear=False,
        ), patch.object(main.httpx, "AsyncClient", _FakeAsyncClient):
            response = self.client.get("/api/road-width", params={"lat": 37.5665, "lon": 126.9780})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["available"])
        self.assertFalse(payload["official_available"])
        self.assertEqual(payload["source"], "official_road_right_of_way_unavailable")
        self.assertEqual(payload["reason"], "upstream_status_502")
        self.assertIsNone(payload["width_m"])
        self.assertIsNone(payload["lane_count"])


if __name__ == "__main__":
    unittest.main()
