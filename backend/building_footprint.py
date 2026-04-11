"""
VWorld WFS building footprint lookup helper.

This module is designed for server-side use because VWorld development keys
require a matching Referer header and the browser cannot call the WFS endpoint
directly due to CORS restrictions.
"""

from __future__ import annotations

import asyncio
import http.client
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional


VWORLD_WFS_ENDPOINT = "https://api.vworld.kr/req/wfs"
OVERPASS_ENDPOINT = os.getenv("OVERPASS_ENDPOINT", "https://overpass-api.de/api/interpreter")
DEFAULT_SEARCH_RADIUS_M = 40
DEFAULT_MAX_FEATURES = 25
DEFAULT_VWORLD_REFERER = "https://uav-vercel.vercel.app/"
DEFAULT_VWORLD_TYPENAME = "lt_c_spbd"
BUILDING_KEYWORDS = ("bldg", "build", "building", "건물", "bd")
FOOTPRINT_CACHE_PATH = os.path.join(os.path.dirname(__file__), "static", "footprint_cache.json")


def _to_radians(degrees: float) -> float:
    return degrees * math.pi / 180.0


def _build_bbox(lat: float, lon: float, radius_m: float = DEFAULT_SEARCH_RADIUS_M) -> Dict[str, float]:
    meters_per_deg_lat = 110540
    meters_per_deg_lon = 111320 * max(0.2, math.cos(_to_radians(lat)))
    lat_offset = radius_m / meters_per_deg_lat
    lon_offset = radius_m / meters_per_deg_lon
    return {
        "minLon": lon - lon_offset,
        "minLat": lat - lat_offset,
        "maxLon": lon + lon_offset,
        "maxLat": lat + lat_offset,
    }


def _format_bbox_for_wfs(bbox: Dict[str, float]) -> str:
    # VWorld WFS expects this lat/lon ordering for EPSG:4326.
    return f"{bbox['minLat']},{bbox['minLon']},{bbox['maxLat']},{bbox['maxLon']},EPSG:4326"


def _average_ring_center(ring: Iterable[Iterable[float]]) -> Optional[Dict[str, float]]:
    coords = list(ring)
    if not coords:
        return None
    lon_sum = 0.0
    lat_sum = 0.0
    count = 0
    for point in coords:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        lon_sum += float(point[0])
        lat_sum += float(point[1])
        count += 1
    if count == 0:
        return None
    return {"lon": lon_sum / count, "lat": lat_sum / count}


def _get_polygon_ring(feature: Dict[str, Any]) -> Optional[List[List[float]]]:
    geometry = (feature or {}).get("geometry") or {}
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []

    if geometry_type == "Polygon":
        ring = coordinates[0] if coordinates else None
        return ring if isinstance(ring, list) else None

    if geometry_type == "MultiPolygon":
        ring = coordinates[0][0] if coordinates and coordinates[0] else None
        return ring if isinstance(ring, list) else None

    return None


def _distance_to_point(feature: Dict[str, Any], lat: float, lon: float) -> float:
    ring = _get_polygon_ring(feature)
    if not ring:
        return float("inf")
    center = _average_ring_center(ring)
    if not center:
        return float("inf")
    return math.sqrt(((center["lat"] - lat) ** 2) + ((center["lon"] - lon) ** 2))


def _distance_to_ring(ring: Iterable[Iterable[float]], lat: float, lon: float) -> float:
    center = _average_ring_center(ring)
    if not center:
        return float("inf")
    return math.sqrt(((center["lat"] - lat) ** 2) + ((center["lon"] - lon) ** 2))


def _parse_type_names_from_capabilities(xml_text: str) -> List[str]:
    feature_blocks = re.findall(r"<FeatureType[\s\S]*?<\/FeatureType>", xml_text or "", flags=re.IGNORECASE)
    type_names: List[str] = []
    for block in feature_blocks:
        match = re.search(r"<Name>([^<]+)<\/Name>", block, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            type_names.append(match.group(1).strip())
    return type_names


def _choose_type_name(type_names: Iterable[str]) -> Optional[str]:
    best_name = None
    best_score = 0
    for type_name in type_names:
        lowered = str(type_name or "").lower()
        score = sum(len(keyword) for keyword in BUILDING_KEYWORDS if keyword in lowered)
        if score > best_score:
            best_name = type_name
            best_score = score
    return best_name


def _sanitize_properties(properties: Any) -> Dict[str, Any]:
    if not isinstance(properties, dict):
        return {}

    sanitized: Dict[str, Any] = {}
    for key, value in properties.items():
        if isinstance(value, (str, int, float, bool)):
            sanitized[str(key)] = value
        if len(sanitized) >= 20:
            break
    return sanitized


def _request_headers() -> Dict[str, str]:
    return {
        "Accept": "application/json, application/xml, text/xml, */*",
        "User-Agent": "UAV-Dash/3.0 render footprint proxy",
        "Referer": os.getenv("VWORLD_REFERER", DEFAULT_VWORLD_REFERER),
        "Connection": "close",
    }


def _fetch_text_sync(params: Dict[str, Any], timeout_s: float = 20.0) -> str:
    query = urllib.parse.urlencode(params)
    url = f"{VWORLD_WFS_ENDPOINT}?{query}"
    request = urllib.request.Request(url, headers=_request_headers(), method="GET")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.read().decode("utf-8", "replace")


def _post_text_sync(url: str, data: str, timeout_s: float = 20.0) -> str:
    encoded = data.encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "User-Agent": "UAV-Dash/3.0 overpass fallback",
            "Connection": "close",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return response.read().decode("utf-8", "replace")


def _fetch_text_with_retries_sync(
    params: Dict[str, Any],
    timeout_s: float = 20.0,
    retries: int = 3,
) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(retries):
        try:
            return _fetch_text_sync(params, timeout_s=timeout_s)
        except (urllib.error.URLError, http.client.RemoteDisconnected, ConnectionResetError, socket.timeout) as error:
            last_error = error
            if attempt == retries - 1:
                raise
            time_to_sleep = 0.6 * (attempt + 1)
            time.sleep(time_to_sleep)
    if last_error:
        raise last_error
    raise RuntimeError("footprint_fetch_failed")


def _load_footprint_cache() -> List[Dict[str, Any]]:
    try:
        with open(FOOTPRINT_CACHE_PATH, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except FileNotFoundError:
        return []
    except Exception:
        return []

    if isinstance(payload, dict):
        entries = payload.get("entries") or payload.get("features") or []
    else:
        entries = payload
    return entries if isinstance(entries, list) else []


def _write_footprint_cache(entries: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(FOOTPRINT_CACHE_PATH), exist_ok=True)
    with open(FOOTPRINT_CACHE_PATH, "w", encoding="utf-8") as fp:
        json.dump(entries, fp, ensure_ascii=False, indent=2)


def _store_footprint_cache_entry(
    lat: float,
    lon: float,
    geometry: Iterable[Iterable[float]],
    properties: Optional[Dict[str, Any]] = None,
    source: str = "manual_seed",
    dedupe_distance_deg: float = 0.00025,
) -> Dict[str, Any]:
    ring = [list(point) for point in (geometry or []) if isinstance(point, (list, tuple)) and len(point) >= 2]
    if len(ring) < 4:
        raise ValueError("invalid_geometry")

    center = _average_ring_center(ring) or {"lat": lat, "lon": lon}
    entries = _load_footprint_cache()
    updated = False

    for entry in entries:
        entry_center = entry.get("center") or {}
        try:
            entry_lat = float(entry_center.get("lat"))
            entry_lon = float(entry_center.get("lon"))
        except Exception:
            continue

        distance = math.sqrt(((entry_lat - center["lat"]) ** 2) + ((entry_lon - center["lon"]) ** 2))
        if distance <= dedupe_distance_deg:
            entry["geometry"] = ring
            entry["properties"] = _sanitize_properties(properties or {})
            entry["center"] = center
            entry["source"] = source
            updated = True
            break

    if not updated:
        entries.append({
            "center": center,
            "geometry": ring,
            "properties": _sanitize_properties(properties or {}),
            "source": source,
        })

    _write_footprint_cache(entries)
    return {
        "available": True,
        "source": "footprint_cache",
        "geometry": ring,
        "properties": _sanitize_properties(properties or {}),
    }


def _match_cached_footprint(lat: float, lon: float, max_distance_deg: float = 0.0012) -> Optional[Dict[str, Any]]:
    best_entry: Optional[Dict[str, Any]] = None
    best_distance = float("inf")

    for entry in _load_footprint_cache():
        geometry = entry.get("geometry")
        properties = entry.get("properties")
        if not isinstance(geometry, list) or len(geometry) < 4:
            continue

        center = entry.get("center")
        if isinstance(center, dict):
            center_lat = float(center.get("lat", 0.0))
            center_lon = float(center.get("lon", 0.0))
        else:
            computed_center = _average_ring_center(geometry)
            if not computed_center:
                continue
            center_lat = computed_center["lat"]
            center_lon = computed_center["lon"]

        distance = math.sqrt(((center_lat - lat) ** 2) + ((center_lon - lon) ** 2))
        if distance < best_distance:
            best_distance = distance
            best_entry = {
                "available": True,
                "source": "footprint_cache",
                "geometry": geometry,
                "properties": _sanitize_properties(properties),
            }

    if best_entry and best_distance <= max_distance_deg:
        return best_entry
    return None


def _osm_query(lat: float, lon: float, radius_m: float = 60.0) -> str:
    return f"""
[out:json][timeout:20];
(
  way(around:{radius_m:.0f},{lat},{lon})["building"];
  relation(around:{radius_m:.0f},{lat},{lon})["building"];
);
out geom tags qt;
""".strip()


def _normalize_osm_ring(element: Dict[str, Any]) -> Optional[List[List[float]]]:
    geometry = element.get("geometry") or []
    if not isinstance(geometry, list) or len(geometry) < 3:
        return None

    ring: List[List[float]] = []
    for point in geometry:
        if not isinstance(point, dict):
            continue
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None:
            continue
        ring.append([float(lon), float(lat)])

    if len(ring) < 3:
        return None

    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring if len(ring) >= 4 else None


def _lookup_osm_fallback_sync(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    try:
        query = urllib.parse.urlencode({"data": _osm_query(lat, lon)})
        payload_text = _post_text_sync(OVERPASS_ENDPOINT, query, timeout_s=20.0)
        payload = json.loads(payload_text)
    except Exception:
        return None

    elements = payload.get("elements") if isinstance(payload, dict) else []
    if not isinstance(elements, list):
        return None

    candidates: List[Dict[str, Any]] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        ring = _normalize_osm_ring(element)
        if not ring:
            continue
        candidates.append({
            "geometry": ring,
            "properties": _sanitize_properties(element.get("tags") or {}),
        })

    if not candidates:
        return None

    nearest = min(candidates, key=lambda item: _distance_to_ring(item["geometry"], lat, lon))
    return {
        "available": True,
        "source": "osm_fallback",
        "geometry": nearest["geometry"],
        "properties": nearest["properties"],
    }


async def lookup_building_footprint(lat: float, lon: float) -> Dict[str, Any]:
    api_key = os.getenv("VWORLD_API_KEY")
    preferred_type_name = os.getenv("VWORLD_WFS_TYPENAME")

    cached_match = _match_cached_footprint(lat, lon)
    osm_fallback = await asyncio.to_thread(_lookup_osm_fallback_sync, lat, lon)
    if osm_fallback and osm_fallback.get("available"):
        try:
            _store_footprint_cache_entry(
                lat,
                lon,
                osm_fallback.get("geometry") or [],
                properties=osm_fallback.get("properties"),
                source="osm_fallback",
            )
        except Exception:
            pass

    if not api_key:
        return cached_match or osm_fallback or {
            "available": False,
            "source": "vworld_wfs",
            "reason": "missing_vworld_api_key",
        }

    try:
        type_name = preferred_type_name or DEFAULT_VWORLD_TYPENAME

        if not preferred_type_name:
            capabilities_text = await asyncio.to_thread(
                _fetch_text_with_retries_sync,
                {
                    "SERVICE": "WFS",
                    "REQUEST": "GetCapabilities",
                    "VERSION": "1.1.0",
                    "key": api_key,
                },
            )
            type_names = _parse_type_names_from_capabilities(capabilities_text)
            type_name = _choose_type_name(type_names) or DEFAULT_VWORLD_TYPENAME

        if not type_name:
            return {
                "available": False,
                "source": "vworld_wfs",
                "reason": "no_building_typename_detected",
            }

        bbox = _build_bbox(lat, lon)
        payload_text = await asyncio.to_thread(
            _fetch_text_with_retries_sync,
            {
                "SERVICE": "WFS",
                "REQUEST": "GetFeature",
                "VERSION": "1.1.0",
                "key": api_key,
                "typeName": type_name,
                "maxFeatures": str(DEFAULT_MAX_FEATURES),
                "srsName": "EPSG:4326",
                "outputFormat": "application/json",
                "bbox": _format_bbox_for_wfs(bbox),
            },
        )
        try:
            payload = json.loads(payload_text)
        except ValueError:
            return {
                "available": False,
                "source": "vworld_wfs",
                "typeName": type_name,
                "reason": "feature_request_failed",
                "detail": payload_text[:400],
            }
        features = payload.get("features") if isinstance(payload, dict) else []
        if not isinstance(features, list):
            features = []

        polygon_features = [
            feature for feature in features
            if isinstance(feature, dict) and (_get_polygon_ring(feature) or [])
        ]
        if not polygon_features:
            return cached_match or osm_fallback or {
                "available": False,
                "source": "vworld_wfs",
                "typeName": type_name,
                "reason": "no_polygon_feature_found",
            }

        nearest = min(polygon_features, key=lambda feature: _distance_to_point(feature, lat, lon))
        geometry = _get_polygon_ring(nearest)
        if not geometry or len(geometry) < 4:
            return cached_match or osm_fallback or {
                "available": False,
                "source": "vworld_wfs",
                "typeName": type_name,
                "reason": "no_polygon_feature_found",
            }

        result = {
            "available": True,
            "source": "vworld_wfs",
            "typeName": type_name,
            "geometry": geometry,
            "properties": _sanitize_properties(nearest.get("properties")),
        }
        try:
            _store_footprint_cache_entry(
                lat,
                lon,
                result["geometry"],
                properties=result.get("properties"),
                source="vworld_wfs",
            )
        except Exception:
            pass
        return result
    except urllib.error.HTTPError as error:
        try:
            detail = error.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = str(error)
        return cached_match or osm_fallback or {
            "available": False,
            "source": "vworld_wfs",
            "reason": "feature_request_failed",
            "detail": detail,
        }
    except Exception as error:
        return cached_match or osm_fallback or {
            "available": False,
            "source": "vworld_wfs",
            "reason": "unexpected_error",
            "detail": str(error),
        }


def cache_building_footprint(lat: float, lon: float, geometry: Iterable[Iterable[float]], properties: Optional[Dict[str, Any]] = None, source: str = "manual_seed") -> Dict[str, Any]:
    return _store_footprint_cache_entry(lat, lon, geometry, properties=properties, source=source)
