# 🚁 UAV 도시 운용판정 대시보드

드론 비행 가능 여부를 4중 게이트 시스템으로 판정하는 웹 대시보드입니다.

## 🚦 4중 게이트 시스템

| Gate | 이름 | 기준 |
|------|------|------|
| Gate0 | 하드스탑 | 비행금지구역, 인파밀집, 강수 |
| Gate1 | 위성품질 | GPS ≥ 8, GLONASS ≥ 4 |
| Gate2 | 시정 | > 3km: GO, 1-3km: RESTRICT |
| Gate3 | 풍속(EWS) | 도시보정 적용 |
| Gate4 | 돌풍 | 기종별 한계 |

## 📊 도시 보정

```
EWS = 풍속 × Fcanyon × α × GF

Fcanyon = 1 + 0.3 × (H/W)  # 도시 협곡 계수
α = 1.2                     # 도시 거칠기
GF = 1.3                    # 오차 버퍼
```

## 🖥️ 실행 방법

### 백엔드 (FastAPI)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

- API 문서: http://localhost:8000/docs
- 헬스체크: http://localhost:8000/health

### 프론트엔드 (React)

```bash
cd frontend
npm install
npm run dev
```

- 대시보드: http://localhost:5173

## 📡 데이터 소스

| API | 데이터 | URL |
|-----|--------|-----|
| NOAA SWPC | Kp 지수 | https://services.swpc.noaa.gov |
| Open-Meteo | 기상 예보 | https://open-meteo.com |
| AviationWeather | METAR | https://aviationweather.gov |

## ⚠️ 주의사항

- 실제 비행 전 **드론원스톱**(https://drone.onestop.go.kr)에서 비행금지구역 확인 필수
- 본 시스템은 **참고용**이며, 최종 판단은 조종자 책임

## 📂 프로젝트 구조

```
드론 용/
├── backend/
│   ├── main.py              # FastAPI 서버
│   └── requirements.txt     # Python 의존성
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # 메인 컴포넌트
│   │   └── App.css          # 스타일
│   ├── package.json
│   └── vite.config.js
└── README.md
```
