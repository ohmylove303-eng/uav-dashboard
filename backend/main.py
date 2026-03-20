"""
🚁 UAV 도시 운용판정 시스템 - FastAPI 백엔드
4중 게이트 시스템 + 실시간 기상 연동 + 기종별 맞춤 판정
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime
from enum import Enum
import httpx
import asyncio
import os

app = FastAPI(
    title="UAV Urban Ops API",
    description="승리 도시지역 드론 운용 판단 프로그램",
    version="2.1.1"
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

class EvaluationResponse(BaseModel):
    timestamp: str
    location: Dict[str, float]
    weather: Dict
    urban_factors: Dict
    gates: List[GateResult]
    final_judgment: JudgmentLevel
    ews: float
    drone_spec: Dict

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

                return {
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
                    "sunset": sunset
                }
    except Exception as e:
        print(f"Weather Fetch Error: {e}")
        pass
        
    return {
        "wind_speed": 5.0, "gust_speed": 8.0, "wind_direction": 0,
        "visibility": 10.0, "precipitation_prob": 10, "temperature": 20, 
        "dew_point": 15, "humidity": 50, "cloud_cover": 20,
        "weather_code": 0, "sunrise": "06:00", "sunset": "18:00"
    }

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
    return {"status": "ok", "version": "2.1.1"}

@app.get("/api/kp")
async def get_kp_index_api():
    kp = await fetch_kp_index()
    return {"kp_index": kp}

@app.get("/api/weather")
async def get_weather_api(lat: float = 37.5665, lon: float = 126.9780):
    w = await fetch_weather(lat, lon)
    kp = await fetch_kp_index()
    w["kp_index"] = kp
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
    
    # 2. Drone Specs
    spec = DRONE_SPECS[request.drone_model]
    
    # 3. Urban Factors
    align_factor = {"일치": 1.3, "직각": 0.9, "불명": 1.1}.get(request.wind_alignment, 1.0)
    fcanyon = calculate_fcanyon(request.building_height, request.street_width)
    ews = calculate_ews(weather["wind_speed"], fcanyon, align_factor)
    
    urban_factors = {
        "H": request.building_height, "W": request.street_width,
        "Fcanyon": round(fcanyon, 2), "alignment_factor": align_factor
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
        final_judgment=final, ews=round(ews, 2), drone_spec=spec
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
    from building_footprint import lookup_building_footprint

    @app.get("/api/building-footprint")
    async def get_building_footprint(lat: float, lon: float):
        return await lookup_building_footprint(lat, lon)
except ImportError:
    pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
# Deployment Trigger
