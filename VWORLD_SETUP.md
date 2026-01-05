# 🇰🇷 VWorld (국토교통부 공간정보 오픈플랫폼) 설정

## 1. API 키 발급 (무료)

1. **VWorld 개발자센터** 접속
   - https://www.vworld.kr/dev/main.do

2. **회원가입 및 로그인**

3. **인증키 발급**
   - 마이페이지 → 인증키 관리 → 인증키 발급
   - 시스템명: `UAV-Dashboard`
   - 활용용도: `연구/학술` 또는 `기타`
   - URL: `http://localhost:5173` (개발용)

4. **API 키 복사**
   - 약 30초 소요

---

## 2. 사용 가능한 무료 데이터

| 데이터 | 설명 | 드론 활용 |
|--------|------|-----------|
| **2D 지도** | 일반/위성/하이브리드 | 기본 위치 확인 |
| **3D 지도** | 전국 3D 건물 및 지형 | **건물 높이, 지형 장애물 확인** |
| **용도지역도** | 주거/상업/공업/녹지 | **H/W 계산, 인구 밀집도 추정** |
| **연속지적도** | 지번, 경계 | 비행 구역 상세 확인 |
| **비행금지구역** | (일부 제공) | Gate0 하드스탑 참고 |

## 3. 프론트엔드 변경 (Leaflet + VWorld)

Google Maps 대신 오픈소스인 Leaflet을 사용하여 VWorld 타일을 불러옵니다.

### 설치
```bash
npm install leaflet react-leaflet
```

### 코드 예시
```javascript
<MapContainer center={[37.5665, 126.9780]} zoom={15}>
  <TileLayer
    url="https://api.vworld.kr/req/wmts/1.0.0/{apiKey}/Base/{z}/{y}/{x}.png"
    attribution="&copy; VWorld"
  />
</MapContainer>
```
