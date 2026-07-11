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
from typing import Any, Dict, Iterable, List, Optional, Tuple


VWORLD_WFS_ENDPOINT = "https://api.vworld.kr/req/wfs"
OVERPASS_ENDPOINTS = [
    endpoint.strip()
    for endpoint in (
        os.getenv("OVERPASS_ENDPOINTS")
        or os.getenv("OVERPASS_ENDPOINT")
        or "https://overpass-api.de/api/interpreter,https://lz4.overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter"
    ).split(",")
    if endpoint.strip()
]
DEFAULT_SEARCH_RADIUS_M = 40
DEFAULT_MAX_FEATURES = 25
DEFAULT_VWORLD_REFERER = "http://localhost:8000/"
DEFAULT_VWORLD_TYPENAME = "lt_c_spbd"
BUILDING_KEYWORDS = ("bldg", "build", "building", "건물", "bd")
FOOTPRINT_CACHE_PATH = os.path.join(os.path.dirname(__file__), "static", "footprint_cache.json")
VWORLD_ENV_FILE_CANDIDATES = [
    os.getenv("VWORLD_ENV_FILE"),
    os.path.join(os.path.dirname(__file__), "cctv-vworld.env"),
    os.path.join(os.path.dirname(__file__), "..", "cctv-vworld.env"),
    os.path.join(os.path.dirname(__file__), "..", "..", "uav-vercel", "cctv-vworld.env"),
    os.path.join(os.path.dirname(__file__), "..", "..", "cctv-vworld.env"),
]
SOURCE_STATUS_OFFICIAL_VERIFIED = "official_verified"
SOURCE_STATUS_ESTIMATED = "estimated"
SOURCE_STATUS_UNVERIFIED_CACHE = "unverified_cache"
SOURCE_STATUS_UNAVAILABLE = "unavailable"
FIELD_SOURCE_KEYS: Dict[str, Tuple[str, ...]] = {
    "building_name": ("buld_nm", "display_name", "name", "name:ko", "label", "building_name"),
    "height_m": ("buld_hg", "height", "height_m", "estimated_height_m"),
    "floor_count": ("gro_flo_co", "building:levels", "levels", "floors", "floor_count", "estimated_floors"),
    "zoning_type": ("zoning_type", "landuse"),
    "far_percent": ("far_percent",),
    "bcr_percent": ("bcr_percent",),
}


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


def _point_in_polygon(lon: float, lat: float, ring: Iterable[Iterable[float]]) -> bool:
    coords = list(ring or [])
    if len(coords) < 4:
        return False

    inside = False
    j = len(coords) - 1
    for i in range(len(coords)):
        xi, yi = coords[i][0], coords[i][1]
        xj, yj = coords[j][0], coords[j][1]
        intersects = ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


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


def _load_env_file(path: str) -> Dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as fp:
            text = fp.read()
    except Exception:
        return {}

    values: Dict[str, str] = {}
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        if "=" not in trimmed:
            continue
        key, value = trimmed.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _resolve_vworld_api_key() -> Optional[str]:
    for key_name in ("VWORLD_API_KEY", "NEXT_PUBLIC_VWORLD_API_KEY", "VITE_VWORLD_API_KEY", "VITE_VWORLD_3D_API_KEY", "VITE_VWORLD_KEY"):
        value = os.getenv(key_name)
        if value:
            return value

    for candidate in VWORLD_ENV_FILE_CANDIDATES:
        if not candidate:
            continue
        values = _load_env_file(candidate)
        for key_name in ("VWORLD_API_KEY", "NEXT_PUBLIC_VWORLD_API_KEY", "VITE_VWORLD_API_KEY", "VITE_VWORLD_3D_API_KEY", "VITE_VWORLD_KEY"):
            value = values.get(key_name)
            if value:
                return value

    return None


def _is_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().lower() not in {"정보없음", "미상", "unknown", "null", "none"}
    return True


def _property_richness(properties: Any) -> int:
    if not isinstance(properties, dict):
        return 0

    score = 0
    name_keys = ("name", "display_name", "buld_nm", "buld_nm_dc", "name:ko", "label")
    address_keys = ("addr:housenumber", "addr:street", "address", "fullAddress", "formatted_address", "sido", "sigungu", "gu", "rd_nm")
    structure_keys = ("building", "building:levels", "levels", "floors", "floor_count", "height", "height_m", "buld_hg", "gro_flo_co", "und_flo_co")
    planning_keys = ("landuse", "zoning_type", "far_percent", "bcr_percent", "usage", "use")

    if any(_is_meaningful_value(properties.get(key)) for key in name_keys):
        score += 8
    if any(_is_meaningful_value(properties.get(key)) for key in address_keys):
        score += 5
    if any(_is_meaningful_value(properties.get(key)) for key in structure_keys):
        score += 4
    if any(_is_meaningful_value(properties.get(key)) for key in planning_keys):
        score += 3

    extras = sum(1 for value in properties.values() if _is_meaningful_value(value))
    score += min(extras, 6)
    return score


def _normalize_building_label(value: Any) -> str:
    if not _is_meaningful_value(value):
        return ""
    return str(value).strip()


def _is_generic_building_label(value: Any) -> bool:
    label = _normalize_building_label(value)
    if not label:
        return True

    lowered = label.lower()
    generic_literals = {
        "미상",
        "미상 건물",
        "미상 건물 주변",
        "선택된 건물",
        "정보없음",
        "unknown",
        "null",
        "none",
        "도로명 미상",
        "주소 정보 없음",
        "주변",
    }
    if lowered in generic_literals:
        return True
    if "주변" in label and not any(token in label for token in ("아파트", "단지", "타워", "오피스", "주상복합", "푸르지오", "자이", "래미안", "힐스테이트", "아이파크", "캐슬", "더샵", "e편한세상")):
        return True
    if re.fullmatch(r"\d+\s*동(?:\s*주변)?", label):
        return True
    if re.fullmatch(r"\d+\s*호(?:\s*주변)?", label):
        return True
    if re.fullmatch(r"\d+\s*층(?:\s*주변)?", label):
        return True
    return False


def _extract_display_name(properties: Any) -> Optional[str]:
    if not isinstance(properties, dict):
        return None

    for key in ("buld_nm", "display_name", "name", "name:ko", "label", "building_name"):
        candidate = _normalize_building_label(properties.get(key))
        if candidate and not _is_generic_building_label(candidate):
            return candidate
    return None


def _source_origin_rank(origin: Optional[str]) -> int:
    origin = (origin or "").lower()
    if origin == "vworld_wfs":
        return 4
    if origin == "vworld_feature_info":
        return 3
    if origin == "footprint_cache":
        return 2
    if origin.startswith("frontend_"):
        return 1
    if origin == "osm_fallback":
        return 0
    return 0


def _find_display_name_candidate(lat: float, lon: float, max_distance_deg: float = 0.0012) -> Optional[Dict[str, Any]]:
    best_candidate: Optional[Dict[str, Any]] = None
    best_score: Optional[tuple] = None

    for entry in _load_footprint_cache():
        geometry = entry.get("geometry")
        if not isinstance(geometry, list) or len(geometry) < 4:
            continue

        properties = _sanitize_properties(entry.get("properties"))
        display_name = _extract_display_name(properties)
        if not display_name:
            continue

        origin = entry.get("source_origin") or entry.get("source")
        origin_rank = _source_origin_rank(origin)
        if origin_rank < 3:
            continue

        center = entry.get("center")
        if isinstance(center, dict):
            try:
                center_lat = float(center.get("lat", 0.0))
                center_lon = float(center.get("lon", 0.0))
            except Exception:
                computed_center = _average_ring_center(geometry)
                if not computed_center:
                    continue
                center_lat = computed_center["lat"]
                center_lon = computed_center["lon"]
        else:
            computed_center = _average_ring_center(geometry)
            if not computed_center:
                continue
            center_lat = computed_center["lat"]
            center_lon = computed_center["lon"]

        distance = math.sqrt(((center_lat - lat) ** 2) + ((center_lon - lon) ** 2))
        if distance > max_distance_deg * 2:
            continue

        score = (
            origin_rank,
            1 if properties.get("buld_nm") else 0,
            _property_richness(properties),
            -distance,
        )
        if best_score is None or score > best_score:
            best_score = score
            best_candidate = {
                "display_name": display_name,
                "display_name_source": entry.get("source") or origin,
                "display_name_source_origin": origin,
                "display_name_source_chain": [entry.get("source") or origin or "footprint_cache"],
                "display_name_distance_m": round(distance * 111000, 1),
            }

    return best_candidate


def _source_rank(source: Optional[str]) -> int:
    source = (source or "").lower()
    if source == "vworld_wfs":
        return 4
    if source == "footprint_cache":
        return 3
    if source == "osm_fallback":
        return 2
    if source.startswith("frontend_"):
        return 1
    return 0


def _source_penalty(source: Optional[str]) -> float:
    source = (source or "").lower()
    if source == "vworld_wfs":
        return 0.0
    if source == "footprint_cache":
        return 0.0
    if source == "osm_fallback":
        return 0.0015
    if source == "frontend_context_seed":
        return 0.0020
    if source.startswith("frontend_"):
        return 0.0014
    return 0.0008


def _merge_properties(primary: Any, fallback: Any) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}

    for source in (fallback, primary):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if _is_meaningful_value(value):
                merged[str(key)] = value

    return merged


def _normalized_source_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _first_property_value(properties: Any, keys: Tuple[str, ...]) -> Tuple[Optional[str], Any]:
    if not isinstance(properties, dict):
        return None, None

    for key in keys:
        value = properties.get(key)
        if _is_meaningful_value(value):
            return key, value
    return None, None


def _has_validated_vworld_cache_receipt(payload: Dict[str, Any]) -> bool:
    source = _normalized_source_token(payload.get("source"))
    origin = _normalized_source_token(payload.get("source_origin")) or source
    if origin == "vworld_wfs":
        return True
    if _normalized_source_token(payload.get("validated_source")) == "vworld_wfs":
        return True

    validation = payload.get("validation")
    if isinstance(validation, dict):
        validation_source = _normalized_source_token(validation.get("source") or validation.get("verified_source"))
        if validation_source == "vworld_wfs":
            return True
        if (
            validation_source == "vworld_wfs"
            and any(
                _is_meaningful_value(validation.get(key))
                for key in ("validated_at", "verified_at", "timestamp", "receipt")
            )
        ):
            return True

    if any(
        _is_meaningful_value(payload.get(key))
        for key in ("vworld_receipt", "raw_vworld_feature", "wfs_receipt", "validation_receipt")
    ) and source == "vworld_wfs":
        return True

    if any(
        _is_meaningful_value(payload.get(key))
        for key in ("validated_at", "verified_at", "validation_timestamp", "receipt_validated_at")
    ):
        supporting_source = _normalized_source_token(
            payload.get("official_footprint_source")
            or payload.get("render_source")
            or payload.get("building_source")
            or payload.get("source")
        )
        if supporting_source == "vworld_wfs":
            return True

    return False


def _classify_footprint_source(payload: Dict[str, Any]) -> Dict[str, Any]:
    source = _normalized_source_token(payload.get("source")) or "vworld_wfs"
    available = bool(payload.get("available", True))
    validated_cache = source == "footprint_cache" and _has_validated_vworld_cache_receipt(payload)

    if not available:
        return {
            "status": SOURCE_STATUS_UNAVAILABLE,
            "public_source": source,
            "official_footprint_available": False,
            "cache_receipt_validated": validated_cache,
        }

    if source == "vworld_wfs":
        return {
            "status": SOURCE_STATUS_OFFICIAL_VERIFIED,
            "public_source": "vworld_wfs",
            "official_footprint_available": True,
            "cache_receipt_validated": False,
        }

    if source == "footprint_cache":
        return {
            "status": SOURCE_STATUS_OFFICIAL_VERIFIED if validated_cache else SOURCE_STATUS_UNVERIFIED_CACHE,
            "public_source": "vworld_cache_receipt" if validated_cache else "unverified_cache",
            "official_footprint_available": validated_cache,
            "cache_receipt_validated": validated_cache,
        }

    if source == "osm_fallback":
        return {
            "status": SOURCE_STATUS_ESTIMATED,
            "public_source": "osm_fallback",
            "official_footprint_available": False,
            "cache_receipt_validated": False,
        }

    return {
        "status": SOURCE_STATUS_ESTIMATED,
        "public_source": source,
        "official_footprint_available": False,
        "cache_receipt_validated": False,
    }


def _build_field_source(status: str, source: str, property_key: Optional[str], value: Any) -> Dict[str, Any]:
    return {
        "status": status,
        "source": source,
        "property_key": property_key,
        "value": value,
    }


def _build_uniform_field_sources(properties: Any, status: str, source: str) -> Dict[str, Dict[str, Any]]:
    field_sources: Dict[str, Dict[str, Any]] = {}
    for field_name, keys in FIELD_SOURCE_KEYS.items():
        property_key, value = _first_property_value(properties, keys)
        field_sources[field_name] = _build_field_source(
            status if property_key is not None else SOURCE_STATUS_UNAVAILABLE,
            source,
            property_key,
            value,
        )
    return field_sources


def _build_mixed_field_sources(
    live_properties: Any,
    cached_properties: Any,
    cached_status: str,
    cached_source: str,
) -> Dict[str, Dict[str, Any]]:
    field_sources: Dict[str, Dict[str, Any]] = {}
    for field_name, keys in FIELD_SOURCE_KEYS.items():
        live_key, live_value = _first_property_value(live_properties, keys)
        if live_key is not None:
            field_sources[field_name] = _build_field_source(
                SOURCE_STATUS_OFFICIAL_VERIFIED,
                "vworld_wfs",
                live_key,
                live_value,
            )
            continue

        cached_key, cached_value = _first_property_value(cached_properties, keys)
        if cached_key is not None:
            field_sources[field_name] = _build_field_source(
                cached_status,
                cached_source,
                cached_key,
                cached_value,
            )
            continue

        field_sources[field_name] = _build_field_source(
            SOURCE_STATUS_UNAVAILABLE,
            "vworld_wfs",
            None,
            None,
        )
    return field_sources


def _has_official_building_data(field_sources: Any) -> bool:
    if not isinstance(field_sources, dict):
        return False

    statuses = [
        metadata.get("status")
        for field_name, metadata in field_sources.items()
        if field_name != "building_name"
        and isinstance(metadata, dict)
        and metadata.get("status") != SOURCE_STATUS_UNAVAILABLE
    ]
    return bool(statuses) and all(status == SOURCE_STATUS_OFFICIAL_VERIFIED for status in statuses)


def _footprint_confidence_for_source(source: Optional[str]) -> float:
    source = (source or "").lower()
    if source == "vworld_wfs":
        return 0.96
    if source == "footprint_cache":
        return 0.84
    if source == "osm_fallback":
        return 0.68
    return 0.5


def _annotate_footprint_result(
    result: Dict[str, Any],
    source_chain: Optional[Iterable[str]] = None,
    profile_source: Optional[str] = None,
    source_origin: Optional[str] = None,
) -> Dict[str, Any]:
    payload = dict(result)
    source = payload.get("source") or "vworld_wfs"
    origin = source_origin or payload.get("source_origin") or source
    payload["source_origin"] = origin
    classified = _classify_footprint_source(payload)
    public_source = classified["public_source"]
    chain = list(source_chain) if source_chain is not None else [source]
    cleaned_chain = []
    seen = set()
    for item in chain:
        token = str(item).strip()
        if _normalized_source_token(token) == "footprint_cache":
            token = public_source
        if not token or token in seen:
            continue
        cleaned_chain.append(token)
        seen.add(token)
    confidence = _footprint_confidence_for_source(origin) if payload.get("available", True) else 0.0
    payload["raw_source"] = source
    payload["source"] = public_source
    payload["source_chain"] = cleaned_chain or [public_source]
    payload["profile_source"] = profile_source or source
    payload["source_status"] = classified["status"]
    payload["official_footprint_available"] = classified["official_footprint_available"]
    payload["cache_receipt_validated"] = classified["cache_receipt_validated"]
    payload["confidence"] = confidence
    payload["building_confidence"] = confidence
    payload["field_sources"] = payload.get("field_sources") or _build_uniform_field_sources(
        payload.get("properties"),
        classified["status"],
        public_source,
    )
    payload["official_building_data"] = bool(payload.get("official_building_data", False)) or _has_official_building_data(
        payload["field_sources"]
    )
    payload["stale_cache"] = bool(payload.get("stale_cache", False))
    return payload


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


def _post_text_with_endpoint_fallback_sync(
    urls: List[str],
    data: str,
    timeout_s: float = 20.0,
) -> str:
    last_error: Optional[Exception] = None
    for url in urls:
        try:
            return _post_text_sync(url, data, timeout_s=timeout_s)
        except Exception as error:
            last_error = error
            continue
    if last_error:
        raise last_error
    raise RuntimeError("overpass_request_failed")


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
            existing_props = _sanitize_properties(entry.get("properties"))
            incoming_props = _sanitize_properties(properties or {})
            existing_score = _property_richness(existing_props)
            incoming_score = _property_richness(incoming_props)
            if incoming_score >= existing_score:
                entry["properties"] = _merge_properties(incoming_props, existing_props)
            else:
                entry["properties"] = _merge_properties(existing_props, incoming_props)
            entry["geometry"] = ring
            entry["center"] = center
            if _source_rank(source) >= _source_rank(entry.get("source")):
                entry["source"] = source
            if source:
                entry["source_origin"] = source
            updated = True
            break

    if not updated:
        entries.append({
            "center": center,
            "geometry": ring,
            "properties": _sanitize_properties(properties or {}),
            "source": source,
            "source_origin": source,
        })

    _write_footprint_cache(entries)
    return _annotate_footprint_result({
        "available": True,
        "source": "footprint_cache",
        "geometry": ring,
        "properties": _sanitize_properties(properties or {}),
    }, source_chain=["footprint_cache"], profile_source="cache", source_origin=source)


def _match_cached_footprint(lat: float, lon: float, max_distance_deg: float = 0.0012) -> Optional[Dict[str, Any]]:
    best_entry: Optional[Dict[str, Any]] = None
    best_score: Optional[tuple] = None

    for entry in _load_footprint_cache():
        geometry = entry.get("geometry")
        properties = _sanitize_properties(entry.get("properties"))
        if not isinstance(geometry, list) or len(geometry) < 4:
            continue

        center = entry.get("center")
        if isinstance(center, dict):
            try:
                center_lat = float(center.get("lat", 0.0))
                center_lon = float(center.get("lon", 0.0))
            except Exception:
                computed_center = _average_ring_center(geometry)
                if not computed_center:
                    continue
                center_lat = computed_center["lat"]
                center_lon = computed_center["lon"]
        else:
            computed_center = _average_ring_center(geometry)
            if not computed_center:
                continue
            center_lat = computed_center["lat"]
            center_lon = computed_center["lon"]

        distance = math.sqrt(((center_lat - lat) ** 2) + ((center_lon - lon) ** 2))
        richness = _property_richness(properties)
        source_rank = _source_rank(entry.get("source"))
        point_in_polygon = _point_in_polygon(lon, lat, geometry)
        candidate_distance = 0.0 if point_in_polygon else distance
        adjusted_distance = candidate_distance + _source_penalty(entry.get("source")) - min(richness, 24) * 0.0001
        candidate_score = (adjusted_distance, -richness, -source_rank, candidate_distance)

        if best_score is None or candidate_score < best_score:
            best_score = candidate_score
            best_entry = {
                "available": True,
                "source": "footprint_cache",
                "geometry": geometry,
                "properties": properties,
                "source_origin": entry.get("source_origin") or entry.get("source"),
            }

    if best_entry and best_score is not None:
        best_distance = best_score[3]
        best_richness = -best_score[1]
        best_source_rank = -best_score[2]
        allowed_distance = max_distance_deg
        if best_richness >= 8 or best_source_rank >= 3:
            allowed_distance = max_distance_deg * 2
        if best_distance <= allowed_distance or best_distance == 0.0:
            display_name_candidate = _find_display_name_candidate(lat, lon, max_distance_deg=max_distance_deg)
            if display_name_candidate:
                best_entry["display_name"] = display_name_candidate.get("display_name")
                best_entry["display_name_source"] = display_name_candidate.get("display_name_source")
                best_entry["display_name_source_origin"] = display_name_candidate.get("display_name_source_origin")
                best_entry["display_name_source_chain"] = display_name_candidate.get("display_name_source_chain")
                best_entry["display_name_distance_m"] = display_name_candidate.get("display_name_distance_m")
            return _annotate_footprint_result(
                best_entry,
                source_chain=["footprint_cache"],
                profile_source="cache",
                source_origin=best_entry.get("source_origin")
            )
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


def _lookup_osm_fallback_sync(lat: float, lon: float, radius_m: float = 60.0) -> Optional[Dict[str, Any]]:
    try:
        query = urllib.parse.urlencode({"data": _osm_query(lat, lon, radius_m=radius_m)})
        payload_text = _post_text_with_endpoint_fallback_sync(OVERPASS_ENDPOINTS, query, timeout_s=20.0)
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
    return _annotate_footprint_result({
        "available": True,
        "source": "osm_fallback",
        "geometry": nearest["geometry"],
        "properties": nearest["properties"],
    }, source_chain=["osm_fallback"], profile_source="fallback", source_origin="osm_fallback")


async def lookup_building_footprint(lat: float, lon: float) -> Dict[str, Any]:
    api_key = _resolve_vworld_api_key()
    preferred_type_name = os.getenv("VWORLD_WFS_TYPENAME")

    cached_match = _match_cached_footprint(lat, lon)
    osm_fallback = await asyncio.to_thread(_lookup_osm_fallback_sync, lat, lon)
    if cached_match and "source_status" not in cached_match:
        cached_match = _annotate_footprint_result(
            cached_match,
            source_chain=cached_match.get("source_chain") or ["footprint_cache"],
            profile_source=cached_match.get("profile_source") or "cache",
            source_origin=cached_match.get("source_origin") or cached_match.get("raw_source") or cached_match.get("source"),
        )
    if osm_fallback and "source_status" not in osm_fallback:
        osm_fallback = _annotate_footprint_result(
            osm_fallback,
            source_chain=osm_fallback.get("source_chain") or ["osm_fallback"],
            profile_source=osm_fallback.get("profile_source") or "fallback",
            source_origin=osm_fallback.get("source_origin") or osm_fallback.get("raw_source") or osm_fallback.get("source"),
        )
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
        return cached_match or osm_fallback or _annotate_footprint_result({
            "available": False,
            "source": "vworld_wfs",
            "reason": "missing_vworld_api_key",
        }, source_chain=["vworld_wfs"], profile_source="wfs", source_origin="vworld_wfs")

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
            return _annotate_footprint_result({
                "available": False,
                "source": "vworld_wfs",
                "reason": "no_building_typename_detected",
            }, source_chain=["vworld_wfs"], profile_source="wfs", source_origin="vworld_wfs")

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
            return _annotate_footprint_result({
                "available": False,
                "source": "vworld_wfs",
                "typeName": type_name,
                "reason": "feature_request_failed",
                "detail": payload_text[:400],
            }, source_chain=["vworld_wfs"], profile_source="wfs", source_origin="vworld_wfs")
        features = payload.get("features") if isinstance(payload, dict) else []
        if not isinstance(features, list):
            features = []

        polygon_features = [
            feature for feature in features
            if isinstance(feature, dict) and (_get_polygon_ring(feature) or [])
        ]
        if not polygon_features:
            return cached_match or osm_fallback or _annotate_footprint_result({
                "available": False,
                "source": "vworld_wfs",
                "typeName": type_name,
                "reason": "no_polygon_feature_found",
            }, source_chain=["vworld_wfs"], profile_source="wfs", source_origin="vworld_wfs")

        nearest = min(polygon_features, key=lambda feature: _distance_to_point(feature, lat, lon))
        geometry = _get_polygon_ring(nearest)
        if not geometry or len(geometry) < 4:
            return cached_match or osm_fallback or _annotate_footprint_result({
                "available": False,
                "source": "vworld_wfs",
                "typeName": type_name,
                "reason": "no_polygon_feature_found",
            }, source_chain=["vworld_wfs"], profile_source="wfs", source_origin="vworld_wfs")

        live_properties = _sanitize_properties(nearest.get("properties"))
        result_properties = live_properties
        source_chain = ["vworld_wfs"]
        cached_field_sources: Optional[Dict[str, Dict[str, Any]]] = None
        if cached_match:
            cached_props = _sanitize_properties(cached_match.get("properties"))
            if _property_richness(cached_props) > _property_richness(result_properties):
                result_properties = _merge_properties(cached_props, result_properties)
            else:
                result_properties = _merge_properties(result_properties, cached_props)
            source_chain.append(cached_match.get("source") or "unverified_cache")
            cached_field_sources = _build_mixed_field_sources(
                live_properties,
                cached_props,
                cached_match.get("source_status", SOURCE_STATUS_UNVERIFIED_CACHE),
                cached_match.get("source") or "unverified_cache",
            )

        result = _annotate_footprint_result({
            "available": True,
            "source": "vworld_wfs",
            "typeName": type_name,
            "geometry": geometry,
            "properties": result_properties,
            "field_sources": cached_field_sources,
        }, source_chain=source_chain, profile_source="wfs", source_origin="vworld_wfs")
        display_name = _extract_display_name(result_properties)
        if display_name:
            result["display_name"] = display_name
            result["display_name_source"] = "vworld_wfs"
            result["display_name_source_origin"] = "vworld_wfs"
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
        return cached_match or osm_fallback or _annotate_footprint_result({
            "available": False,
            "source": "vworld_wfs",
            "reason": "feature_request_failed",
            "detail": detail,
        }, source_chain=["vworld_wfs"], profile_source="wfs", source_origin="vworld_wfs")
    except Exception as error:
        return cached_match or osm_fallback or _annotate_footprint_result({
            "available": False,
            "source": "vworld_wfs",
            "reason": "unexpected_error",
            "detail": str(error),
        }, source_chain=["vworld_wfs"], profile_source="wfs", source_origin="vworld_wfs")


def cache_building_footprint(lat: float, lon: float, geometry: Iterable[Iterable[float]], properties: Optional[Dict[str, Any]] = None, source: str = "manual_seed") -> Dict[str, Any]:
    return _store_footprint_cache_entry(lat, lon, geometry, properties=properties, source=source)
