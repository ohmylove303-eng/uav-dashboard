"""
🚁 UAV 도시 운용판정 시스템 - FastAPI 백엔드
4중 게이트 시스템 + 실시간 기상 연동 + 기종별 맞춤 판정
"""

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from enum import Enum
import httpx
import asyncio
import os
import json
import math
import re
import secrets
import time
from urban_canyon import measure_facade_gap

app = FastAPI(
    title="UAV Urban Ops API",
    description="승리 도시지역 드론 운용 판단 프로그램",
    version="2.2.0"
)

# Static 폴더 마운트
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

CLIENT_RUNTIME_ENV_KEYS = (
    "VITE_API_URL",
    "VITE_API_BASE_URL",
    "VITE_VWORLD_API_KEY",
    "VITE_VWORLD_3D_API_KEY",
    "VITE_VWORLD_KEY",
    "NEXT_PUBLIC_VWORLD_API_KEY",
)

# WFS building and road geometry credentials are server-only. Do not add these
# names to CLIENT_RUNTIME_ENV_KEYS or accept browser-prefixed fallbacks here.
# VWORLD_API_KEY remains as a Render compatibility name; runtime-config.js does
# not publish it to browsers.
SERVER_ONLY_VWORLD_DATA_ENV_KEYS = (
    "VWORLD_OFFICIAL_DATA_API_KEY",
    "VWORLD_DATA_API_KEY",
    "VWORLD_API_KEY",
)


@app.get("/runtime-config.js")
async def runtime_config_js():
    runtime_config = {}
    for key in CLIENT_RUNTIME_ENV_KEYS:
        value = os.getenv(key)
        if value:
            runtime_config[key] = value

    payload = "window.__UAV_RUNTIME_CONFIG__ = " + json.dumps(runtime_config, ensure_ascii=False) + ";"
    return Response(content=payload, media_type="application/javascript")


@app.get("/drone.svg")
async def drone_icon():
    return FileResponse('static/drone.svg')


@app.get("/")
async def read_root():
    return FileResponse('static/index.html')

# ============================================
# 모델 정의
# ============================================

class JudgmentLevel(str, Enum):
    HOLD = "HOLD"
    GO = "GO"
    RESTRICT = "RESTRICT"
    NO_GO = "NO-GO"

class DroneModel(str, Enum):
    MINI_3 = "DJI Mini 3 Pro"
    MAVIC_3 = "DJI Mavic 3"
    MATRICE_300 = "DJI Matrice 300 RTK"
    INSPIRE_3 = "DJI Inspire 3"
    CUSTOM = "Custom (Generic)"

# 기종별 내풍성 스펙 (m/s)
DRONE_SPECS = {
    DroneModel.MINI_3: {"wind": 10.7, "gust": 13.0, "desc": "소형 (한계 10.7m/s)"},
    DroneModel.MAVIC_3: {"wind": 12.0, "gust": 15.0, "desc": "준전문가용 (한계 12m/s)"},
    DroneModel.MATRICE_300: {"wind": 15.0, "gust": 18.0, "desc": "산업용 (한계 15m/s)"},
    DroneModel.INSPIRE_3: {"wind": 14.0, "gust": 16.0, "desc": "전문 촬영용 (한계 14m/s)"},
    DroneModel.CUSTOM: {"wind": 10.0, "gust": 12.0, "desc": "일반 기준 (한계 10m/s)"},
}

KMA_API_KEY = os.getenv("KMA_API_KEY")
KMA_DEFAULT_STATIONS = [
    {"id": 47102, "name": "백령도", "lat": 37.967, "lon": 124.630},
    {"id": 47122, "name": "오산", "lat": 37.090, "lon": 127.029},
    {"id": 47138, "name": "포항", "lat": 36.032, "lon": 129.380},
    {"id": 47169, "name": "흑산도", "lat": 34.688, "lon": 125.451},
]
WIS2_STATION_ENDPOINT = "https://wis2box.kma.go.kr/oapi/collections/stations/items/0-20000-0-{stn}?f=json"
WIND_PROFILER_MODE = os.getenv("KMA_WIND_PROFILER_MODE", "L")
WIND_PROFILER_MAX_ALT_M = float(os.getenv("KMA_WIND_PROFILER_MAX_ALT_M", "5000"))
WIS2_STATION_CACHE: Dict[int, Dict[str, Any]] = {}
WEATHER_CACHE_TTL_S = float(os.getenv("WEATHER_CACHE_TTL_S", "180"))
UPPER_AIR_CACHE_TTL_S = float(os.getenv("UPPER_AIR_CACHE_TTL_S", "900"))
WIND_PROFILER_CACHE_TTL_S = float(os.getenv("WIND_PROFILER_CACHE_TTL_S", "300"))
WEATHER_STALE_TTL_S = float(os.getenv("WEATHER_STALE_TTL_S", "1800"))
UPPER_AIR_STALE_TTL_S = float(os.getenv("UPPER_AIR_STALE_TTL_S", "21600"))
WIND_PROFILER_STALE_TTL_S = float(os.getenv("WIND_PROFILER_STALE_TTL_S", "1800"))
WEATHER_CACHE: Dict[str, Dict[str, Any]] = {}
UPPER_AIR_CACHE: Dict[str, Dict[str, Any]] = {}
WIND_PROFILER_CACHE: Dict[str, Dict[str, Any]] = {}
WEATHER_LAST_GOOD_CACHE: Dict[str, Dict[str, Any]] = {}
UPPER_AIR_LAST_GOOD_CACHE: Dict[str, Dict[str, Any]] = {}
WIND_PROFILER_LAST_GOOD_CACHE: Dict[str, Dict[str, Any]] = {}
KMA_UPPER_AIR_REQUEST_TIMEOUT_S = float(os.getenv("KMA_UPPER_AIR_REQUEST_TIMEOUT_S", "3.5"))
KMA_WIND_PROFILER_REQUEST_TIMEOUT_S = float(os.getenv("KMA_WIND_PROFILER_REQUEST_TIMEOUT_S", "2.5"))
SURFACE_WEATHER_REQUEST_TIMEOUT_S = float(os.getenv("SURFACE_WEATHER_REQUEST_TIMEOUT_S", "3.0"))
KP_REQUEST_TIMEOUT_S = float(os.getenv("KP_REQUEST_TIMEOUT_S", "2.0"))
VWORLD_WFS_API_ENDPOINTS = (
    {"url": "https://api.vworld.kr/req/wfs", "mode": "api"},
    {"url": "https://map.vworld.kr/js/wfs.do", "mode": "map"},
)
VWORLD_ROAD_LAYER = "lt_l_n3a0020000"
VWORLD_ROAD_PROPERTY_KEYS = ("rvwd", "rdln", "rdnm", "ag_geom")
VWORLD_ROAD_QUERY_RADII_M = (180, 500, 1500)
VWORLD_REQUEST_TIMEOUT_S = float(os.getenv("VWORLD_REQUEST_TIMEOUT_S", "5.0"))
CANYON_EVIDENCE_CACHE_TTL_S = float(os.getenv("CANYON_EVIDENCE_CACHE_TTL_S", "300"))
CANYON_EVIDENCE_CACHE: Dict[str, Dict[str, Any]] = {}
# Full server-side endpoint of the fixed-egress official GIS bridge. This is
# intentionally not part of runtime-config.js or any browser payload.
OFFICIAL_GIS_BRIDGE_URL = (os.getenv("OFFICIAL_GIS_BRIDGE_URL") or "").strip()
OFFICIAL_GIS_BRIDGE_TOKEN = (os.getenv("OFFICIAL_GIS_BRIDGE_TOKEN") or "").strip()
OFFICIAL_GIS_BRIDGE_TIMEOUT_S = float(os.getenv("OFFICIAL_GIS_BRIDGE_TIMEOUT_S", "6.0"))
# Set only on the dedicated bridge deployment. The primary API keeps this empty.
OFFICIAL_GIS_BRIDGE_INBOUND_TOKEN = (os.getenv("OFFICIAL_GIS_BRIDGE_INBOUND_TOKEN") or "").strip()
AUTHORITATIVE_WEATHER_SOURCE_TOKENS = (
    "kma_surface_observation",
    "kma_surface_forecast",
    "kma_surface_cache",
)
NON_AUTHORITATIVE_WEATHER_SOURCE_TOKENS = (
    "open_meteo_surface",
    "manual_surface_input",
    "surface_fallback",
    "weather_unavailable",
)
OFFICIAL_BUILDING_SOURCE_HINTS = ("official_verified", "vworld")
UNVERIFIED_BUILDING_SOURCE_HINTS = (
    "coordinate_based",
    "osm_fallback",
    "fallback",
    "heuristic",
    "manual",
    "unverified",
    "client",
    "browser",
)

class GateResult(BaseModel):
    gate: str
    status: JudgmentLevel
    reason: str
    value: Optional[float] = None
    threshold: Optional[str] = None

class EvaluationRequest(BaseModel):
    latitude: float = Field(..., description="위도")
    longitude: float = Field(..., description="경도")
    
    # 현장 정보
    building_height: float = Field(20.0, description="건물 높이 H (m)")
    street_width: float = Field(15.0, description="도로 폭 W (m)")
    wind_alignment: str = Field("직각", description="골목-풍향 (일치/직각/불명)")
    mission_altitude: float = Field(30.0, description="임무 고도 (m)")
    
    # 하드스탑 체크
    no_fly_zone: bool = Field(False, description="비행금지구역 여부")
    crowd_area: bool = Field(False, description="인파밀집 여부")
    
    # 위성 정보
    gps_locked: int = Field(12, description="GPS 잠금 위성 수")
    glonass_locked: int = Field(6, description="GLONASS 잠금 위성 수")
    
    # 기종 선택
    drone_model: DroneModel = Field(DroneModel.MAVIC_3, description="드론 기종")

    # 기상 정보 (선택)
    wind_speed: Optional[float] = Field(None, description="풍속 (m/s)")
    gust_speed: Optional[float] = Field(None, description="돌풍 (m/s)")
    visibility: Optional[float] = Field(None, description="시정 (km)")
    precipitation_prob: Optional[float] = Field(None, description="강수확률 (%)")
    kp_index: Optional[float] = Field(None, description="Kp 지수")
    # 추가 기상 정보 (옵션)
    temperature: Optional[float] = None
    humidity: Optional[float] = None

    # 건물 provenance (선택)
    building_source: Optional[str] = Field(None, description="건물 데이터 소스")
    building_profile_source: Optional[str] = Field(None, description="건물 프로파일 소스")
    building_source_chain: Optional[List[str]] = Field(None, description="건물 소스 체인")
    building_confidence: Optional[float] = Field(None, description="건물 신뢰도 (0-1)")
    building_evidence: Optional[Dict[str, Any]] = Field(None, description="건물 근거 객체")
    road_evidence: Optional[Dict[str, Any]] = Field(None, description="도로 폭 근거 객체")
    canyon_evidence: Optional[Dict[str, Any]] = Field(None, description="건물 간 이격폭 근거 객체")
    weather_evidence: Optional[Dict[str, Any]] = Field(None, description="기상 근거 객체")


class FootprintCacheRequest(BaseModel):
    lat: float
    lon: float
    geometry: List[List[float]]
    properties: Optional[Dict] = None
    source: str = "frontend_seed"

class EvaluationResponse(BaseModel):
    timestamp: str
    location: Dict[str, float]
    weather: Dict
    urban_factors: Dict
    gates: List[GateResult]
    final_judgment: JudgmentLevel
    ews: Optional[float]
    drone_spec: Dict
    source: Optional[str] = None
    profile_source: Optional[str] = None
    source_chain: Optional[List[str]] = None
    weather_source_chain: Optional[List[str]] = None
    building_source: Optional[str] = None
    building_profile_source: Optional[str] = None
    building_source_chain: Optional[List[str]] = None
    building_confidence: Optional[float] = None
    stale_cache: Optional[bool] = None
    upper_air_profile: Optional[Dict] = None
    wind_profiler_profile: Optional[Dict] = None
    selected_layer: Optional[Dict] = None
    profile_layers: Optional[List[Dict]] = None
    building_evidence: Optional[Dict] = None
    road_evidence: Optional[Dict] = None
    canyon_evidence: Optional[Dict] = None
    weather_evidence: Optional[Dict] = None
    input_quality: Optional[Dict] = None


class RoutePoint(BaseModel):
    lat: float
    lon: float


class CorridorAnalysisRequest(BaseModel):
    point_a: RoutePoint
    point_b: RoutePoint
    altitude: float = Field(50.0, ge=5.0, le=500.0)
    segment_count: int = Field(5, ge=2, le=20)
    drone_type: str = Field(DroneModel.MAVIC_3.value)


def _round_coord(value: float, precision: int = 3) -> float:
    return round(value, precision)


def _cache_get(store: Dict[str, Dict[str, Any]], key: str, ttl_s: float):
    entry = store.get(key)
    if not entry:
        return None
    if (time.time() - entry["ts"]) > ttl_s:
        store.pop(key, None)
        return None
    return entry["value"]


def _cache_set(store: Dict[str, Dict[str, Any]], key: str, value: Any):
    store[key] = {"ts": time.time(), "value": value}
    return value


def _cache_get_stale(store: Dict[str, Dict[str, Any]], key: str, max_age_s: float):
    entry = store.get(key)
    if not entry:
        return None
    if (time.time() - entry["ts"]) > max_age_s:
        store.pop(key, None)
        return None
    return entry["value"]


def _mark_stale_payload(value: Any):
    if value is None:
        return None
    if isinstance(value, dict):
        marked = dict(value)
        marked["stale_cache"] = True
        return marked
    return value


def _cache_key_for_latlon(lat: float, lon: float) -> str:
    return f"{_round_coord(lat)},{_round_coord(lon)}"


def _canyon_cache_key(lat: float, lon: float, road_name: Optional[str]) -> str:
    normalized_road_name = " ".join(str(road_name or "").split()).lower()
    return f"{_round_coord(lat, 5)},{_round_coord(lon, 5)}:{normalized_road_name}"


def _mark_source_suffix(payload: Optional[Dict[str, Any]], suffix: str, fallback_source: str) -> Optional[Dict[str, Any]]:
    if not payload:
        return payload
    marked = dict(payload)
    marked["stale_cache"] = True
    marked["source"] = f'{marked.get("source", fallback_source)} + {suffix}'
    return marked


def _normalize_source_chain(*parts: Any) -> List[str]:
    chain: List[str] = []
    seen = set()
    for part in parts:
        if part is None:
            continue
        values = part if isinstance(part, (list, tuple, set)) else [part]
        for value in values:
            token = str(value).strip()
            if not token or token in seen:
                continue
            chain.append(token)
            seen.add(token)
    return chain


def _parse_source_chain(source: Optional[str]) -> List[str]:
    if not source:
        return []
    tokens = []
    for part in str(source).split(" + "):
        token = part.strip()
        if not token or token.startswith("stale_"):
            continue
        tokens.append(token)
    return _normalize_source_chain(tokens)


def _chain_to_source(chain: Optional[List[str]]) -> Optional[str]:
    normalized = _normalize_source_chain(chain or [])
    return " + ".join(normalized) if normalized else None


def _clamp(value: Optional[float], minimum: float = 0.0, maximum: float = 1.0) -> float:
    if value is None:
        return minimum
    return max(minimum, min(maximum, float(value)))


def _weather_profile_source(upper_air: Optional[Dict[str, Any]], wind_profiler: Optional[Dict[str, Any]]) -> str:
    if upper_air and wind_profiler:
        return "kma_radiosonde_wind_profiler"
    if upper_air:
        return "kma_radiosonde"
    if wind_profiler:
        return "kma_wind_profiler"
    return "surface_only"


def _attach_weather_provenance(
    weather: Dict[str, Any],
    upper_air: Optional[Dict[str, Any]] = None,
    wind_profiler: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = dict(weather)
    source_chain = _normalize_source_chain(payload.get("source_chain") or [])
    if not source_chain:
        source_chain = _parse_source_chain(payload.get("source"))
    if not source_chain:
        source = str(payload.get("source") or "")
        if source.startswith("surface_fallback"):
            source_chain = ["surface_fallback"]
        elif source:
            source_chain = [source]
        else:
            source_chain = ["open_meteo_surface"]

    if upper_air and "kma_radiosonde" not in source_chain:
        source_chain.append("kma_radiosonde")
    if wind_profiler and "kma_wind_profiler" not in source_chain:
        source_chain.append("kma_wind_profiler")

    payload["source_chain"] = _normalize_source_chain(source_chain)
    payload["profile_source"] = _weather_profile_source(upper_air, wind_profiler)
    payload["stale_cache"] = bool(payload.get("stale_cache", False))
    return payload


def _source_chain_contains(source_chain: List[str], hints: tuple[str, ...]) -> bool:
    lowered = [token.lower() for token in source_chain]
    return any(any(hint in token for hint in hints) for token in lowered)


def _weather_is_authoritative(source_chain: List[str], stale_cache: bool) -> bool:
    if stale_cache:
        return False
    return _source_chain_contains(source_chain, AUTHORITATIVE_WEATHER_SOURCE_TOKENS)


def _default_weather_fields() -> Dict[str, Any]:
    return {
        "wind_speed": 5.0,
        "gust_speed": 8.0,
        "wind_direction": 0,
        "visibility": 10.0,
        "precipitation_prob": 0,
        "temperature": 20.0,
        "dew_point": 15.0,
        "humidity": 50,
        "cloud_cover": 20,
        "weather_code": 0,
        "sunrise": "06:00",
        "sunset": "18:00",
        "profile_source": "surface_only",
    }


def _make_weather_unavailable(reason: str, fallback: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = _default_weather_fields()
    if fallback:
        for key in payload:
            if key in fallback:
                payload[key] = fallback[key]
    payload.update(
        {
            "available": False,
            "authoritative": False,
            "authority_source": None,
            "reason": reason,
            "source": "weather_unavailable",
            "source_chain": ["weather_unavailable", reason],
            "stale_cache": bool(fallback and fallback.get("stale_cache")),
        }
    )
    return payload


def _make_fresh_weather_cache_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    cached = dict(payload)
    cached["source"] = "kma_surface_cache"
    cached["source_chain"] = _normalize_source_chain(["kma_surface_cache"], payload.get("source_chain") or payload.get("source"))
    cached["available"] = True
    cached["authoritative"] = True
    cached["authority_source"] = "kma_surface_cache"
    cached["stale_cache"] = False
    return cached


def _build_weather_evidence(
    weather: Dict[str, Any],
    upper_air: Optional[Dict[str, Any]] = None,
    wind_profiler: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    source_chain = _normalize_source_chain(weather.get("source_chain") or [], weather.get("source"))
    stale_cache = bool(weather.get("stale_cache", False))
    authoritative = bool(weather.get("authoritative")) and _weather_is_authoritative(source_chain, stale_cache)
    if not authoritative:
        authoritative = _weather_is_authoritative(source_chain, stale_cache)
    available = bool(weather.get("available", "weather_unavailable" not in source_chain))
    if weather.get("reason") == "surface_weather_timeout":
        available = False
    status = "official_verified" if authoritative else ("unavailable" if not available else "estimated")
    authority_source = weather.get("authority_source")
    if authoritative and not authority_source:
        authority_source = next((token for token in source_chain if token in AUTHORITATIVE_WEATHER_SOURCE_TOKENS), source_chain[0] if source_chain else None)
    return {
        "available": available,
        "authoritative": authoritative,
        "status": status,
        "source": weather.get("source"),
        "source_chain": source_chain,
        "authority_source": authority_source,
        "profile_source": weather.get("profile_source"),
        "stale_cache": stale_cache,
        "reason": weather.get("reason"),
        "upper_air_available": bool(upper_air),
        "wind_profiler_available": bool(wind_profiler),
    }


def _build_building_evidence(request: EvaluationRequest) -> Dict[str, Any]:
    provided = dict(request.building_evidence or {})
    source_chain = _normalize_source_chain(
        provided.get("source_chain") or [],
        request.building_source_chain or [],
        request.building_source or None,
        request.building_profile_source or None,
    )
    available = bool(provided.get("available", request.building_height > 0))
    receipt = provided.get("receipt") if isinstance(provided.get("receipt"), dict) else {}
    receipt_sources = _normalize_source_chain(receipt.get("source_chain") or [], receipt.get("source"))
    height_source_text = " ".join(
        str(value or "")
        for value in (
            request.building_source,
            request.building_profile_source,
            provided.get("source"),
            provided.get("profile_source"),
            provided.get("derivation"),
            provided.get("height_source"),
            provided.get("building_height_source"),
            *source_chain,
        )
    ).lower()
    height_is_floor_derived = bool(provided.get("height_estimated_from_official_floors")) or bool(
        re.search(r"floor(?:_|-|\s)?(?:count|estimate)|official_floor_count|derived", height_source_text)
    )
    height_receipt_complete = bool(
        receipt.get("kind") == "official_building_height"
        and receipt.get("geometry_receipt") is True
        and receipt.get("selection_match") is True
        and receipt_sources
    )
    if "official_available" in provided:
        reported_official_available = bool(provided.get("official_available"))
    else:
        reported_official_available = (
            available
            and _source_chain_contains(source_chain, OFFICIAL_BUILDING_SOURCE_HINTS)
            and not _source_chain_contains(source_chain, UNVERIFIED_BUILDING_SOURCE_HINTS)
        )
    official_available = bool(
        reported_official_available
        and available
        and height_receipt_complete
        and not height_is_floor_derived
    )
    status = "official_verified" if official_available else ("estimated" if available else "unavailable")
    return {
        "available": available,
        "official_available": official_available,
        "status": status,
        "height_m": request.building_height,
        "source": request.building_source or (source_chain[0] if source_chain else "building_unavailable"),
        "profile_source": request.building_profile_source,
        "source_chain": source_chain,
        "confidence": _clamp(request.building_confidence if request.building_confidence is not None else provided.get("confidence", 0.72)),
        "height_receipt_complete": height_receipt_complete,
        "height_is_floor_derived": height_is_floor_derived,
    }


def _normalize_road_evidence(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    road = dict(payload or {})
    source_chain = _normalize_source_chain(road.get("source_chain") or [], road.get("source"))
    available = bool(road.get("available"))
    official_available = bool(road.get("official_available"))
    status = "official_verified" if official_available else ("estimated" if available else "unavailable")
    return {
        "available": available,
        "official_available": official_available,
        "status": status,
        "width_m": road.get("width_m"),
        "official_road_right_of_way_width_m": road.get("official_road_right_of_way_width_m", road.get("width_m")),
        "lane_count": road.get("lane_count"),
        "road_name": road.get("road_name"),
        "geometry_paths": road.get("geometry_paths") or [],
        "geometry_receipt": bool(road.get("geometry_receipt")),
        "source": road.get("source", "official_road_right_of_way_unavailable"),
        "source_chain": source_chain,
        "reason": road.get("reason"),
        "query_meta": road.get("query_meta"),
    }


def _build_input_quality(
    building_evidence: Dict[str, Any],
    road_evidence: Dict[str, Any],
    canyon_evidence: Dict[str, Any],
    weather_evidence: Dict[str, Any],
) -> Dict[str, Any]:
    missing_prerequisites: List[str] = []
    reasons: List[str] = []

    if not building_evidence.get("official_available"):
        missing_prerequisites.append("building")
        reasons.append(f'building:{building_evidence.get("status")}')
    if not canyon_evidence.get("official_available"):
        missing_prerequisites.append("canyon_width")
        reasons.append(f'canyon_width:{canyon_evidence.get("reason") or canyon_evidence.get("status")}')
    if not (weather_evidence.get("available") and weather_evidence.get("authoritative")):
        missing_prerequisites.append("weather")
        reasons.append(f'weather:{weather_evidence.get("reason") or weather_evidence.get("status")}')

    status = "hold" if missing_prerequisites else "ready"
    return {
        "status": status,
        "missing_prerequisites": missing_prerequisites,
        "reasons": reasons,
    }


def _vworld_api_key() -> Optional[str]:
    for key in SERVER_ONLY_VWORLD_DATA_ENV_KEYS:
        value = os.getenv(key)
        if value:
            return value
    return None


def _vworld_referer() -> str:
    return str(os.getenv("VWORLD_REFERER") or os.getenv("VWORLD_DOMAIN") or "https://uav-vercel.pages.dev/").strip()


def _official_gis_readiness() -> Dict[str, Any]:
    """Expose deployment readiness without ever returning a credential or bridge URL."""
    vworld_data_key_configured = bool(_vworld_api_key())
    bridge_url_configured = bool(OFFICIAL_GIS_BRIDGE_URL)
    bridge_token_configured = bool(OFFICIAL_GIS_BRIDGE_TOKEN)
    missing = []
    if not vworld_data_key_configured:
        missing.append("vworld_server_data_api_key")
    if not bridge_url_configured:
        missing.append("official_gis_bridge_url")
    if not bridge_token_configured:
        missing.append("official_gis_bridge_token")

    return {
        "status": "ready" if not missing else "hold",
        "vworld_server_data_key_configured": vworld_data_key_configured,
        "vworld_referer_source": "environment" if (os.getenv("VWORLD_REFERER") or os.getenv("VWORLD_DOMAIN")) else "default",
        "official_gis_bridge_url_configured": bridge_url_configured,
        "official_gis_bridge_token_configured": bridge_token_configured,
        "facade_gap_policy": "verified_official_geometry_only",
        "missing_prerequisites": missing,
    }


def _parse_loose_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    if not isinstance(value, str):
        return None
    cleaned = "".join(char for char in value if char.isdigit() or char in ".+-")
    if not cleaned:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _extract_road_width_meters(properties: Dict[str, Any]) -> Optional[float]:
    for key in ("width", "road_width", "roadwidth", "r_width", "rd_width", "road_bt", "rvwd", "wdt", "wid", "dwg_wid", "std_width", "carriageway_width"):
        value = _parse_loose_number(properties.get(key))
        if value is not None and value > 0:
            return round(value, 1)
    return None


def _extract_road_lane_count(properties: Dict[str, Any]) -> Optional[int]:
    for key in ("rdln", "lanes", "lane_count", "lanecount", "lane_cnt", "lane_num", "ln_cnt", "car_lane", "car_lanes"):
        value = _parse_loose_number(properties.get(key))
        if value is not None and value > 0:
            return max(1, int(round(value)))
    return None


def _extract_road_name(properties: Dict[str, Any]) -> Optional[str]:
    for key in ("rdnm", "name", "name:ko", "road_name", "rd_nm", "rn", "display_name", "label"):
        value = str(properties.get(key) or "").strip()
        if value:
            return value
    return None


def _lonlat_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    origin_shift = 20037508.34
    x = lon * origin_shift / 180.0
    lat = max(min(lat, 89.5), -89.5)
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0)
    y = y * origin_shift / 180.0
    return x, y


def _mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    origin_shift = 20037508.34
    lon = (x / origin_shift) * 180.0
    lat = (y / origin_shift) * 180.0
    lat = 180.0 / math.pi * (2.0 * math.atan(math.exp(lat * math.pi / 180.0)) - math.pi / 2.0)
    return lon, lat


def _build_mercator_bbox(lon: float, lat: float, radius_m: float) -> str:
    x, y = _lonlat_to_mercator(lon, lat)
    return f"{x - radius_m},{y - radius_m},{x + radius_m},{y + radius_m}"


def _point_to_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    ratio = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    ratio = max(0.0, min(1.0, ratio))
    closest_x = ax + ratio * dx
    closest_y = ay + ratio * dy
    return math.hypot(px - closest_x, py - closest_y)


def _parse_linestring_wkt(value: str) -> List[List[List[float]]]:
    text = value.strip()
    if not text:
        return []
    upper = text.upper()
    if upper.startswith("LINESTRING(") and text.endswith(")"):
        body = text[text.find("(") + 1 : -1]
        return [[_parse_wkt_point_pair(pair) for pair in body.split(",") if _parse_wkt_point_pair(pair)]]
    if upper.startswith("MULTILINESTRING(") and text.endswith(")"):
        body = text[text.find("(") + 1 : -1]
        lines: List[List[List[float]]] = []
        for chunk in body.split("),"):
            clean = chunk.replace("(", "").replace(")", "")
            line = [_parse_wkt_point_pair(pair) for pair in clean.split(",") if _parse_wkt_point_pair(pair)]
            if line:
                lines.append(line)
        return lines
    return []


def _parse_wkt_point_pair(pair: str) -> Optional[List[float]]:
    values = [token for token in pair.strip().split(" ") if token]
    if len(values) < 2:
        return None
    try:
        return [float(values[0]), float(values[1])]
    except ValueError:
        return None


def _geometry_paths(feature: Dict[str, Any]) -> List[List[List[float]]]:
    geometry = feature.get("geometry") or {}
    geometry_type = str(geometry.get("type") or "").lower()
    coords = geometry.get("coordinates") or []
    if geometry_type == "linestring" and len(coords) >= 2:
        return [coords]
    if geometry_type == "multilinestring":
        return [path for path in coords if len(path) >= 2]
    properties = feature.get("properties") or feature.get("attributes") or {}
    ag_geom = properties.get("ag_geom")
    if isinstance(ag_geom, str):
        return _parse_linestring_wkt(ag_geom)
    return []


def _extract_vworld_features(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = (payload.get("response") or {}).get("result") or payload.get("result") or {}
    collection = result.get("featureCollection") or result.get("FeatureCollection") or payload.get("featureCollection") or payload.get("FeatureCollection") or {}
    return collection.get("features") or result.get("features") or payload.get("features") or []


def _normalize_road_candidate(feature: Dict[str, Any], lat: float, lon: float) -> Optional[Dict[str, Any]]:
    properties = dict(feature.get("properties") or feature.get("attributes") or {})
    width_m = _extract_road_width_meters(properties)
    lane_count = _extract_road_lane_count(properties)
    road_name = _extract_road_name(properties)
    if width_m is None and lane_count is None and not road_name:
        return None
    paths = _geometry_paths(feature)
    target_x, target_y = _lonlat_to_mercator(lon, lat)
    edge_distance_m = None
    for path in paths:
        for start, end in zip(path, path[1:]):
            segment_distance = _point_to_segment_distance(target_x, target_y, float(start[0]), float(start[1]), float(end[0]), float(end[1]))
            if edge_distance_m is None or segment_distance < edge_distance_m:
                edge_distance_m = segment_distance
    source = "official_road_right_of_way" if width_m is not None else ("official_road_lanes_estimate" if lane_count is not None else "official_road_right_of_way_unavailable")
    return {
        "available": width_m is not None or lane_count is not None,
        "official_available": width_m is not None,
        "width_m": width_m,
        "official_road_right_of_way_width_m": width_m,
        "lane_count": lane_count,
        "road_name": road_name,
        "source": source,
        "source_chain": ["vworld_wfs", source],
        "geometry_paths": paths,
        "geometry_receipt": bool(paths),
        "edge_distance_m": round(edge_distance_m, 1) if edge_distance_m is not None else None,
        "properties": properties,
    }


def _build_vworld_wfs_request_url(endpoint: Dict[str, str], bbox: str, key: str, referer: str) -> str:
    url = httpx.URL(endpoint["url"])
    registered_domain = referer
    if "://" in referer:
        registered_domain = httpx.URL(referer).netloc.decode("ascii")
    params = {
        "service" if endpoint["mode"] == "api" else "SERVICE": "WFS",
        "request" if endpoint["mode"] == "api" else "REQUEST": "GetFeature",
        "version" if endpoint["mode"] == "api" else "VERSION": "1.1.0",
        "typename" if endpoint["mode"] == "api" else "TYPENAME": VWORLD_ROAD_LAYER,
        "maxfeatures" if endpoint["mode"] == "api" else "MAXFEATURES": "40",
        "srsname" if endpoint["mode"] == "api" else "SRSNAME": "EPSG:3857",
        "output" if endpoint["mode"] == "api" else "OUTPUT": "application/json",
        "exceptions" if endpoint["mode"] == "api" else "EXCEPTIONS": "text/xml",
        "propertyname" if endpoint["mode"] == "api" else "PROPERTYNAME": ",".join(VWORLD_ROAD_PROPERTY_KEYS),
        "bbox" if endpoint["mode"] == "api" else "BBOX": bbox,
    }
    if endpoint["mode"] == "api":
        params["key"] = key
        params["domain"] = registered_domain
    else:
        params["APIKEY"] = key
        params["DOMAIN"] = registered_domain
    return str(url.copy_merge_params(params))


async def fetch_road_width_evidence(lat: float, lon: float, road_name: Optional[str] = None) -> Dict[str, Any]:
    key = _vworld_api_key()
    referer = _vworld_referer()
    if not key:
        return {
            "available": False,
            "official_available": False,
            "width_m": None,
            "lane_count": None,
            "road_name": road_name,
            "source": "official_road_right_of_way_unavailable",
            "source_chain": ["vworld_wfs", "official_road_right_of_way_unavailable"],
            "reason": "missing_vworld_data_api_key",
            "query_meta": {"layer": VWORLD_ROAD_LAYER},
        }

    last_reason = "road_feature_not_matched"
    async with httpx.AsyncClient(timeout=VWORLD_REQUEST_TIMEOUT_S) as client:
        for radius_m in VWORLD_ROAD_QUERY_RADII_M:
            bbox = _build_mercator_bbox(lon, lat, radius_m)
            for endpoint in VWORLD_WFS_API_ENDPOINTS:
                request_url = _build_vworld_wfs_request_url(endpoint, bbox, key, referer)
                query_meta = {
                    "layer": VWORLD_ROAD_LAYER,
                    "radius_m": radius_m,
                    "bbox": bbox,
                    "endpoint_mode": endpoint["mode"],
                    "property_names": list(VWORLD_ROAD_PROPERTY_KEYS),
                }
                try:
                    response = await client.get(
                        request_url,
                        headers={
                            "Accept": "application/json",
                            "Referer": referer,
                            "User-Agent": "uav-dashboard/road-width-authority",
                        },
                    )
                except Exception:
                    last_reason = "network_error"
                    continue
                if response.status_code != 200:
                    last_reason = f"upstream_status_{response.status_code}"
                    continue
                if "ServiceException" in response.text:
                    last_reason = "road_feature_not_matched"
                    continue
                try:
                    payload = json.loads(response.text)
                except json.JSONDecodeError:
                    last_reason = "malformed_upstream_payload"
                    continue
                candidates = [
                    candidate
                    for candidate in (
                        _normalize_road_candidate(feature, lat, lon)
                        for feature in _extract_vworld_features(payload)
                    )
                    if candidate and candidate.get("available")
                ]
                if not candidates:
                    last_reason = "road_feature_not_matched"
                    continue
                candidates.sort(
                    key=lambda item: (
                        not bool(item.get("official_available")),
                        item.get("edge_distance_m") if item.get("edge_distance_m") is not None else float("inf"),
                        -(item.get("width_m") or 0.0),
                    )
                )
                selected = dict(candidates[0])
                selected["query_meta"] = query_meta
                selected["source_chain"] = ["vworld_wfs", selected.get("source", "official_road_right_of_way"), VWORLD_ROAD_LAYER]
                selected["reason"] = "official_road_right_of_way" if selected.get("official_available") else "official_road_lanes_estimate"
                return selected

    return {
        "available": False,
        "official_available": False,
        "width_m": None,
        "lane_count": None,
        "road_name": road_name,
        "source": "official_road_right_of_way_unavailable",
        "source_chain": ["vworld_wfs", "official_road_right_of_way_unavailable"],
        "reason": last_reason,
        "query_meta": {"layer": VWORLD_ROAD_LAYER},
    }


def _project_lonlat_ring(ring: List[List[float]]) -> List[List[float]]:
    projected: List[List[float]] = []
    for point in ring:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            x, y = _lonlat_to_mercator(float(point[0]), float(point[1]))
        except (TypeError, ValueError):
            continue
        projected.append([x, y])
    return projected


def _unavailable_canyon_evidence(
    road_evidence: Dict[str, Any],
    reason: str,
    target_building: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "available": False,
        "official_available": False,
        "facade_gap_m": None,
        "effective_canyon_width_m": None,
        "official_road_right_of_way_width_m": road_evidence.get("official_road_right_of_way_width_m", road_evidence.get("width_m")),
        "road_name": road_evidence.get("road_name"),
        "target_building": target_building,
        "opposing_building": None,
        "road_crossing_verified": False,
        "normal_alignment": None,
        "source": "official_canyon_width_unavailable",
        "source_chain": _normalize_source_chain(road_evidence.get("source_chain"), "official_canyon_width_unavailable"),
        "reason": reason,
        "receipt": {
            "kind": "official_canyon_width_unavailable",
            "target_geometry_receipt": False,
            "opposing_geometry_receipt": False,
            "road_geometry_receipt": bool(road_evidence.get("geometry_receipt")),
            "road_crossing_verified": False,
            "source_chain": _normalize_source_chain(road_evidence.get("source_chain"), "official_canyon_width_unavailable"),
        },
    }


def _unavailable_official_gis_bridge_evidence(reason: str) -> Dict[str, Any]:
    """Return a safe HOLD when the configured server-only bridge cannot be used."""
    source_chain = ["official_gis_bridge", "official_gis_bridge_unavailable"]
    return {
        "available": False,
        "official_available": False,
        "facade_gap_m": None,
        "effective_canyon_width_m": None,
        "official_road_right_of_way_width_m": None,
        "road_name": None,
        "target_building": None,
        "opposing_building": None,
        "road_crossing_verified": False,
        "normal_alignment": None,
        "source": "official_gis_bridge_unavailable",
        "source_chain": source_chain,
        "reason": reason,
        "receipt": {
            "kind": "official_gis_bridge_unavailable",
            "target_geometry_receipt": False,
            "opposing_geometry_receipt": False,
            "road_geometry_receipt": False,
            "road_crossing_verified": False,
            "source_chain": source_chain,
        },
    }


def _select_target_building_from_collection(
    collection: Dict[str, Any],
    lat: float,
    lon: float,
) -> Optional[Dict[str, Any]]:
    candidates = []
    for feature in collection.get("features") or []:
        ring = feature.get("ring") if isinstance(feature, dict) else None
        if not isinstance(ring, list) or len(ring) < 4:
            continue
        if _point_in_polygon(lon, lat, ring):
            candidates.append(feature)

    if len(candidates) != 1:
        return None

    selected = candidates[0]
    properties = selected.get("properties") if isinstance(selected.get("properties"), dict) else {}
    return {
        "id": str(selected.get("id") or properties.get("bd_mgt_sn") or properties.get("pk") or "target-building"),
        "name": selected.get("name") or properties.get("buld_nm"),
        "ring": selected.get("ring"),
        "properties": properties,
    }


def _bridge_canyon_evidence_is_verified(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    receipt = payload.get("receipt")
    facade_gap_m = _parse_loose_number(payload.get("facade_gap_m"))
    source_chain = _normalize_source_chain(payload.get("source_chain"), payload.get("source"))
    return bool(
        payload.get("available")
        and payload.get("official_available")
        and payload.get("source") == "official_canyon_width"
        and facade_gap_m is not None
        and facade_gap_m > 0
        and isinstance(receipt, dict)
        and receipt.get("kind") == "official_canyon_width"
        and receipt.get("target_geometry_receipt")
        and receipt.get("opposing_geometry_receipt")
        and receipt.get("road_geometry_receipt")
        and receipt.get("road_crossing_verified")
        and any(token in {"vworld_wfs", "official_building_collection"} for token in source_chain)
    )


def _with_official_gis_bridge_provenance(payload: Dict[str, Any]) -> Dict[str, Any]:
    source_chain = _normalize_source_chain(payload.get("source_chain"), "official_gis_bridge", "official_canyon_width")
    receipt = dict(payload["receipt"])
    receipt["source_chain"] = source_chain
    result = dict(payload)
    result["source_chain"] = source_chain
    result["receipt"] = receipt
    result["bridge_provider"] = "official_gis_bridge"
    return result


def _bridge_canyon_evidence_is_explicitly_unavailable(payload: Any) -> bool:
    """Accept an authenticated bridge HOLD receipt without replacing it with estimates."""
    if not isinstance(payload, dict):
        return False
    receipt = payload.get("receipt")
    source = payload.get("source")
    receipt_kind = receipt.get("kind") if isinstance(receipt, dict) else None
    return bool(
        payload.get("available") is False
        and payload.get("official_available") is False
        and payload.get("reason")
        and isinstance(receipt, dict)
        and (
            (source == "official_canyon_width_unavailable" and receipt_kind == "official_canyon_width_unavailable")
            or (source == "official_gis_bridge_unavailable" and receipt_kind == "official_gis_bridge_unavailable")
        )
    )


def _bridge_vworld_upstream_failure_allows_direct_fallback(payload: Any) -> bool:
    """Allow Render to re-query official VWorld geometry after a Worker-only upstream failure."""
    if not _bridge_canyon_evidence_is_explicitly_unavailable(payload):
        return False
    reason = payload.get("reason")
    return isinstance(reason, str) and reason.startswith(("building_upstream_status_", "road_upstream_status_"))


def _with_official_gis_bridge_unavailable_provenance(payload: Dict[str, Any]) -> Dict[str, Any]:
    source_chain = _normalize_source_chain(
        payload.get("source_chain"),
        "official_gis_bridge",
        payload.get("source"),
    )
    receipt = dict(payload.get("receipt") or {})
    receipt["source_chain"] = source_chain
    result = dict(payload)
    result["source_chain"] = source_chain
    result["receipt"] = receipt
    result["bridge_provider"] = "official_gis_bridge"
    return result


def _with_official_gis_bridge_fallback_provenance(payload: Dict[str, Any], reason: Optional[str]) -> Dict[str, Any]:
    if not reason:
        return payload
    result = dict(payload)
    receipt = dict(result.get("receipt") or {})
    result["bridge_provider"] = "official_gis_bridge"
    result["bridge_fallback_reason"] = reason
    receipt["bridge_fallback_reason"] = reason
    result["receipt"] = receipt
    return result


async def fetch_official_gis_bridge_canyon_evidence(
    lat: float,
    lon: float,
    road_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not OFFICIAL_GIS_BRIDGE_URL or not OFFICIAL_GIS_BRIDGE_TOKEN:
        return None

    params: Dict[str, Any] = {"lat": lat, "lon": lon}
    if road_name:
        params["road_name"] = road_name

    try:
        async with httpx.AsyncClient(timeout=OFFICIAL_GIS_BRIDGE_TIMEOUT_S) as client:
            response = await client.get(
                OFFICIAL_GIS_BRIDGE_URL,
                params=params,
                headers={"Authorization": f"Bearer {OFFICIAL_GIS_BRIDGE_TOKEN}"},
            )
        if response.status_code != 200:
            return _unavailable_official_gis_bridge_evidence(f"official_gis_bridge_http_{response.status_code}")
        payload = response.json()
    except httpx.TimeoutException:
        return _unavailable_official_gis_bridge_evidence("official_gis_bridge_timeout")
    except httpx.HTTPError:
        return _unavailable_official_gis_bridge_evidence("official_gis_bridge_transport_error")
    except ValueError:
        return _unavailable_official_gis_bridge_evidence("official_gis_bridge_invalid_payload")

    if not isinstance(payload, dict):
        return _unavailable_official_gis_bridge_evidence("official_gis_bridge_invalid_payload")

    return payload


async def fetch_canyon_width_evidence(lat: float, lon: float, road_name: Optional[str] = None) -> Dict[str, Any]:
    cache_key = _canyon_cache_key(lat, lon, road_name)
    cached_evidence = _cache_get(CANYON_EVIDENCE_CACHE, cache_key, CANYON_EVIDENCE_CACHE_TTL_S)
    if cached_evidence:
        return cached_evidence

    bridge_evidence = await fetch_official_gis_bridge_canyon_evidence(lat, lon, road_name=road_name)
    if _bridge_canyon_evidence_is_verified(bridge_evidence):
        return _cache_set(CANYON_EVIDENCE_CACHE, cache_key, _with_official_gis_bridge_provenance(bridge_evidence))
    bridge_fallback_reason: Optional[str] = None
    if _bridge_canyon_evidence_is_explicitly_unavailable(bridge_evidence):
        if _bridge_vworld_upstream_failure_allows_direct_fallback(bridge_evidence):
            bridge_fallback_reason = str(bridge_evidence["reason"])
        else:
            return _with_official_gis_bridge_unavailable_provenance(bridge_evidence)

    road_evidence, collection = await asyncio.gather(
        fetch_road_width_evidence(lat, lon, road_name=road_name),
        lookup_official_building_collection(lat, lon),
    )
    road_paths = road_evidence.get("geometry_paths") or []
    if not road_evidence.get("official_available") or not road_evidence.get("geometry_receipt") or not road_paths:
        return _with_official_gis_bridge_fallback_provenance(
            _unavailable_canyon_evidence(road_evidence, road_evidence.get("reason") or "official_road_geometry_not_matched"),
            bridge_fallback_reason,
        )

    if not collection.get("official_available"):
        return _with_official_gis_bridge_fallback_provenance(
            _unavailable_canyon_evidence(
                road_evidence,
                collection.get("reason") or "official_building_collection_not_matched",
            ),
            bridge_fallback_reason,
        )

    target = _select_target_building_from_collection(collection, lat, lon)
    target_building = {
        "id": target.get("id") if target else "target-building",
        "name": target.get("name") if target else None,
        "geometry_receipt": bool(target),
        "selection_match": bool(target),
    }
    target_geometry = target.get("ring") if target else None
    if not isinstance(target_geometry, list) or len(target_geometry) < 4:
        return _with_official_gis_bridge_fallback_provenance(
            _unavailable_canyon_evidence(road_evidence, "target_official_building_not_selected", target_building),
            bridge_fallback_reason,
        )

    buildings = []
    for feature in collection.get("features") or []:
        ring = feature.get("ring") if isinstance(feature, dict) else None
        if not isinstance(ring, list) or len(ring) < 4:
            continue
        buildings.append({
            "id": str(feature.get("id") or "official-building"),
            "name": feature.get("name"),
            "ring": _project_lonlat_ring(ring),
        })

    measurement = measure_facade_gap(
        _project_lonlat_ring(target_geometry),
        road_paths[0],
        buildings,
    )
    source_chain = _normalize_source_chain(
        collection.get("source_chain"),
        road_evidence.get("source_chain"),
        "official_canyon_width",
    )
    if not measurement.get("available"):
        unavailable = _unavailable_canyon_evidence(road_evidence, measurement.get("reason") or "opposing_official_building_not_matched", target_building)
        unavailable["source_chain"] = source_chain
        unavailable["receipt"]["source_chain"] = source_chain
        unavailable["receipt"]["target_geometry_receipt"] = True
        return _with_official_gis_bridge_fallback_provenance(unavailable, bridge_fallback_reason)

    opposing_building = {
        "id": measurement.get("opposing_building_id"),
        "name": measurement.get("opposing_building_name"),
        "geometry_receipt": True,
    }
    receipt = {
        "kind": "official_canyon_width",
        "target_geometry_receipt": True,
        "opposing_geometry_receipt": True,
        "road_geometry_receipt": True,
        "road_crossing_verified": True,
        "source_chain": source_chain,
    }
    result = {
        "available": True,
        "official_available": True,
        "facade_gap_m": measurement["facade_gap_m"],
        "effective_canyon_width_m": measurement["facade_gap_m"],
        "official_road_right_of_way_width_m": road_evidence.get("official_road_right_of_way_width_m", road_evidence.get("width_m")),
        "road_name": road_evidence.get("road_name"),
        "target_building": target_building,
        "opposing_building": opposing_building,
        "road_crossing_verified": True,
        "normal_alignment": measurement.get("normal_alignment"),
        "target_point": measurement.get("target_point"),
        "opposing_point": measurement.get("opposing_point"),
        "source": "official_canyon_width",
        "source_chain": source_chain,
        "reason": None,
        "receipt": receipt,
    }
    return _cache_set(
        CANYON_EVIDENCE_CACHE,
        cache_key,
        _with_official_gis_bridge_fallback_provenance(result, bridge_fallback_reason),
    )


def _normalize_canyon_evidence(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    canyon = dict(payload or {})
    source_chain = _normalize_source_chain(canyon.get("source_chain") or [], canyon.get("source"))
    receipt = canyon.get("receipt") if isinstance(canyon.get("receipt"), dict) else {}
    facade_gap_m = _parse_loose_number(canyon.get("facade_gap_m"))
    verified = bool(
        canyon.get("available")
        and canyon.get("official_available")
        and facade_gap_m is not None
        and receipt.get("kind") == "official_canyon_width"
        and receipt.get("target_geometry_receipt")
        and receipt.get("opposing_geometry_receipt")
        and receipt.get("road_geometry_receipt")
        and receipt.get("road_crossing_verified")
    )
    return {
        "available": bool(canyon.get("available")),
        "official_available": verified,
        "status": "official_verified" if verified else ("estimated" if canyon.get("available") else "unavailable"),
        "facade_gap_m": facade_gap_m if verified else None,
        "official_road_right_of_way_width_m": _parse_loose_number(canyon.get("official_road_right_of_way_width_m")),
        "source": canyon.get("source", "official_canyon_width_unavailable"),
        "source_chain": source_chain,
        "reason": canyon.get("reason"),
        "receipt": receipt,
    }


def _building_source_quality(
    building_source: Optional[str],
    building_source_chain: Optional[List[str]] = None,
) -> float:
    tokens = [token.lower() for token in _normalize_source_chain(building_source_chain or [], building_source or "")]
    if any("vworld_wfs" in token for token in tokens):
        return 1.0
    if any("footprint_cache" in token for token in tokens):
        return 0.92
    if any("osm_fallback" in token for token in tokens):
        return 0.75
    if any("coordinate_based" in token or "building_height_heuristic" in token for token in tokens):
        return 0.65
    if any("browser_synthetic" in token or "client_fallback" in token for token in tokens):
        return 0.5
    if any("manual" in token for token in tokens):
        return 0.7
    if any("vworld" in token for token in tokens):
        return 0.85
    return 0.65


def _resolve_building_canyon_weight(
    building_confidence: Optional[float],
    building_source: Optional[str],
    building_source_chain: Optional[List[str]] = None,
) -> float:
    confidence = _clamp(building_confidence if building_confidence is not None else 0.72)
    quality = _building_source_quality(building_source, building_source_chain)
    return round(0.35 + 0.65 * confidence * quality, 3)


async def fetch_kp_index_safe() -> float:
    try:
        return await asyncio.wait_for(fetch_kp_index(), timeout=KP_REQUEST_TIMEOUT_S)
    except Exception:
        return 3.0


async def fetch_weather_safe(lat: float, lon: float) -> Dict:
    cache_key = _cache_key_for_latlon(lat, lon)
    try:
        weather = _attach_weather_provenance(
            await asyncio.wait_for(fetch_weather(lat, lon), timeout=SURFACE_WEATHER_REQUEST_TIMEOUT_S)
        )
        if weather.get("source") == "surface_fallback" or "surface_fallback" in weather.get("source_chain", []):
            return _make_weather_unavailable("surface_weather_timeout", fallback=weather)
        weather["available"] = bool(weather.get("available", True))
        weather["authoritative"] = _weather_is_authoritative(weather.get("source_chain", []), bool(weather.get("stale_cache")))
        weather["authority_source"] = (
            next((token for token in weather.get("source_chain", []) if token in AUTHORITATIVE_WEATHER_SOURCE_TOKENS), None)
            if weather.get("authoritative")
            else weather.get("authority_source")
        )
        return weather
    except Exception:
        cached = _cache_get(WEATHER_CACHE, cache_key, WEATHER_CACHE_TTL_S)
        if cached and _weather_is_authoritative(_normalize_source_chain(cached.get("source_chain") or [], cached.get("source")), False):
            return _attach_weather_provenance(_make_fresh_weather_cache_payload(cached))
        stale = _cache_get_stale(WEATHER_LAST_GOOD_CACHE, cache_key, WEATHER_STALE_TTL_S)
        return _attach_weather_provenance(_make_weather_unavailable("surface_weather_timeout", fallback=stale))


async def fetch_kma_upper_air_profile_safe(lat: float, lon: float) -> Optional[Dict]:
    cache_key = _cache_key_for_latlon(lat, lon)
    try:
        return await asyncio.wait_for(
            fetch_kma_upper_air_profile(lat, lon),
            timeout=KMA_UPPER_AIR_REQUEST_TIMEOUT_S
        )
    except Exception:
        stale = _cache_get_stale(UPPER_AIR_LAST_GOOD_CACHE, cache_key, UPPER_AIR_STALE_TTL_S)
        if stale:
            marked = dict(stale)
            marked["stale_cache"] = True
            return marked
        cached = _cache_get(UPPER_AIR_CACHE, cache_key, UPPER_AIR_CACHE_TTL_S)
        if cached:
            marked = dict(cached)
            marked["stale_cache"] = True
            return marked
        return None


async def fetch_kma_wind_profiler_profile_safe(lat: float, lon: float, mode: str = WIND_PROFILER_MODE) -> Optional[Dict]:
    cache_key = f"{mode}:{_cache_key_for_latlon(lat, lon)}"
    try:
        return await asyncio.wait_for(
            fetch_kma_wind_profiler_profile(lat, lon, mode),
            timeout=KMA_WIND_PROFILER_REQUEST_TIMEOUT_S
        )
    except Exception:
        stale = _cache_get_stale(WIND_PROFILER_LAST_GOOD_CACHE, cache_key, WIND_PROFILER_STALE_TTL_S)
        if stale:
            marked = dict(stale)
            marked["stale_cache"] = True
            return marked
        cached = _cache_get(WIND_PROFILER_CACHE, cache_key, WIND_PROFILER_CACHE_TTL_S)
        if cached:
            marked = dict(cached)
            marked["stale_cache"] = True
            return marked
        return None

# ============================================
# 기상 API 연동
# ============================================

async def fetch_kp_index() -> float:
    url = "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(url)
            if response.status_code == 200:
                data = response.json()
                if data: return float(data[-1].get("kp_index", 3))
    except Exception: pass
    return 3.0

async def fetch_weather(lat: float, lon: float) -> Dict:
    cache_key = f"{_round_coord(lat)},{_round_coord(lon)}"
    cached = _cache_get(WEATHER_CACHE, cache_key, WEATHER_CACHE_TTL_S)
    if cached:
        return _attach_weather_provenance(dict(cached))

    url = "https://api.open-meteo.com/v1/forecast"
    # UAV Forecast급 상세 데이터 요청
    params = {
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,dew_point_2m,weather_code,cloud_cover,wind_speed_10m,wind_direction_10m,wind_gusts_10m,visibility,precipitation_probability",
        "daily": "sunrise,sunset",
        "timezone": "Asia/Seoul"
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            res = await client.get(url, params=params)
            if res.status_code == 200:
                data = res.json()
                curr = data.get("current", {})
                daily = data.get("daily", {})
                
                # 일출/일몰 시간 포맷팅 (06:38 형태)
                sunrise = daily.get("sunrise", ["00:00"])[0].split("T")[-1][:5]
                sunset = daily.get("sunset", ["00:00"])[0].split("T")[-1][:5]

                result = {
                    "wind_speed": curr.get("wind_speed_10m", 5) / 3.6, # m/s 변환
                    "gust_speed": curr.get("wind_gusts_10m", 8) / 3.6,
                    "wind_direction": curr.get("wind_direction_10m", 0),
                    "visibility": curr.get("visibility", 10000) / 1000,
                    "precipitation_prob": curr.get("precipitation_probability", 0),
                    "weather_code": curr.get("weather_code", 0),
                    "temperature": curr.get("temperature_2m", 20),
                    "dew_point": curr.get("dew_point_2m", 15),
                    "humidity": curr.get("relative_humidity_2m", 50),
                    "cloud_cover": curr.get("cloud_cover", 0),
                    "sunrise": sunrise,
                    "sunset": sunset,
                    "source": "open_meteo_surface",
                    "source_chain": ["open_meteo_surface"],
                    "profile_source": "surface_only",
                    "stale_cache": False
                }
                _cache_set(WEATHER_LAST_GOOD_CACHE, cache_key, result)
                return _attach_weather_provenance(_cache_set(WEATHER_CACHE, cache_key, result))
    except Exception as e:
        print(f"Weather Fetch Error: {e}")
        stale = _cache_get_stale(WEATHER_LAST_GOOD_CACHE, cache_key, WEATHER_STALE_TTL_S)
        if stale:
            stale_result = dict(_mark_stale_payload(stale))
            stale_result["source"] = f'{stale_result.get("source", "open_meteo_surface")} + stale_cache'
            return _attach_weather_provenance(stale_result)

    stale = _cache_get_stale(WEATHER_LAST_GOOD_CACHE, cache_key, WEATHER_STALE_TTL_S)
    if stale:
        stale_result = dict(_mark_stale_payload(stale))
        stale_result["source"] = f'{stale_result.get("source", "open_meteo_surface")} + stale_cache'
        return _attach_weather_provenance(stale_result)

    fallback = {
        "wind_speed": 5.0, "gust_speed": 8.0, "wind_direction": 0,
        "visibility": 10.0, "precipitation_prob": 10, "temperature": 20, 
        "dew_point": 15, "humidity": 50, "cloud_cover": 20,
        "weather_code": 0, "sunrise": "06:00", "sunset": "18:00",
        "source": "surface_fallback",
        "source_chain": ["surface_fallback"],
        "profile_source": "surface_only",
        "stale_cache": False
    }
    return _attach_weather_provenance(_cache_set(WEATHER_CACHE, cache_key, fallback))

def nearest_kma_station(lat: float, lon: float) -> Dict:
    def station_dist(station: Dict) -> float:
        return math.sqrt((station["lat"] - lat) ** 2 + (station["lon"] - lon) ** 2)
    return min(KMA_DEFAULT_STATIONS, key=station_dist)

def latest_kma_cycles(now_utc: Optional[datetime] = None, limit: int = 4) -> List[str]:
    now_utc = now_utc or datetime.now(timezone.utc)
    current_hour = 12 if now_utc.hour >= 12 else 0
    current_cycle = now_utc.replace(hour=current_hour, minute=0, second=0, microsecond=0)
    cycles = []
    cursor = current_cycle
    while len(cycles) < limit:
        cycles.append(cursor.strftime("%Y%m%d%H%M"))
        cursor -= timedelta(hours=12)
    return cycles


def latest_wind_profiler_cycles(now_utc: Optional[datetime] = None, limit: int = 18) -> List[str]:
    now_utc = now_utc or datetime.now(timezone.utc)
    minute_bucket = (now_utc.minute // 10) * 10
    cursor = now_utc.replace(minute=minute_bucket, second=0, microsecond=0)
    cycles = []
    while len(cycles) < limit:
        cycles.append(cursor.strftime("%Y%m%d%H%M"))
        cursor -= timedelta(minutes=10)
    return cycles

def parse_kma_upper_air_text(text: str) -> List[Dict]:
    rows: List[Dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        numeric_values = []
        for token in tokens:
            try:
                numeric_values.append(float(token))
            except ValueError:
                continue
        if len(numeric_values) < 8:
            continue
        # KMA upp_temp rows are:
        # YYMMDDHHMI STN PA GH TA TD WD WS FLAG
        pa, gh, ta, td, wd, ws = numeric_values[2:8]
        if pa <= 0 or gh < 0 or ta < -100 or wd < -100 or ws < -100:
            continue
        rows.append({
            "pressure_hpa": pa,
            "height_m": gh,
            "temperature_c": ta,
            "dew_point_c": td,
            "wind_direction_deg": wd,
            "wind_speed_mps": ws
        })
    rows.sort(key=lambda item: item["height_m"])
    return rows


def parse_kma_wind_profiler_text(text: str) -> Dict[int, List[Dict]]:
    grouped_rows: Dict[int, List[Dict]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        numeric_values = []
        for token in tokens:
            try:
                numeric_values.append(float(token))
            except ValueError:
                continue
        # TM STN HT WD WS U V W QC
        if len(numeric_values) < 9:
            continue
        stn = int(numeric_values[1])
        ht, wd, ws, u, v, w, qc = numeric_values[2:9]
        if ht < 0 or ht > WIND_PROFILER_MAX_ALT_M or wd < -100 or ws < -100:
            continue
        grouped_rows.setdefault(stn, []).append({
            "height_m": ht,
            "wind_direction_deg": wd,
            "wind_speed_mps": ws,
            "u_component": u,
            "v_component": v,
            "w_component": w,
            "qc": qc,
        })

    for stn_rows in grouped_rows.values():
        stn_rows.sort(key=lambda item: item["height_m"])
    return grouped_rows

def interpolate_profile_layer(profile: List[Dict], mission_altitude: float) -> Optional[Dict]:
    if not profile:
        return None
    mission_altitude = max(0.0, mission_altitude)
    lower = profile[0]
    upper = profile[-1]
    for row in profile:
        if row["height_m"] <= mission_altitude:
            lower = row
        if row["height_m"] >= mission_altitude:
            upper = row
            break
    if lower["height_m"] == upper["height_m"]:
        return dict(lower)
    span = upper["height_m"] - lower["height_m"]
    if span <= 0:
        return dict(lower)
    ratio = (mission_altitude - lower["height_m"]) / span
    layer = {"height_m": mission_altitude}
    for key in ["pressure_hpa", "temperature_c", "dew_point_c", "wind_direction_deg", "wind_speed_mps"]:
        lower_value = lower.get(key)
        upper_value = upper.get(key)
        if lower_value is None and upper_value is None:
            continue
        if lower_value is None:
            layer[key] = upper_value
        elif upper_value is None:
            layer[key] = lower_value
        else:
            layer[key] = lower_value + (upper_value - lower_value) * ratio
    return layer


def build_surface_anchor(weather: Dict) -> Dict:
    surface_temp = float(weather.get("temperature", 20.0))
    surface_dew = float(weather.get("dew_point", max(surface_temp - 3.0, -20.0)))
    return {
        "pressure_hpa": 1013.25,
        "height_m": 0.0,
        "temperature_c": surface_temp,
        "dew_point_c": surface_dew,
        "wind_direction_deg": float(weather.get("wind_direction_surface", weather.get("wind_direction", 0.0))),
        "wind_speed_mps": float(weather.get("wind_speed_surface", weather.get("wind_speed", 0.0))),
    }


def build_synthetic_profile_layer(weather: Dict, altitude_m: float) -> Dict:
    surface = build_surface_anchor(weather)
    pressure_hpa = surface["pressure_hpa"] * math.exp(-altitude_m / 8434.5)
    temperature_c = surface["temperature_c"] - 0.0065 * altitude_m
    dew_point_c = surface["dew_point_c"] - 0.002 * altitude_m
    wind_speed_mps = max(0.0, surface["wind_speed_mps"] * (1.0 + min(altitude_m, 200.0) * 0.003))
    return {
        "pressure_hpa": pressure_hpa,
        "height_m": altitude_m,
        "temperature_c": temperature_c,
        "dew_point_c": dew_point_c,
        "wind_direction_deg": surface["wind_direction_deg"],
        "wind_speed_mps": wind_speed_mps,
    }


def build_profile_layers(
    weather: Dict,
    upper_air: Optional[Dict],
    wind_profiler: Optional[Dict],
    altitude_max_m: int = 200,
    step_m: int = 5
) -> List[Dict]:
    layers: List[Dict] = []
    upper_profile = None
    if upper_air and upper_air.get("layers"):
        upper_profile = [build_surface_anchor(weather)] + list(upper_air["layers"])
        upper_profile.sort(key=lambda item: item["height_m"])

    profiler_profile = wind_profiler["layers"] if wind_profiler and wind_profiler.get("layers") else None

    for altitude in range(0, altitude_max_m + 1, step_m):
        if upper_profile:
            layer = interpolate_profile_layer(upper_profile, float(altitude))
        else:
            layer = build_synthetic_profile_layer(weather, float(altitude))

        if not layer:
            layer = build_synthetic_profile_layer(weather, float(altitude))

        if profiler_profile:
            profiler_layer = interpolate_profile_layer(profiler_profile, float(altitude))
            if profiler_layer:
                layer["wind_speed_mps"] = profiler_layer["wind_speed_mps"]
                layer["wind_direction_deg"] = profiler_layer["wind_direction_deg"]

        density = calculate_air_density(layer["pressure_hpa"], layer["temperature_c"])
        layers.append({
            "height_m": round(layer["height_m"], 1),
            "pressure_hpa": round(layer["pressure_hpa"], 1),
            "temperature_c": round(layer["temperature_c"], 1),
            "dew_point_c": round(layer["dew_point_c"], 1),
            "wind_direction_deg": round(layer["wind_direction_deg"], 1),
            "wind_speed_mps": round(layer["wind_speed_mps"], 2),
            "density": density
        })

    return layers

def calculate_air_density(pressure_hpa: float, temperature_c: float) -> float:
    pressure_pa = pressure_hpa * 100.0
    temperature_k = temperature_c + 273.15
    if temperature_k <= 0:
        return 1.225
    return round(pressure_pa / (287.05 * temperature_k), 3)


async def fetch_wis2_station_metadata(stn: int) -> Optional[Dict[str, Any]]:
    if stn in WIS2_STATION_CACHE:
        return WIS2_STATION_CACHE[stn]

    url = WIS2_STATION_ENDPOINT.format(stn=stn)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(url)
            if response.status_code != 200:
                return None
            payload = response.json()
    except Exception:
        return None

    properties = payload.get("properties") or {}
    geometry = payload.get("geometry") or {}
    coords = geometry.get("coordinates") or []
    if len(coords) < 2:
        return None

    station = {
        "id": stn,
        "name": properties.get("name") or f"STN-{stn}",
        "lat": float(coords[1]),
        "lon": float(coords[0]),
    }
    WIS2_STATION_CACHE[stn] = station
    return station

async def fetch_kma_upper_air_profile(lat: float, lon: float) -> Optional[Dict]:
    if not KMA_API_KEY:
        return None

    cache_key = f"{_round_coord(lat)},{_round_coord(lon)}"
    cached = _cache_get(UPPER_AIR_CACHE, cache_key, UPPER_AIR_CACHE_TTL_S)
    if cached is not None:
        return cached

    stations = sorted(
        KMA_DEFAULT_STATIONS,
        key=lambda station: math.sqrt((station["lat"] - lat) ** 2 + (station["lon"] - lon) ** 2)
    )
    async with httpx.AsyncClient(timeout=8) as client:
        for station in stations:
            for cycle in latest_kma_cycles():
                url = "https://apihub.kma.go.kr/api/typ01/url/upp_temp.php"
                params = {
                    "tm": cycle,
                    "stn": station["id"],
                    "pa": 0,
                    "help": 0,
                    "authKey": KMA_API_KEY
                }
                try:
                    response = await client.get(url, params=params)
                    if response.status_code != 200:
                        continue
                    rows = parse_kma_upper_air_text(response.text)
                    if rows:
                        result = {
                            "station_id": station["id"],
                            "station_name": station["name"],
                            "observed_at_utc": cycle,
                            "layers": rows,
                            "stale_cache": False
                        }
                        _cache_set(UPPER_AIR_LAST_GOOD_CACHE, cache_key, result)
                        return _cache_set(UPPER_AIR_CACHE, cache_key, result)
                except Exception:
                    continue

    stale = _cache_get_stale(UPPER_AIR_LAST_GOOD_CACHE, cache_key, UPPER_AIR_STALE_TTL_S)
    if stale:
        return _mark_stale_payload(stale)

    return _cache_set(UPPER_AIR_CACHE, cache_key, None)


async def fetch_kma_wind_profiler_profile(lat: float, lon: float, mode: str = WIND_PROFILER_MODE) -> Optional[Dict]:
    if not KMA_API_KEY:
        return None

    cache_key = f"{mode}:{_round_coord(lat)},{_round_coord(lon)}"
    cached = _cache_get(WIND_PROFILER_CACHE, cache_key, WIND_PROFILER_CACHE_TTL_S)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=10) as client:
        for cycle in latest_wind_profiler_cycles():
            url = "https://apihub.kma.go.kr/api/typ01/url/kma_wpf.php"
            params = {
                "tm": cycle,
                "stn": 0,
                "mode": mode,
                "help": 0,
                "authKey": KMA_API_KEY
            }
            try:
                response = await client.get(url, params=params)
                if response.status_code != 200:
                    continue
                grouped_rows = parse_kma_wind_profiler_text(response.text)
                if not grouped_rows:
                    continue
            except Exception:
                continue

            station_candidates: List[Dict[str, Any]] = []
            for stn, layers in grouped_rows.items():
                metadata = await fetch_wis2_station_metadata(stn)
                if not metadata:
                    continue
                station_candidates.append({
                    "station": metadata,
                    "layers": layers
                })

            if not station_candidates:
                continue

            station_candidates.sort(
                key=lambda item: math.sqrt((item["station"]["lat"] - lat) ** 2 + (item["station"]["lon"] - lon) ** 2)
            )
            selected = station_candidates[0]
            result = {
                "station_id": selected["station"]["id"],
                "station_name": selected["station"]["name"],
                "observed_at_utc": cycle,
                "mode": mode,
                "layers": selected["layers"],
                "stale_cache": False
            }
            _cache_set(WIND_PROFILER_LAST_GOOD_CACHE, cache_key, result)
            return _cache_set(WIND_PROFILER_CACHE, cache_key, result)

    stale = _cache_get_stale(WIND_PROFILER_LAST_GOOD_CACHE, cache_key, WIND_PROFILER_STALE_TTL_S)
    if stale:
        return _mark_stale_payload(stale)

    return _cache_set(WIND_PROFILER_CACHE, cache_key, None)

# ============================================
# 게이트 계산 로직
# ============================================

def calculate_fcanyon(h: float, w: float) -> float:
    hw_ratio = h / w if w > 0 else 1
    return 1 + 0.3 * min(hw_ratio, 3)

def calculate_ews(wind_speed: float, fcanyon: float, alignment_factor: float = 1.0) -> float:
    return wind_speed * fcanyon * 1.2 * 1.3 * alignment_factor

def evaluate_gate0(req: EvaluationRequest, weather: Dict) -> GateResult:
    reasons = []
    if req.no_fly_zone: reasons.append("❌비행금지구역")
    if req.crowd_area: reasons.append("❌인파밀집지역")
    
    rain_prob = weather.get("precipitation_prob", 0)
    w_code = weather.get("weather_code", 0)
    is_raining = w_code >= 51
    
    if rain_prob >= 70: reasons.append(f"❌강수확률 높음({rain_prob}%)")
    elif is_raining: reasons.append("❌현재 비/눈")

    if reasons:
        return GateResult(gate="Gate0", status=JudgmentLevel.NO_GO, reason=" / ".join(reasons))
    return GateResult(gate="Gate0", status=JudgmentLevel.GO, reason="✅비행 방해 요소 없음")

def evaluate_gate1(req: EvaluationRequest) -> GateResult:
    gps = req.gps_locked
    glo = req.glonass_locked
    if gps >= 8 and glo >= 4:
        return GateResult(gate="Gate1", status=JudgmentLevel.GO, reason=f"✅양호 (GPS:{gps}, GLO:{glo})")
    return GateResult(gate="Gate1", status=JudgmentLevel.NO_GO, reason=f"❌위성신호 부족 (GPS:{gps}, GLO:{glo})", threshold="GPS 8+, GLO 4+")

def evaluate_gate2(weather: Dict) -> GateResult:
    vis = weather.get("visibility", 10)
    if vis >= 3:
        return GateResult(gate="Gate2", status=JudgmentLevel.GO, reason=f"✅양호 (시정 {vis:.1f}km)")
    elif vis < 1:
        return GateResult(gate="Gate2", status=JudgmentLevel.NO_GO, reason=f"❌시야 미확보 ({vis:.1f}km)", threshold="1km")
    return GateResult(gate="Gate2", status=JudgmentLevel.RESTRICT, reason=f"⚠️안개 주의 ({vis:.1f}km)", threshold="3km")

def evaluate_gate3(ews: float, drone_spec: Dict) -> GateResult:
    limit = drone_spec["wind"]
    msg = f"빌딩풍 {ews:.1f}m/s vs 한계 {limit}m/s"
    if ews <= limit * 0.8:
        return GateResult(gate="Gate3", status=JudgmentLevel.GO, reason=f"✅바람 잔잔함 ({msg})", value=ews, threshold=str(limit))
    elif ews <= limit:
        return GateResult(gate="Gate3", status=JudgmentLevel.RESTRICT, reason=f"⚠️주의 필요 ({msg})", value=ews, threshold=str(limit))
    return GateResult(gate="Gate3", status=JudgmentLevel.NO_GO, reason=f"❌비행 불가: 강풍 ({msg})", value=ews, threshold=str(limit))

def evaluate_gate4(gust: float, drone_spec: Dict) -> GateResult:
    limit = drone_spec["gust"]
    effective_gust = gust * 1.3
    msg = f"순간돌풍 {effective_gust:.1f}m/s vs 한계 {limit}m/s"
    if effective_gust <= limit:
        return GateResult(gate="Gate4", status=JudgmentLevel.GO, reason=f"✅안전 ({msg})", value=effective_gust)
    return GateResult(gate="Gate4", status=JudgmentLevel.NO_GO, reason=f"❌비행 위험: 돌풍 ({msg})", value=effective_gust, threshold=str(limit))


def resolve_drone_spec(drone_type: str) -> Dict:
    try:
        return DRONE_SPECS[DroneModel(drone_type)]
    except ValueError:
        return DRONE_SPECS[DroneModel.MAVIC_3]


def haversine_distance_m(a: RoutePoint, b: RoutePoint) -> float:
    radius_m = 6371000
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = math.radians(b.lat - a.lat)
    dlon = math.radians(b.lon - a.lon)
    h = (
        math.sin(dlat / 2) ** 2 +
        math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_m * math.asin(math.sqrt(h))


def interpolate_route_point(a: RoutePoint, b: RoutePoint, ratio: float) -> Dict[str, float]:
    return {
        "lat": a.lat + (b.lat - a.lat) * ratio,
        "lon": a.lon + (b.lon - a.lon) * ratio,
    }


def worst_judgment(statuses: List[JudgmentLevel]) -> JudgmentLevel:
    if JudgmentLevel.NO_GO in statuses:
        return JudgmentLevel.NO_GO
    if JudgmentLevel.RESTRICT in statuses:
        return JudgmentLevel.RESTRICT
    return JudgmentLevel.GO


def estimate_route_building_height(lat: float, lon: float, with_metadata: bool = False):
    try:
        from building_height import predict_building_height
        result = predict_building_height(lat, lon)
        height = float(result.get("estimated_height_m", 25.0))
        if with_metadata:
            source_chain = _normalize_source_chain(result.get("source_chain") or [result.get("source") or "building_height_heuristic"])
            return {
                "height_m": height,
                "estimated_floors": int(result.get("estimated_floors", 0) or 0),
                "confidence": _clamp(result.get("building_confidence", result.get("confidence", 0.6)), 0.0, 1.0),
                "source": result.get("source", "building_height_heuristic"),
                "profile_source": result.get("profile_source", "coordinate_based"),
                "source_chain": source_chain,
                "method": result.get("method"),
            }
        return height
    except Exception:
        fallback_height = round(18.0 + abs(math.sin(lat * 41.7 + lon * 17.3)) * 35.0, 1)
        if with_metadata:
            return {
                "height_m": fallback_height,
                "estimated_floors": max(1, int(round(fallback_height / 3.3))),
                "confidence": 0.45,
                "source": "building_height_fallback",
                "profile_source": "coordinate_based",
                "source_chain": ["building_height_fallback"],
                "method": "heuristic_fallback",
            }
        return fallback_height


# ============================================
# API 엔드포인트
# ============================================

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "version": app.version,
        "kma_configured": bool(KMA_API_KEY)
    }


@app.get("/api/official-gis/readiness")
async def get_official_gis_readiness_api():
    return _official_gis_readiness()

@app.get("/api/kma/status")
async def get_kma_status(lat: float = 37.558056, lon: float = 126.708333):
    if not KMA_API_KEY:
        return {
            "configured": False,
            "available": False,
            "reason": "missing_kma_api_key"
        }

    profile = await fetch_kma_upper_air_profile(lat, lon)
    if not profile:
        return {
            "configured": True,
            "available": False,
            "reason": "no_recent_profile_or_key_not_authorized"
        }

    return {
        "configured": True,
        "available": True,
        "station_id": profile["station_id"],
        "station_name": profile["station_name"],
        "observed_at_utc": profile["observed_at_utc"],
        "layer_count": len(profile["layers"])
    }

@app.get("/api/kp")
async def get_kp_index_api():
    kp = await fetch_kp_index()
    return {"kp_index": kp}

@app.get("/api/road-width")
async def get_road_width_api(lat: float, lon: float, road_name: Optional[str] = None):
    return await fetch_road_width_evidence(lat, lon, road_name=road_name)


@app.get("/api/canyon-width")
async def get_canyon_width_api(
    lat: float,
    lon: float,
    road_name: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    if OFFICIAL_GIS_BRIDGE_INBOUND_TOKEN:
        expected = f"Bearer {OFFICIAL_GIS_BRIDGE_INBOUND_TOKEN}"
        if not authorization or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="official GIS bridge authorization required")
    return await fetch_canyon_width_evidence(lat, lon, road_name=road_name)

@app.get("/api/weather")
async def get_weather_api(lat: float = 37.5665, lon: float = 126.9780):
    w = await fetch_weather_safe(lat, lon)
    kp = await fetch_kp_index_safe()
    w["kp_index"] = kp
    upper_air, wind_profiler = await asyncio.gather(
        fetch_kma_upper_air_profile_safe(lat, lon),
        fetch_kma_wind_profiler_profile_safe(lat, lon)
    )
    source_suffixes = []
    if w.get("stale_cache"):
        source_suffixes.append("stale_surface_cache")
    if upper_air and upper_air.get("stale_cache"):
        source_suffixes.append("stale_upper_air_cache")
    if wind_profiler and wind_profiler.get("stale_cache"):
        source_suffixes.append("stale_wind_profiler_cache")
    if upper_air and wind_profiler:
        w["source"] = "kma_radiosonde + kma_wind_profiler + open_meteo_surface"
    elif upper_air:
        w["source"] = "kma_radiosonde + open_meteo_surface"
    elif wind_profiler:
        w["source"] = "kma_wind_profiler + open_meteo_surface"
    if source_suffixes:
        w["source"] = f'{w.get("source", "open_meteo_surface")} + {", ".join(source_suffixes)}'
    w = _attach_weather_provenance(w, upper_air, wind_profiler)
    weather_evidence = _build_weather_evidence(w, upper_air, wind_profiler)
    return {"weather": w, "weather_evidence": weather_evidence}

@app.post("/api/evaluate", response_model=EvaluationResponse)
async def evaluate_flight(request: EvaluationRequest):
    # 1. Weather
    if request.wind_speed is not None:
        # 수동 입력 시
        weather = {
            "wind_speed": request.wind_speed,
            "gust_speed": request.gust_speed or request.wind_speed * 1.5,
            "visibility": request.visibility or 10.0,
            "precipitation_prob": request.precipitation_prob or 0,
            "weather_code": 0,
            "temperature": request.temperature or 20,
            "humidity": request.humidity or 50,
            "dew_point": 15,
            "cloud_cover": 20,
            "wind_direction": 0,
            "sunrise": "06:00",
            "sunset": "18:00",
            "source": "manual_surface_input",
            "source_chain": ["manual_surface_input"],
            "profile_source": "surface_only",
            "stale_cache": False,
            "available": True,
            "authoritative": False,
            "authority_source": None,
        }
        kp = request.kp_index or 3.0
    else:
        weather = await fetch_weather_safe(request.latitude, request.longitude)
        kp = await fetch_kp_index_safe()
    
    weather["kp_index"] = kp
    upper_air, wind_profiler = await asyncio.gather(
        fetch_kma_upper_air_profile_safe(request.latitude, request.longitude),
        fetch_kma_wind_profiler_profile_safe(request.latitude, request.longitude)
    )
    selected_layer = None
    wind_profiler_layer = None
    source_suffixes = []
    if weather.get("stale_cache"):
        source_suffixes.append("stale_surface_cache")
    if upper_air:
        selected_layer = interpolate_profile_layer(upper_air["layers"], request.mission_altitude)
        if selected_layer:
            weather["wind_speed_surface"] = weather["wind_speed"]
            weather["gust_speed_surface"] = weather["gust_speed"]
            weather["wind_direction_surface"] = weather["wind_direction"]
            weather["wind_speed"] = round(selected_layer["wind_speed_mps"], 3)
            weather["wind_direction"] = round(selected_layer["wind_direction_deg"], 1)
            weather["gust_speed"] = round(max(weather["gust_speed"], selected_layer["wind_speed_mps"] * 1.25), 3)
            weather["upper_air_density"] = calculate_air_density(
                selected_layer["pressure_hpa"],
                selected_layer["temperature_c"]
            )
            weather["source"] = "kma_radiosonde + open_meteo_surface"
            if upper_air.get("stale_cache"):
                source_suffixes.append("stale_upper_air_cache")

    if wind_profiler:
        wind_profiler_layer = interpolate_profile_layer(wind_profiler["layers"], request.mission_altitude)
        if wind_profiler_layer:
            weather["wind_speed_surface"] = weather.get("wind_speed_surface", weather["wind_speed"])
            weather["gust_speed_surface"] = weather.get("gust_speed_surface", weather["gust_speed"])
            weather["wind_direction_surface"] = weather.get("wind_direction_surface", weather.get("wind_direction", 0))
            weather["wind_speed"] = round(wind_profiler_layer["wind_speed_mps"], 3)
            weather["wind_direction"] = round(wind_profiler_layer["wind_direction_deg"], 1)
            weather["gust_speed"] = round(max(weather["gust_speed"], wind_profiler_layer["wind_speed_mps"] * 1.2), 3)
            if selected_layer:
                selected_layer["wind_speed_mps"] = wind_profiler_layer["wind_speed_mps"]
                selected_layer["wind_direction_deg"] = wind_profiler_layer["wind_direction_deg"]
            else:
                selected_layer = {
                    "height_m": wind_profiler_layer["height_m"],
                    "pressure_hpa": 1013.25,
                    "temperature_c": weather.get("temperature", 20.0),
                    "dew_point_c": weather.get("dew_point", 15.0),
                    "wind_direction_deg": wind_profiler_layer["wind_direction_deg"],
                    "wind_speed_mps": wind_profiler_layer["wind_speed_mps"],
                }
                weather["upper_air_density"] = calculate_air_density(
                    selected_layer["pressure_hpa"],
                    selected_layer["temperature_c"]
                )
            weather["source"] = (
                "kma_radiosonde + kma_wind_profiler + open_meteo_surface"
                if upper_air else
                "kma_wind_profiler + open_meteo_surface"
            )
            if wind_profiler.get("stale_cache"):
                source_suffixes.append("stale_wind_profiler_cache")

    if source_suffixes:
        weather["source"] = f'{weather.get("source", "open_meteo_surface")} + {", ".join(source_suffixes)}'
    weather = _attach_weather_provenance(weather, upper_air, wind_profiler)
    if request.weather_evidence:
        weather["available"] = request.weather_evidence.get("available", weather.get("available"))
        weather["authoritative"] = request.weather_evidence.get("authoritative", weather.get("authoritative"))
        weather["authority_source"] = request.weather_evidence.get("authority_source", weather.get("authority_source"))
    profile_source = weather.get("profile_source", "surface_only")

    # 2. Drone Specs
    spec = DRONE_SPECS[request.drone_model]
    
    # 3. Urban Factors
    align_factor = {"일치": 1.3, "직각": 0.9, "불명": 1.1}.get(request.wind_alignment, 1.0)
    building_evidence = _build_building_evidence(request)
    building_source_chain = building_evidence["source_chain"]
    if not building_source_chain:
        building_source_chain = ["manual_input"]
    building_source = request.building_source or building_source_chain[0]
    building_profile_source = request.building_profile_source or "manual_input"
    building_confidence = float(building_evidence["confidence"])
    raw_road_evidence = request.road_evidence if request.road_evidence is not None else await fetch_road_width_evidence(request.latitude, request.longitude)
    road_evidence = _normalize_road_evidence(raw_road_evidence)
    raw_canyon_evidence = request.canyon_evidence if request.canyon_evidence is not None else await fetch_canyon_width_evidence(request.latitude, request.longitude)
    canyon_evidence = _normalize_canyon_evidence(raw_canyon_evidence)
    weather_evidence = _build_weather_evidence(weather, upper_air, wind_profiler)
    input_quality = _build_input_quality(building_evidence, road_evidence, canyon_evidence, weather_evidence)
    effective_street_width = float(canyon_evidence["facade_gap_m"] or 0.0)
    hw_ratio = request.building_height / effective_street_width if effective_street_width > 0 else None

    if input_quality["status"] == "hold":
        gates = [
            GateResult(
                gate="Authority",
                status=JudgmentLevel.HOLD,
                reason=" / ".join(input_quality["reasons"]) or "authoritative inputs unavailable",
            )
        ]
        profile_max_altitude = int(max(50, min(200, math.ceil(request.mission_altitude / 5) * 5)))
        profile_layers = build_profile_layers(
            weather,
            upper_air,
            wind_profiler,
            altitude_max_m=profile_max_altitude,
            step_m=5
        )
        urban_factors = {
            "H": request.building_height,
            "W": canyon_evidence.get("facade_gap_m"),
            "H_W_ratio": round(hw_ratio, 2) if hw_ratio is not None else None,
            "official_road_right_of_way_width_m": road_evidence.get("official_road_right_of_way_width_m"),
            "Fcanyon": None,
            "Fcanyon_raw": None,
            "building_canyon_weight": None,
            "building_source": building_source,
            "building_profile_source": building_profile_source,
            "building_source_chain": building_source_chain,
            "building_confidence": round(building_confidence, 2),
            "road_width_source": road_evidence.get("source"),
            "canyon_width_source": canyon_evidence.get("source"),
            "alignment_factor": align_factor,
            "mission_altitude": request.mission_altitude,
        }
        weather_source_chain = weather.get("source_chain", [])
        evaluation_source_chain = _normalize_source_chain(weather_source_chain, building_source_chain, road_evidence.get("source_chain"), canyon_evidence.get("source_chain"))
        stale_cache = bool(
            weather.get("stale_cache")
            or (upper_air and upper_air.get("stale_cache"))
            or (wind_profiler and wind_profiler.get("stale_cache"))
        )
        return EvaluationResponse(
            timestamp=datetime.now().isoformat(),
            location={"lat": request.latitude, "lon": request.longitude},
            weather=weather,
            urban_factors=urban_factors,
            gates=gates,
            final_judgment=JudgmentLevel.HOLD,
            ews=None,
            drone_spec=spec,
            source="backend",
            profile_source=profile_source,
            source_chain=evaluation_source_chain,
            weather_source_chain=weather_source_chain,
            building_source=building_source,
            building_profile_source=building_profile_source,
            building_source_chain=building_source_chain,
            building_confidence=round(building_confidence, 2),
            stale_cache=stale_cache,
            upper_air_profile={
                "station_id": upper_air["station_id"],
                "station_name": upper_air["station_name"],
                "observed_at_utc": upper_air["observed_at_utc"],
                "layer_count": len(upper_air["layers"]),
                "stale_cache": upper_air.get("stale_cache", False)
            } if upper_air and "station_id" in upper_air else None,
            wind_profiler_profile={
                "station_id": wind_profiler["station_id"],
                "station_name": wind_profiler["station_name"],
                "observed_at_utc": wind_profiler["observed_at_utc"],
                "mode": wind_profiler["mode"],
                "layer_count": len(wind_profiler["layers"]),
                "stale_cache": wind_profiler.get("stale_cache", False)
            } if wind_profiler and "station_id" in wind_profiler else None,
            selected_layer={
                "height_m": round(selected_layer["height_m"], 1),
                "pressure_hpa": round(selected_layer["pressure_hpa"], 1),
                "temperature_c": round(selected_layer["temperature_c"], 1),
                "dew_point_c": round(selected_layer["dew_point_c"], 1),
                "wind_direction_deg": round(selected_layer["wind_direction_deg"], 1),
                "wind_speed_mps": round(selected_layer["wind_speed_mps"], 2),
                "density": weather.get("upper_air_density")
            } if selected_layer else None,
            profile_layers=profile_layers,
            building_evidence=building_evidence,
            road_evidence=road_evidence,
            canyon_evidence=canyon_evidence,
            weather_evidence=weather_evidence,
            input_quality=input_quality,
        )

    building_canyon_weight = _resolve_building_canyon_weight(
        building_confidence,
        building_source,
        building_source_chain,
    )
    fcanyon_raw = calculate_fcanyon(request.building_height, effective_street_width)
    fcanyon = 1 + (fcanyon_raw - 1) * building_canyon_weight
    ews = calculate_ews(weather["wind_speed"], fcanyon, align_factor)
    
    urban_factors = {
        "H": request.building_height, "W": effective_street_width,
        "H_W_ratio": round(hw_ratio, 2) if hw_ratio is not None else None,
        "official_road_right_of_way_width_m": road_evidence.get("official_road_right_of_way_width_m"),
        "Fcanyon": round(fcanyon, 2),
        "Fcanyon_raw": round(fcanyon_raw, 2),
        "building_canyon_weight": round(building_canyon_weight, 3),
        "building_source": building_source,
        "building_profile_source": building_profile_source,
        "building_source_chain": building_source_chain,
        "building_confidence": round(building_confidence, 2),
        "road_width_source": road_evidence.get("source"),
        "canyon_width_source": canyon_evidence.get("source"),
        "alignment_factor": align_factor,
        "mission_altitude": request.mission_altitude
    }
    weather_source_chain = weather.get("source_chain", [])
    evaluation_source_chain = _normalize_source_chain(weather_source_chain, building_source_chain, road_evidence.get("source_chain"), canyon_evidence.get("source_chain"))
    stale_cache = bool(
        weather.get("stale_cache")
        or (upper_air and upper_air.get("stale_cache"))
        or (wind_profiler and wind_profiler.get("stale_cache"))
    )
    
    # 4. Gate Judgments
    g0 = evaluate_gate0(request, weather)
    g1 = evaluate_gate1(request)
    g2 = evaluate_gate2(weather)
    g3 = evaluate_gate3(ews, spec)
    g4 = evaluate_gate4(weather["gust_speed"], spec)
    
    gates = [g0, g1, g2, g3, g4]
    
    # 5. Final
    final = JudgmentLevel.GO
    if any(g.status == JudgmentLevel.NO_GO for g in gates): final = JudgmentLevel.NO_GO
    elif any(g.status == JudgmentLevel.RESTRICT for g in gates): final = JudgmentLevel.RESTRICT
    profile_max_altitude = int(max(50, min(200, math.ceil(request.mission_altitude / 5) * 5)))
    profile_layers = build_profile_layers(
        weather,
        upper_air,
        wind_profiler,
        altitude_max_m=profile_max_altitude,
        step_m=5
    )
    
    return EvaluationResponse(
        timestamp=datetime.now().isoformat(),
        location={"lat": request.latitude, "lon": request.longitude},
        weather=weather, urban_factors=urban_factors, gates=gates,
        final_judgment=final, ews=round(ews, 2), drone_spec=spec,
        source="backend",
        profile_source=profile_source,
        source_chain=evaluation_source_chain,
        weather_source_chain=weather_source_chain,
        building_source=building_source,
        building_profile_source=building_profile_source,
        building_source_chain=building_source_chain,
        building_confidence=round(building_confidence, 2),
        building_evidence=building_evidence,
        road_evidence=road_evidence,
        canyon_evidence=canyon_evidence,
        weather_evidence=weather_evidence,
        input_quality=input_quality,
        upper_air_profile={
            "station_id": upper_air["station_id"],
            "station_name": upper_air["station_name"],
            "observed_at_utc": upper_air["observed_at_utc"],
            "layer_count": len(upper_air["layers"]),
            "stale_cache": upper_air.get("stale_cache", False)
        } if upper_air and "station_id" in upper_air else None,
        wind_profiler_profile={
            "station_id": wind_profiler["station_id"],
            "station_name": wind_profiler["station_name"],
            "observed_at_utc": wind_profiler["observed_at_utc"],
            "mode": wind_profiler["mode"],
            "layer_count": len(wind_profiler["layers"]),
            "stale_cache": wind_profiler.get("stale_cache", False)
        } if wind_profiler and "station_id" in wind_profiler else None,
        selected_layer={
            "height_m": round(selected_layer["height_m"], 1),
            "pressure_hpa": round(selected_layer["pressure_hpa"], 1),
            "temperature_c": round(selected_layer["temperature_c"], 1),
            "dew_point_c": round(selected_layer["dew_point_c"], 1),
            "wind_direction_deg": round(selected_layer["wind_direction_deg"], 1),
            "wind_speed_mps": round(selected_layer["wind_speed_mps"], 2),
            "density": weather.get("upper_air_density")
        } if selected_layer else None,
        profile_layers=profile_layers,
        stale_cache=stale_cache
    )


@app.post("/api/corridor-analysis")
async def analyze_corridor(request: CorridorAnalysisRequest):
    segment_count = max(2, min(request.segment_count, 20))
    total_distance = haversine_distance_m(request.point_a, request.point_b)
    midpoint = interpolate_route_point(request.point_a, request.point_b, 0.5)
    weather = await fetch_weather_safe(midpoint["lat"], midpoint["lon"])
    spec = resolve_drone_spec(request.drone_type)
    weather_source_chain = weather.get("source_chain", [])
    segments = []

    for idx in range(segment_count):
        start_ratio = idx / segment_count
        end_ratio = (idx + 1) / segment_count
        mid_ratio = (start_ratio + end_ratio) / 2
        point = interpolate_route_point(request.point_a, request.point_b, mid_ratio)
        building = estimate_route_building_height(point["lat"], point["lon"], with_metadata=True)
        building_height = float(building["height_m"])
        street_width = 12.0 + abs(math.cos(point["lat"] * 29.0 + point["lon"] * 13.0)) * 14.0
        building_confidence = _clamp(building.get("confidence", 0.45))
        building_source = building.get("source", "building_height_heuristic")
        building_source_chain = _normalize_source_chain(building.get("source_chain") or [building_source])
        building_canyon_weight = _resolve_building_canyon_weight(
            building_confidence,
            building_source,
            building_source_chain,
        )
        fcanyon_raw = calculate_fcanyon(building_height, street_width)
        fcanyon_effective = 1 + (fcanyon_raw - 1) * building_canyon_weight
        ews = calculate_ews(weather["wind_speed"], fcanyon_effective, 1.1)
        wind_gate = evaluate_gate3(ews, spec)
        gust_gate = evaluate_gate4(weather["gust_speed"], spec)

        rain_blocked = weather.get("precipitation_prob", 0) >= 70 or weather.get("weather_code", 0) >= 51
        visibility = weather.get("visibility", 10)
        vis_status = (
            JudgmentLevel.GO if visibility >= 3
            else JudgmentLevel.NO_GO if visibility < 1
            else JudgmentLevel.RESTRICT
        )
        segment_status = worst_judgment([
            JudgmentLevel.NO_GO if rain_blocked else JudgmentLevel.GO,
            vis_status,
            wind_gate.status,
            gust_gate.status
        ])

        reasons = []
        if rain_blocked:
            reasons.append("강수 조건")
        if vis_status != JudgmentLevel.GO:
            reasons.append(f"시정 {visibility:.1f}km")
        if wind_gate.status != JudgmentLevel.GO:
            reasons.append(wind_gate.reason.replace("✅", "").replace("⚠️", "").replace("❌", "").strip())
        if gust_gate.status != JudgmentLevel.GO:
            reasons.append(gust_gate.reason.replace("✅", "").replace("⚠️", "").replace("❌", "").strip())

        segments.append({
            "id": idx + 1,
            "start_percent": round(start_ratio * 100, 1),
            "end_percent": round(end_ratio * 100, 1),
            "status": segment_status.value,
            "wind_speed": round(weather["wind_speed"], 1),
            "ews": round(ews, 1),
            "building_height": round(building_height, 1),
            "building_floors": building.get("estimated_floors"),
            "building_confidence": round(building_confidence, 2),
            "building_source": building_source,
            "building_profile_source": building.get("profile_source"),
            "building_source_chain": building_source_chain,
            "building_canyon_weight": round(building_canyon_weight, 3),
            "fcanyon_raw": round(fcanyon_raw, 2),
            "fcanyon_effective": round(fcanyon_effective, 2),
            "street_width": round(street_width, 1),
            "weather_source": weather.get("source", "unknown"),
            "weather_source_chain": weather_source_chain,
            "weather_profile_source": weather.get("profile_source"),
            "reason": " / ".join(reasons) if reasons else "안전 통과 가능"
        })

    overall = worst_judgment([JudgmentLevel(segment["status"]) for segment in segments])
    max_building_height = max(segment["building_height"] for segment in segments)
    recommended_altitude = request.altitude
    if overall == JudgmentLevel.NO_GO:
        recommended_altitude = max(request.altitude + 20, max_building_height + 15)
    elif overall == JudgmentLevel.RESTRICT:
        recommended_altitude = max(request.altitude, max_building_height + 10)
    building_source_chain = _normalize_source_chain(*(segment["building_source_chain"] for segment in segments))
    building_confidence = round(
        sum(float(segment.get("building_confidence", 0.0)) for segment in segments) / len(segments),
        2
    )

    return {
        "distance_m": round(total_distance),
        "flight_time_min": round(total_distance / 10 / 60, 1),
        "overall_judgment": overall.value,
        "segments": segments,
        "recommended_altitude": round(recommended_altitude, 1),
        "alternative_route": "고층/강풍 구간 우회 권장" if overall == JudgmentLevel.NO_GO else None,
        "weather_source": weather.get("source", "unknown"),
        "weather_source_chain": weather_source_chain,
        "weather_profile_source": weather.get("profile_source"),
        "building_source": "building_height_heuristic",
        "building_source_chain": building_source_chain,
        "building_confidence": building_confidence,
        "source_chain": _normalize_source_chain(weather_source_chain, building_source_chain),
        "stale_cache": bool(weather.get("stale_cache")),
        "drone_spec": spec
    }


def _build_official_building_height_evidence(footprint: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(footprint, dict):
        return None
    if not (
        footprint.get("official_footprint_available")
        and footprint.get("official_geometry_receipt")
        and footprint.get("official_selection_match")
    ):
        return None
    properties = footprint.get("properties") if isinstance(footprint.get("properties"), dict) else {}
    floor_count = _parse_loose_number(properties.get("gro_flo_co"))
    official_height = next(
        (
            _parse_loose_number(properties.get(key))
            for key in ("buld_hg", "bldg_hg", "building_height_m")
            if _parse_loose_number(properties.get(key)) is not None
        ),
        None,
    )
    source_chain = _normalize_source_chain(footprint.get("source_chain"), "official_building_height")
    building_name = footprint.get("display_name") or properties.get("buld_nm") or properties.get("buld_nm_dc")
    parsed_floors = int(round(floor_count)) if floor_count is not None and floor_count > 0 else None
    if official_height is not None and official_height > 0:
        return {
            "available": True,
            "official_available": True,
            "estimated_height_m": round(official_height, 1),
            "estimated_floors": parsed_floors,
            "max_possible_height_m": round(official_height, 1),
            "zoning_type": properties.get("zoning_type"),
            "far_percent": _parse_loose_number(properties.get("far_percent")),
            "bcr_percent": _parse_loose_number(properties.get("bcr_percent")),
            "confidence": 0.99,
            "building_confidence": 0.99,
            "method": "official_building_height",
            "source": "official_building_height",
            "profile_source": "official_verified",
            "source_chain": source_chain,
            "source_status": "official_verified",
            "official_building_data": True,
            "official_footprint_available": True,
            "official_geometry_receipt": True,
            "official_selection_match": True,
            "display_name": building_name,
            "height_estimated_from_official_floors": False,
            "field_sources": {
                "estimated_height_m": {"source": "vworld_wfs", "status": "official_verified", "property_key": "buld_hg", "value": official_height},
                "estimated_floors": {"source": "vworld_wfs", "status": "official_verified", "property_key": "gro_flo_co", "value": parsed_floors},
            },
            "receipt": {
                "kind": "official_building_height",
                "geometry_receipt": True,
                "selection_match": True,
                "source_chain": source_chain,
            },
            "stale_cache": False,
        }
    if parsed_floors is None:
        return None
    derived_height = round(parsed_floors * 3.3, 1)
    derived_source_chain = _normalize_source_chain(footprint.get("source_chain"), "official_floor_count_derived")
    return {
        "available": True,
        "official_available": False,
        "estimated_height_m": derived_height,
        "estimated_floors": parsed_floors,
        "max_possible_height_m": derived_height,
        "zoning_type": properties.get("zoning_type"),
        "far_percent": _parse_loose_number(properties.get("far_percent")),
        "bcr_percent": _parse_loose_number(properties.get("bcr_percent")),
        "confidence": 0.86,
        "building_confidence": 0.86,
        "method": "official_floor_count_derived",
        "source": "official_floor_count_derived",
        "profile_source": "official_floor_count",
        "source_chain": derived_source_chain,
        "source_status": "estimated",
        "official_building_data": True,
        "official_footprint_available": True,
        "official_geometry_receipt": True,
        "official_selection_match": True,
        "display_name": building_name,
        "height_estimated_from_official_floors": True,
        "field_sources": {
            "estimated_height_m": {"source": "official_floor_count_derived", "status": "estimated", "property_key": "gro_flo_co", "value": derived_height},
            "estimated_floors": {"source": "vworld_wfs", "status": "official_verified", "property_key": "gro_flo_co", "value": parsed_floors},
        },
        "receipt": {
            "kind": "official_floor_count_derived",
            "geometry_receipt": True,
            "selection_match": True,
            "source_chain": derived_source_chain,
        },
        "stale_cache": False,
    }


# Building Height API
try:
    from building_height import predict_building_height
    @app.get("/api/building-height")
    async def get_building_height(lat: float, lon: float):
        footprint = await lookup_building_footprint(lat, lon)
        official_height = _build_official_building_height_evidence(footprint)
        if official_height:
            return official_height
        return predict_building_height(lat, lon)
except ImportError:
    pass

# Building Footprint API
try:
    from building_footprint import _point_in_polygon, cache_building_footprint, lookup_building_footprint, lookup_official_building_collection

    @app.get("/api/building-footprint")
    async def get_building_footprint(lat: float, lon: float):
        return await lookup_building_footprint(lat, lon)

    @app.post("/api/building-footprint/cache")
    async def seed_building_footprint(payload: FootprintCacheRequest):
        return cache_building_footprint(
            payload.lat,
            payload.lon,
            payload.geometry,
            properties=payload.properties,
            source=payload.source,
        )
except ImportError:
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
# Deployment Trigger
