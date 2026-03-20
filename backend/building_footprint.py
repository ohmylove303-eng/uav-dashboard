"""
VWorld WFS building footprint lookup helper.

This module is designed for server-side use because VWorld development keys
require a matching Referer header and the browser cannot call the WFS endpoint
directly due to CORS restrictions.
"""

from __future__ import annotations

import math
import os
import re
from typing import Any, Dict, Iterable, List, Optional

import httpx


VWORLD_WFS_ENDPOINT = "https://api.vworld.kr/req/wfs"
DEFAULT_SEARCH_RADIUS_M = 40
DEFAULT_MAX_FEATURES = 25
DEFAULT_VWORLD_REFERER = "https://uav-vercel.vercel.app/"
DEFAULT_VWORLD_TYPENAME = "lt_c_spbd"
BUILDING_KEYWORDS = ("bldg", "build", "building", "건물", "bd")


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
    }


async def _fetch_text(client: httpx.AsyncClient, params: Dict[str, Any]) -> str:
    response = await client.get(VWORLD_WFS_ENDPOINT, params=params, headers=_request_headers())
    response.raise_for_status()
    return response.text


async def lookup_building_footprint(lat: float, lon: float) -> Dict[str, Any]:
    api_key = os.getenv("VWORLD_API_KEY")
    preferred_type_name = os.getenv("VWORLD_WFS_TYPENAME")

    if not api_key:
        return {
            "available": False,
            "source": "vworld_wfs",
            "reason": "missing_vworld_api_key",
        }

    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
        http2=False,
    ) as client:
        try:
            type_name = preferred_type_name

            if not type_name:
                capabilities_text = await _fetch_text(
                    client,
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
            response = await client.get(
                VWORLD_WFS_ENDPOINT,
                params={
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
                headers=_request_headers(),
            )
            response.raise_for_status()
            payload_text = response.text
            try:
                payload = response.json()
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
                return {
                    "available": False,
                    "source": "vworld_wfs",
                    "typeName": type_name,
                    "reason": "no_polygon_feature_found",
                }

            nearest = min(polygon_features, key=lambda feature: _distance_to_point(feature, lat, lon))
            geometry = _get_polygon_ring(nearest)
            if not geometry or len(geometry) < 4:
                return {
                    "available": False,
                    "source": "vworld_wfs",
                    "typeName": type_name,
                    "reason": "no_polygon_feature_found",
                }

            return {
                "available": True,
                "source": "vworld_wfs",
                "typeName": type_name,
                "geometry": geometry,
                "properties": _sanitize_properties(nearest.get("properties")),
            }
        except httpx.HTTPStatusError as error:
            detail = error.response.text[:400] if error.response is not None else str(error)
            return {
                "available": False,
                "source": "vworld_wfs",
                "reason": "feature_request_failed",
                "detail": detail,
            }
        except Exception as error:
            return {
                "available": False,
                "source": "vworld_wfs",
                "reason": "unexpected_error",
                "detail": str(error),
            }
