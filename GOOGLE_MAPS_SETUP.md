# 🗺️ Google Maps API 설정 가이드

## 1. Google Cloud Console 접속

https://console.cloud.google.com

## 2. 프로젝트 생성

1. 상단 프로젝트 선택 → "새 프로젝트"
2. 이름: `UAV-Dashboard`
3. 만들기 클릭

## 3. API 활성화

"API 및 서비스" → "라이브러리"에서 다음 3개 활성화:

1. **Maps JavaScript API** (지도 표시)
2. **Places API** (장소 검색)
3. **Elevation API** (고도 정보)

## 4. API 키 생성

1. "API 및 서비스" → "사용자 인증정보"
2. "사용자 인증정보 만들기" → "API 키"
3. 키 복사

## 5. API 키 제한 (권장)

1. 생성된 API 키 클릭
2. "애플리케이션 제한사항" → "HTTP 리퍼러"
3. 허용 리퍼러 추가:
   - `http://localhost:5173/*`
   - `http://localhost:3000/*`
   - `https://yourdomain.com/*`
4. "API 제한사항" → 위 3개 API만 선택
5. 저장

## 6. 결제 설정

⚠️ **필수**: 결제 계정 연결 필요

- 월 $200 무료 크레딧 제공
- 드론 판정용으로 충분 (월 10,000회 이상)

## 7. 환경변수 설정

```bash
# 드론 용/.env
VITE_GOOGLE_MAPS_API_KEY=AIzaSyXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

## 8. 요금 참고 (2024년 기준)

| API | 무료 한도 | 초과 시 |
|-----|----------|---------|
| Maps JavaScript | 월 28,500회 | $7/1000회 |
| Places | 월 10,000회 | $17/1000회 |
| Elevation | 월 40,000회 | $5/1000회 |

---

## 🇰🇷 네이버 지도 API (대안)

### 발급 방법

1. https://www.ncloud.com 접속
2. AI·NAVER API → Maps → "신청하기"
3. 앱 등록 → 도메인 설정
4. Client ID 복사

### 장점
- 한국 지도 데이터 정확도 ↑
- 건물 정보 풍부
- 무료 한도 넉넉

### 단점
- 글로벌 지원 ✕
- 3D 건물 지원 ✕

---

## 권장 조합

| 용도 | 권장 API |
|------|----------|
| 글로벌/3D 건물 | Google Maps |
| 한국 주소/POI | 네이버 지도 |
| 비용 절약 | 네이버 + OpenStreetMap |
