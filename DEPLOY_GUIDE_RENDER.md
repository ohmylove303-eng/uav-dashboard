# 🚀 Render 배포 가이드 (UAV Dashboard)

Render(render.com)를 무료 호스팅 서비스로 사용하여, 365일 접속 가능한 **나만의 대시보드 URL**을 만드는 방법입니다.

## 1단계: GitHub 저장소 만들기 (필수)
Render는 GitHub에 있는 코드를 가져와서 배포합니다. 먼저 코드를 GitHub에 올려야 합니다.

1. [GitHub](https://github.com)에 로그인하고 **New Repositoy**를 클릭합니다.
2. 저장소 이름(예: `uav-dashboard`)을 입력하고 **Create repository**를 누릅니다.
3. 터미널(VS Code 하단)에서 다음 명령어로 코드를 업로드합니다.
   (⚠️ 이미 `git init`이 되어 있다면 `git remote add...`부터 하세요)

```bash
# 터미널에서 실행 (프로젝트 폴더: '드론 용')
git init
git add .
git commit -m "First commit"

# GitHub에서 복사한 주소로 변경하세요 (예: https://github.com/ohmylove303-eng/uav-dashboard.git)
git branch -M main
git remote add origin https://github.com/ohmylove303-eng/uav-dashboard.git
git push -u origin main
```

---

## 2단계: Render 웹 서비스 생성
1. [Render Dashboard](https://dashboard.render.com/)에 접속합니다.
2. **New +** 버튼을 누르고 **Web Service**를 선택합니다.
3. **Build and deploy from a Git repository**를 선택하고 [Next]를 누릅니다.
4. 방금 올린 `ohmylove303-eng/uav-dashboard` 저장소를 찾아 **Connect**를 누릅니다.

---

## 3단계: 배포 설정 (중요 🌟)
가장 중요한 단계입니다. 아래 설정을 정확히 입력해야 합니다.

| 항목 | 설정값 | 설명 |
| :--- | :--- | :--- |
| **Name** | `uav-dashboard` | 원하는 이름 |
| **Region** | `Singapore` | 한국과 가까워 속도가 빠름 |
| **Branch** | `main` | 기본값 |
| **Root Directory** | `backend` | **(필수)** Python 코드가 있는 폴더 지정 |
| **Runtime** | `Python 3` | |
| **Build Command** | `pip install -r requirements.txt` | 패키지 설치 명령어 |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port 10000` | 서버 실행 명령어 |
| **Plan** | `Free` | 무료 플랜 선택 |

### 환경 변수 (Environment Variables)
*만약 API 키가 필요하다면 `Advanced` -> `Environment Variables`에 추가하세요.* (현재 프로젝트는 무료 오픈 API만 사용하여 필수는 아닙니다.)

---

## 4단계: 배포 완료
1. 맨 아래 **Create Web Service** 버튼을 누릅니다.
2. 검은색 로그 화면이 나오며 배포가 시작됩니다. (약 3~5분 소요)
3. "Your service is live" 메시지가 뜨면 성공!
4. 좌측 상단에 있는 **`https://uav-dashboard-xxxx.onrender.com`** 주소를 클릭하여 접속합니다.

이제 이 주소만 있으면 스마트폰, 태블릿, 다른 PC 어디서든 접속할 수 있습니다! 🚁
