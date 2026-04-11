"""
🚁 UAV 도시 운용판정 시스템 - FastAPI 백엔드
4중 게이트 시스템 + 실시간 기상 연동 + 기종별 맞춤 판정
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
from enum import Enum
import httpx
import asyncio
import os
import math
import time

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

@app.get("/")
async def read_root():
    return FileResponse('static/index.html')

# ============================================
# 모델 정의
# ============================================

class JudgmentLevel(str, Enum):
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
    ews: float
    drone_spec: Dict
    source: Optional[str] = None
    profile_source: Optional[str] = None
    upper_air_profile: Optional[Dict] = None
    wind_profiler_profile: Optional[Dict] = None
    selected_layer: Optional[Dict] = None
    profile_layers: Optional[List[Dict]] = None


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
        return dict(cached)

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
                    "stale_cache": False
                }
                _cache_set(WEATHER_LAST_GOOD_CACHE, cache_key, result)
                return _cache_set(WEATHER_CACHE, cache_key, result)
    except Exception as e:
        print(f"Weather Fetch Error: {e}")
        stale = _cache_get_stale(WEATHER_LAST_GOOD_CACHE, cache_key, WEATHER_STALE_TTL_S)
        if stale:
            stale_result = dict(_mark_stale_payload(stale))
            stale_result["source"] = f'{stale_result.get("source", "open_meteo_surface")} + stale_cache'
            return stale_result

    stale = _cache_get_stale(WEATHER_LAST_GOOD_CACHE, cache_key, WEATHER_STALE_TTL_S)
    if stale:
        stale_result = dict(_mark_stale_payload(stale))
        stale_result["source"] = f'{stale_result.get("source", "open_meteo_surface")} + stale_cache'
        return stale_result

    fallback = {
        "wind_speed": 5.0, "gust_speed": 8.0, "wind_direction": 0,
        "visibility": 10.0, "precipitation_prob": 10, "temperature": 20, 
        "dew_point": 15, "humidity": 50, "cloud_cover": 20,
        "weather_code": 0, "sunrise": "06:00", "sunset": "18:00",
        "source": "surface_fallback",
        "stale_cache": False
    }
    return _cache_set(WEATHER_CACHE, cache_key, fallback)

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
    return {
        "pressure_hpa": lower["pressure_hpa"] + (upper["pressure_hpa"] - lower["pressure_hpa"]) * ratio,
        "height_m": mission_altitude,
        "temperature_c": lower["temperature_c"] + (upper["temperature_c"] - lower["temperature_c"]) * ratio,
        "dew_point_c": lower["dew_point_c"] + (upper["dew_point_c"] - lower["dew_point_c"]) * ratio,
        "wind_direction_deg": lower["wind_direction_deg"] + (upper["wind_direction_deg"] - lower["wind_direction_deg"]) * ratio,
        "wind_speed_mps": lower["wind_speed_mps"] + (upper["wind_speed_mps"] - lower["wind_speed_mps"]) * ratio
    }


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

@app.get("/api/weather")
async def get_weather_api(lat: float = 37.5665, lon: float = 126.9780):
    w = await fetch_weather(lat, lon)
    kp = await fetch_kp_index()
    w["kp_index"] = kp
    upper_air, wind_profiler = await asyncio.gather(
        fetch_kma_upper_air_profile(lat, lon),
        fetch_kma_wind_profiler_profile(lat, lon)
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
    return {"weather": w}

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
            "dew_point": 15, "cloud_cover": 20, "wind_direction": 0, "sunrise": "06:00", "sunset": "18:00"
        }
        kp = request.kp_index or 3.0
    else:
        weather = await fetch_weather(request.latitude, request.longitude)
        kp = await fetch_kp_index()
    
    weather["kp_index"] = kp
    upper_air, wind_profiler = await asyncio.gather(
        fetch_kma_upper_air_profile(request.latitude, request.longitude),
        fetch_kma_wind_profiler_profile(request.latitude, request.longitude)
    )
    selected_layer = None
    profile_source = "surface_only"
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
            profile_source = "kma_radiosonde"
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
            profile_source = "kma_radiosonde_wind_profiler" if upper_air else "kma_wind_profiler"
            if wind_profiler.get("stale_cache"):
                source_suffixes.append("stale_wind_profiler_cache")

    if source_suffixes:
        weather["source"] = f'{weather.get("source", "open_meteo_surface")} + {", ".join(source_suffixes)}'

    profile_layers = build_profile_layers(weather, upper_air, wind_profiler)
    
    # 2. Drone Specs
    spec = DRONE_SPECS[request.drone_model]
    
    # 3. Urban Factors
    align_factor = {"일치": 1.3, "직각": 0.9, "불명": 1.1}.get(request.wind_alignment, 1.0)
    fcanyon = calculate_fcanyon(request.building_height, request.street_width)
    ews = calculate_ews(weather["wind_speed"], fcanyon, align_factor)
    
    urban_factors = {
        "H": request.building_height, "W": request.street_width,
        "Fcanyon": round(fcanyon, 2), "alignment_factor": align_factor,
        "mission_altitude": request.mission_altitude
    }
    
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
    
    return EvaluationResponse(
        timestamp=datetime.now().isoformat(),
        location={"lat": request.latitude, "lon": request.longitude},
        weather=weather, urban_factors=urban_factors, gates=gates,
        final_judgment=final, ews=round(ews, 2), drone_spec=spec,
        source="backend",
        profile_source=profile_source,
        upper_air_profile={
            "station_id": upper_air["station_id"],
            "station_name": upper_air["station_name"],
            "observed_at_utc": upper_air["observed_at_utc"],
            "layer_count": len(upper_air["layers"]),
            "stale_cache": upper_air.get("stale_cache", False)
        } if upper_air else None,
        wind_profiler_profile={
            "station_id": wind_profiler["station_id"],
            "station_name": wind_profiler["station_name"],
            "observed_at_utc": wind_profiler["observed_at_utc"],
            "mode": wind_profiler["mode"],
            "layer_count": len(wind_profiler["layers"]),
            "stale_cache": wind_profiler.get("stale_cache", False)
        } if wind_profiler else None,
        selected_layer={
            "height_m": round(selected_layer["height_m"], 1),
            "pressure_hpa": round(selected_layer["pressure_hpa"], 1),
            "temperature_c": round(selected_layer["temperature_c"], 1),
            "dew_point_c": round(selected_layer["dew_point_c"], 1),
            "wind_direction_deg": round(selected_layer["wind_direction_deg"], 1),
            "wind_speed_mps": round(selected_layer["wind_speed_mps"], 2),
            "density": weather.get("upper_air_density")
        } if selected_layer else None,
        profile_layers=profile_layers
    )

# Building Height API
try:
    from building_height import predict_building_height
    @app.get("/api/building-height")
    def get_building_height(lat: float, lon: float):
        # FIX: Use wrapper function instead of object method
        return predict_building_height(lat, lon)
except ImportError:
    pass

# Building Footprint API
try:
    from building_footprint import lookup_building_footprint, cache_building_footprint

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
