#!/usr/bin/env python3
"""
Seed the runtime footprint cache with practical operating locations.

Harness principle:
- keep VWorld as primary source
- only add cache entries as fallback seeds
- do not delete existing cache entries
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from building_footprint import cache_building_footprint, _lookup_osm_fallback_sync  # noqa: E402


SEED_POINTS = [
    {"name": "인천 계양산", "lat": 37.558056, "lon": 126.708333},
    {"name": "풍무역", "lat": 37.6121200, "lon": 126.7326100},
    {"name": "아라한강갑문", "lat": 37.5987817, "lon": 126.8003011},
    {"name": "루원e-편한세상하늘채 121동 인근", "lat": 37.519335289490975, "lon": 126.67143176681867},
]


def main() -> int:
    seeded = []
    skipped = []

    for point in SEED_POINTS:
        result = _lookup_osm_fallback_sync(point["lat"], point["lon"])
        if not result or not result.get("available"):
            skipped.append({**point, "reason": "no_osm_building"})
            continue

        properties = result.get("properties") or {}
        if not properties.get("name"):
            properties["name"] = point["name"]

        cache_building_footprint(
            point["lat"],
            point["lon"],
            result.get("geometry") or [],
            properties=properties,
            source="seed_script",
        )
        seeded.append({
            "name": point["name"],
            "lat": point["lat"],
            "lon": point["lon"],
            "property_keys": sorted(list(properties.keys()))[:10],
            "geometry_points": len(result.get("geometry") or []),
        })

    print(json.dumps({"seeded": seeded, "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
