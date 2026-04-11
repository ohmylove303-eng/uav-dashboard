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
from typing import Any, Dict, List


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from building_footprint import cache_building_footprint, _lookup_osm_fallback_sync  # noqa: E402


SEED_TARGETS_PATH = Path(__file__).with_name("seed_targets.json")


DEFAULT_SEED_POINTS = [
    {"name": "인천 계양산", "lat": 37.558056, "lon": 126.708333, "radius_m": 60},
    {"name": "풍무역", "lat": 37.6121200, "lon": 126.7326100, "radius_m": 60},
    {"name": "아라한강갑문", "lat": 37.5987817, "lon": 126.8003011, "radius_m": 60},
    {"name": "루원e-편한세상하늘채 121동 인근", "lat": 37.519335289490975, "lon": 126.67143176681867, "radius_m": 60},
]


def load_seed_points() -> List[Dict[str, Any]]:
    if not SEED_TARGETS_PATH.exists():
        return DEFAULT_SEED_POINTS

    try:
        payload = json.loads(SEED_TARGETS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_SEED_POINTS

    if isinstance(payload, dict):
        entries = payload.get("targets") or []
    else:
        entries = payload
    return entries if isinstance(entries, list) and entries else DEFAULT_SEED_POINTS


def lookup_with_radius(point: Dict[str, Any]) -> Dict[str, Any] | None:
    candidates = [
        {"lat": point["lat"], "lon": point["lon"], "label": point["name"]},
        *[
            {
                "lat": candidate.get("lat"),
                "lon": candidate.get("lon"),
                "label": candidate.get("label") or point["name"],
            }
            for candidate in (point.get("candidate_points") or [])
            if isinstance(candidate, dict) and candidate.get("lat") is not None and candidate.get("lon") is not None
        ]
    ]

    for candidate in candidates:
        result = _lookup_osm_fallback_sync(
            float(candidate["lat"]),
            float(candidate["lon"]),
            radius_m=float(point.get("radius_m", 60)),
        )
        if result and result.get("available"):
            return {
                "result": result,
                "seed_lat": float(candidate["lat"]),
                "seed_lon": float(candidate["lon"]),
                "seed_label": candidate["label"],
            }
    return None


def main() -> int:
    seeded = []
    skipped = []
    seed_points = load_seed_points()

    for point in seed_points:
        lookup = lookup_with_radius(point)
        if not lookup:
            skipped.append({**point, "reason": "no_osm_building"})
            continue

        result = lookup["result"]

        properties = result.get("properties") or {}
        if not properties.get("name"):
            properties["name"] = lookup["seed_label"]

        cache_building_footprint(
            lookup["seed_lat"],
            lookup["seed_lon"],
            result.get("geometry") or [],
            properties=properties,
            source="seed_script",
        )
        seeded.append({
            "name": point["name"],
            "lat": lookup["seed_lat"],
            "lon": lookup["seed_lon"],
            "property_keys": sorted(list(properties.keys()))[:10],
            "geometry_points": len(result.get("geometry") or []),
            "radius_m": point.get("radius_m", 60),
        })

    print(json.dumps({"seeded": seeded, "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
