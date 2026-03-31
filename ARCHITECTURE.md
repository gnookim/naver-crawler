# Naver Crawler — 시스템 아키텍처 기획서

## 1. 시스템 개요

네이버 검색결과를 실제 브라우저로 크롤링하는 **자가 복구 분산 워커 시스템**.
여러 대의 PC(크롤링 워커)가 크롤링을 수행하고, 중앙 컨트롤타워 **CrawlStation**이 작업을 분배·감시·진단·복구한다.

### 용어 정의

```
CrawlStation  = 컨트롤타워 (웹 대시보드 + 에이전트 조직)
크롤링 워커    = 크롤링을 실행하는 각 PC (윈도우/맥)
Supabase      = 신경계 (모든 통신 채널)
```

### 에이전트 계층

```
Orchestrator  = 부서장 (CrawlStation 내부, 최종 판단)
Agent         = 팀장 (배분/감시/스케줄/진단 — 영역별 전문가)
Sub-Agent     = 크롤링 워커 (각 PC — 시키는 것만 한다)
```

### 자가 복구 대상 — 두 가지 핵심 문제

| # | 문제 | 증상 | 대응 주체 |
|---|------|------|-----------|
| 1 | **차단/우회** — 네이버가 IP, UA, 행동패턴 등으로 봇을 차단 | 403, 캡차, 빈 응답, 특정 워커만 실패 | Evasion Agent |
| 2 | **구조 변경** — 네이버 HTML/DOM 구조가 바뀌어 파싱 실패 | HTTP 200인데 파싱 결과 0건, 모든 워커 동일 실패 | Repair Agent |

---

## 2. 에이전트 조직도

```
┌──────────────────────────────────────────────────────────────┐
│               CrawlStation (컨트롤타워)                        │
│                                                              │
│   ┌────────────────────────────────────────────────────┐     │
│   │              Orchestrator (부서장)                    │     │
│   │          "누가, 뭘, 언제, 얼마나" 를 결정              │     │
│   └──────┬───────────┬───────────┬───────────┬────────┘     │
│          │           │           │           │              │
│    ┌─────▼────┐ ┌───▼────┐ ┌───▼────┐ ┌────▼──────────┐   │
│    │ Dispatch │ │Monitor │ │Schedule│ │   Doctor       │   │
│    │  Agent   │ │ Agent  │ │ Agent  │ │   Agent        │   │
│    │ (배분팀장)│ │(감시팀장)│ │(스케줄러)│ │ (진단→치료→배포)│   │
│    └─────┬────┘ └───┬────┘ └──┬────┘ └────┬──────────┘   │
│          │          │         │            │              │
│    ┌─────┴──────────┴─────────┴────────────┘              │
│    │  웹 대시보드 UI                                        │
│    │  - 워커 현황 (자동등록/수동등록)                          │
│    │  - 작업 큐 / 분배                                      │
│    │  - 진단 이력 / 순위 차트                                │
│    │  - 크롤링 워커 다운로드 + 설치 가이드                     │
│    └──────────────────────────────────────────────────────┘ │
└──────────────────────────┬───────────────────────────────────┘
                           │
                    Supabase (신경계)
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
   │  크롤링 워커 A │ │  크롤링 워커 B │ │  크롤링 워커 C │
   │  윈도우 PC    │ │  맥북         │ │  사무실 PC    │
   │  자동등록됨 ✅ │ │  자동등록됨 ✅ │ │  자동등록됨 ✅ │
   └──────────────┘ └──────────────┘ └──────────────┘
```

### 외부 연동 시스템

```
┌──────────┐     ┌──────────────────────────────────────┐
│ desk-web │────▶│            Supabase                   │
│ (사전분석)│◀────│                                      │
└──────────┘     │  crawl_requests    (요청)             │
                 │  crawl_results     (결과)             │
┌──────────┐     │  workers           (워커 등록)         │
│ kin-web  │────▶│  rank_tracking     (순위 기록)         │
│ (지식인)  │◀────│  schedules         (스케줄)           │
└──────────┘     │  worker_config     (워커 설정)         │
                 │  selectors         (셀렉터 맵)         │
┌──────────┐     │  html_snapshots    (진단용 스냅샷)      │
│  Crawl   │────▶│  doctor_logs       (진단 기록)         │
│ Station  │◀────│                                      │
└──────────┘     └──────────────────────────────────────┘
```

---

## 3. 크롤링 워커 등록 체계

### 3-1. 자동등록 (기본)

크롤링 워커를 새 PC에 설치하고 처음 실행하면, **CrawlStation에 자동으로 등록**된다.

```
크롤링 워커 첫 실행
  │
  ├─ 1. 워커 ID 생성 (없으면 자동 생성)
  │     UUID 기반: "worker-a1b2c3d4"
  │     .env에 저장 → 이후 재사용
  │
  ├─ 2. 머신 정보 수집
  │     - OS (Windows 11 / macOS 15.x)
  │     - hostname ("서울사무실-PC")
  │     - Python 버전
  │     - Playwright 상태
  │     - 공인 IP (외부 API로 확인)
  │
  ├─ 3. Supabase workers 테이블에 UPSERT
  │     {
  │       id: "worker-a1b2c3d4",
  │       name: "서울사무실-PC",     ← hostname 기반, 나중에 변경 가능
  │       os: "Windows 11",
  │       ip_address: "211.x.x.x",
  │       status: "online",
  │       registered_at: now(),
  │       last_seen: now()
  │     }
  │
  ├─ 4. heartbeat 시작 (5초마다)
  │
  └─ 5. CrawlStation 대시보드에 즉시 표시 ✅
        "새 워커 등록됨: 서울사무실-PC (Windows 11)"
```

### 3-2. 수동등록

CrawlStation 대시보드에서 수동으로 워커를 등록할 수도 있다.

```
CrawlStation > 워커 관리 > 수동 등록

입력:
- 워커 이름: "마케팅팀 PC"
- 워커 ID: (자동생성 or 직접 지정)

출력:
- 워커 ID + Supabase 인증 정보가 포함된 .env 파일 생성
- 해당 .env를 워커 PC에 복사 → 실행하면 등록 완료

용도:
- 자동등록이 안 되는 네트워크 환경
- 특정 ID를 미리 지정하고 싶을 때
- 워커를 사전에 등록해두고 나중에 연결할 때
```

### 3-3. 워커 상태 관리

```
CrawlStation 대시보드 — 워커 현황

┌─────────────────────────────────────────────────────────────┐
│ 워커 이름          │ OS        │ IP          │ 상태    │ 마지막 │
├────────────────────┼───────────┼─────────────┼────────┼───────┤
│ 서울사무실-PC       │ Windows 11│ 211.x.x.x  │ 🟢 idle │ 3초전  │
│ 집-맥북            │ macOS 15  │ 175.x.x.x  │ 🔵 작업중│ 1초전  │
│ 마케팅팀-PC        │ Windows 11│ 121.x.x.x  │ 🔴 오프  │ 2시간  │
│ 테스트-맥미니       │ macOS 15  │ 192.x.x.x  │ 🟡 차단  │ 30분   │
└─────────────────────────────────────────────────────────────┘

상태 색상:
🟢 idle      — 대기 중 (작업 할당 가능)
🔵 crawling  — 크롤링 실행 중
🟡 blocked   — IP 차단 의심 (쿨다운 중)
🔴 offline   — heartbeat 없음 (10초 초과)
```

---

## 4. 크롤링 워커 설치 (크로스 플랫폼)

### 4-1. CrawlStation에서 다운로드

CrawlStation 대시보드의 **"워커 설치"** 페이지에서 제공:

```
CrawlStation > 워커 설치

┌─────────────────────────────────────────────────────┐
│                                                     │
│   크롤링 워커 설치하기                                 │
│                                                     │
│   ┌──────────────┐  ┌──────────────┐                │
│   │  🪟 Windows  │  │  🍎 macOS    │                │
│   │  설치 스크립트  │  │  설치 스크립트  │                │
│   │  다운로드 ↓   │  │  다운로드 ↓   │                │
│   └──────────────┘  └──────────────┘                │
│                                                     │
│   또는 터미널에서:                                     │
│   curl -sL https://crawlstation.app/install | bash  │
│                                                     │
│   ─────────────────────────────────────────         │
│   📋 설치 가이드 (아래 참조)                           │
│                                                     │
└─────────────────────────────────────────────────────┘
```

### 4-2. Windows 설치

```
설치 가이드 (Windows)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 사전 요구사항
   - Windows 10 이상
   - Python 3.10+ (python.org에서 설치)
     ※ 설치 시 "Add Python to PATH" 체크 필수

2. 설치 스크립트 실행
   PowerShell을 관리자 권한으로 열고:

   Invoke-WebRequest -Uri "https://crawlstation.app/install.ps1" -OutFile install.ps1
   .\install.ps1

   스크립트가 자동으로:
   ✅ Python 버전 확인
   ✅ pip install playwright supabase
   ✅ playwright install chromium
   ✅ naver-crawler 파일 다운로드
   ✅ .env 파일 생성 (Supabase 인증 + 워커 ID)

3. 실행

   cd C:\CrawlWorker
   python worker.py

   → CrawlStation에 자동 등록됨 ✅

4. 시작 프로그램 등록 (선택)
   - 부팅 시 자동 실행하려면:
   - 작업 스케줄러 → 새 작업 → python worker.py
```

### 4-3. macOS 설치

```
설치 가이드 (macOS)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 사전 요구사항
   - macOS 12 이상
   - Python 3.10+ (Homebrew: brew install python@3.12)

2. 설치 (터미널에서 한 줄)

   curl -sL https://crawlstation.app/install.sh | bash

   스크립트가 자동으로:
   ✅ Python 버전 확인
   ✅ pip install playwright supabase
   ✅ playwright install chromium
   ✅ naver-crawler 파일 다운로드 → ~/CrawlWorker/
   ✅ .env 파일 생성 (Supabase 인증 + 워커 ID)

3. 실행

   cd ~/CrawlWorker
   python3.12 worker.py

   → CrawlStation에 자동 등록됨 ✅

4. 백그라운드 실행 (선택)
   - launchd 또는 nohup으로 상시 실행:
   nohup python3.12 worker.py > worker.log 2>&1 &
```

### 4-4. 설치 스크립트가 하는 일

```
install.sh / install.ps1 공통 흐름:

1. Python 버전 체크 (3.10+ 필수)
   └─ 없으면 설치 안내 후 종료

2. pip 패키지 설치
   └─ playwright, supabase, (기타 의존성)

3. Playwright 브라우저 설치
   └─ playwright install chromium

4. 크롤링 워커 파일 다운로드
   ├─ worker.py
   ├─ handlers/
   │   ├─ __init__.py
   │   ├─ base.py
   │   ├─ kin.py
   │   ├─ blog.py
   │   └─ serp.py
   └─ .env.template

5. .env 파일 생성
   ├─ SUPABASE_URL=... (CrawlStation에서 제공)
   ├─ SUPABASE_KEY=... (CrawlStation에서 제공)
   └─ WORKER_ID=worker-{uuid}  (자동 생성)

6. 테스트 연결
   └─ Supabase 연결 확인 → "✅ 연결 성공"

7. 완료 메시지
   └─ "python worker.py 로 실행하세요"
```

---

## 5. 에이전트 상세

### 5-1. Orchestrator (부서장)

전체를 총괄하는 **최종 의사결정자**. 직접 크롤링은 안 하고, 판단만 한다.

```
역할:
- 외부 요청 수신 (desk-web, kin-web, 수동 등록)
- 에이전트들에게 지시
- 최종 의사결정 (워커 차단, 긴급 중단, 우선순위 변경)
- Doctor Agent 수정안 중 "코드 변경"은 사람 승인 결정

비유: "김대리, 이 키워드 3000개 오늘 밤까지 돌려"
```

### 5-2. Dispatch Agent (배분팀장)

```
역할:
- 요청 큐 관리 (우선순위 정렬)
- 워커별 작업 할당 (라운드로빈 / 부하 기반 / IP 분산)
- 배치 구성 (30개씩 묶기 + 휴식 시간 삽입)
- 실패 작업 재할당

판단 기준:
- 워커 A 큐 3개 → 여유 → 할당
- 워커 B 큐 15개 → 바쁨 → 스킵
- 이 키워드는 아까 A IP로 했음 → B에 할당
```

### 5-3. Monitor Agent (감시팀장)

```
역할:
- heartbeat 감시 (10초 이내 → 정상 / 초과 → 이상)
- 연속 실패 감지 → IP 차단 판단 (3연속 실패 → blocked)
- 워커 다운 감지 → Dispatch에게 재할당 요청
- 이상 징후 → Doctor Agent 호출
- 실패 패턴 분석 (특정 워커만? 전체? 특정 핸들러?)
- 새 워커 등록 알림 → 대시보드에 표시

판단 기준:
- 워커 A 30초 heartbeat 없음 → offline 처리
- 워커 B 3번 연속 실패 → blocked, 30분 정지
- 전체 실패율 20% 초과 → Doctor Agent에 진단 요청
```

### 5-4. Schedule Agent (스케줄러)

```
역할:
- cron 스케줄 관리 (매일 02시 순위 체크 등)
- 스케줄 시간 도달 → crawl_requests 자동 생성
- Dispatch에게 "새 배치 나왔다" 알림
- 실행 이력 기록 (last_run, next_run)
```

### 5-5. Doctor Agent (진단→치료→배포)

크롤링 실패를 **자동으로 진단하고 복구**하는 핵심 에이전트.
내부에 3개의 서브 모듈로 구성된다.

```
┌─────────────────────────────────────────────────┐
│                Doctor Agent                      │
│                                                 │
│   ┌──────────────┐                              │
│   │ Diagnostician │ ← 진단관: 문제 1인지 2인지     │
│   └──────┬───────┘                              │
│          │                                      │
│    문제 1 │  문제 2                               │
│     ┌────▼────┐  ┌────▼────┐                    │
│     │ Evasion │  │ Repair  │                    │
│     │  Agent  │  │  Agent  │                    │
│     │ (우회)   │  │ (수리)   │                    │
│     └────┬────┘  └────┬────┘                    │
│          └──────┬─────┘                         │
│          ┌──────▼──────┐                        │
│          │  Deployer   │ ← 배포관: 수정사항 적용   │
│          └─────────────┘                        │
└─────────────────────────────────────────────────┘
```

#### Diagnostician (진단관)

```
입력:
- 실패한 crawl_request 정보
- 에러 메시지
- 실패 시점 스크린샷 (자동 캡처)
- 실패 시점 HTML 스냅샷
- 최근 24시간 성공/실패 통계

처리:
1. 패턴 매칭 (빠른 진단)
   - 403/captcha → 즉시 "차단"
   - 파싱 0건 + 200 OK → 즉시 "구조 변경"

2. 교차 검증 (애매한 경우)
   - 다른 워커로 같은 키워드 테스트 크롤링
   - 성공 → 특정 워커 IP 차단
   - 실패 → 전체 문제

3. HTML 비교 분석 (구조 변경 의심 시)
   - 마지막 성공 HTML vs 현재 HTML diff
   - 셀렉터 매칭 테스트
   - 변경된 클래스명/구조 특정

출력:
{
  "diagnosis": "structure_change",   // 또는 "blocked"
  "confidence": 0.95,
  "evidence": {
    "selector_failures": [".api_subject_bx → 매칭 0건"],
    "html_diff_summary": "블로그 영역 래퍼 클래스 변경"
  },
  "affected_handlers": ["serp", "blog"],
  "recommended_action": "update_selectors"
}
```

#### Evasion Agent (우회 전문가) — 문제 1 담당

```
차단 유형별 자동 대응:

┌─────────────────┬──────────────────────────────────┐
│ 진단             │ 자동 대응                         │
├─────────────────┼──────────────────────────────────┤
│ 특정 IP 차단     │ → 해당 워커 30분 쿨다운            │
│                 │ → 작업을 다른 워커에 재할당          │
│                 │                                  │
│ UA 패턴 감지     │ → UA 풀 교체                      │
│                 │ → worker_config 업데이트           │
│                 │                                  │
│ 행동 패턴 감지   │ → 타이핑 속도/스크롤 파라미터 조정    │
│                 │ → 랜덤 범위 변경                    │
│                 │                                  │
│ 속도 제한        │ → 배치 간 휴식 시간 증가             │
│                 │ → 키워드 간 딜레이 증가              │
│                 │                                  │
│ 전면 차단        │ → 전체 워커 일시 중단               │
│                 │ → Orchestrator에게 보고            │
│                 │ → 수동 판단 대기                    │
└─────────────────┴──────────────────────────────────┘

구현: 대부분 worker_config 테이블의 설정값 변경으로 해결
워커가 매 배치 시작 시 최신 config를 읽어감
```

#### Repair Agent (수리 전문가) — 문제 2 담당

```
Step 1. 셀렉터 매핑 분석
  이전 HTML: <div class="api_subject_bx">
  현재 HTML: <div class="search_result_box">
  → ".api_subject_bx" → ".search_result_box" 매핑 추론

Step 2. 새 셀렉터로 파싱 테스트
  저장된 HTML에 새 셀렉터 적용
  → 결과가 이전과 동일 구조인지 검증
  → 일치율 90% 이상 → 수정안 확정

Step 3. 코드 패치 생성
  selectors 테이블 업데이트 (코드 수정 불필요)
  또는 핸들러 코드 패치 (큰 변경 시)
```

#### Deployer (배포관)

```
수정 유형별 배포 방식:

Config 변경 (우회 파라미터)
  → worker_config 테이블 UPDATE
  → 워커가 다음 배치 시 자동 반영 ✅
  → 승인 불필요 (자동)

Selector 변경 (구조 변경 대응)
  → selectors 테이블 UPDATE
  → 테스트 크롤링 1건 실행 → 성공 시 전체 반영
  → 신뢰도 90% 이상: 자동 / 미만: 승인 대기

코드 변경 (큰 구조 변경)
  → Git PR 자동 생성
  → 반드시 사람 승인 후 배포 ⚠️
```

---

## 6. 크롤링 워커 (Sub-Agent) 상세

### 워커가 하는 것

| 기능 | 설명 |
|------|------|
| 자동 등록 | 첫 실행 시 CrawlStation에 자동 등록 |
| 크롤링 실행 | 할당된 작업만 수행 |
| 상태 보고 | 5초마다 heartbeat 전송 |
| 결과 전송 | 크롤링 결과를 DB에 저장 |
| 오류 보고 | 실패 시 에러 메시지 + HTML 스냅샷 저장 |
| 설정 동기화 | 매 배치 시작 시 최신 config/selectors 로드 |

### 워커가 하지 않는 것

| 금지 | 이유 |
|------|------|
| 작업 선택 | Dispatch Agent가 할당 |
| 스케줄링 | Schedule Agent가 제어 |
| 다른 워커와 통신 | Supabase를 통해서만 |
| 자체 판단 | 시키는 것만 수행 |
| 셀렉터 하드코딩 | selectors 테이블에서 동적 로드 |

### 워커 동작 루프

```
첫 실행:
  워커 ID 생성 (.env에 없으면 UUID 자동 생성)
  → 머신 정보 수집 (OS, hostname, IP)
  → workers 테이블에 UPSERT (자동 등록)
  → CrawlStation에 즉시 표시

메인 루프:
  config/selectors 로드 → heartbeat("online") → 대기
     └→ 내 ID로 assigned된 작업 있나? (5초마다)
         ├─ 없음 → heartbeat("idle") → 대기
         └─ 있음 → heartbeat("crawling", keyword)
                   → status = "running"
                   → 크롤링 실행 (동적 셀렉터 사용)
                   ├─ 성공 → 결과 저장 → status = "completed"
                   └─ 실패 → HTML 스냅샷 저장 → status = "failed"
                   → heartbeat("idle")
                   → 배치 끝? → config 재로드 (변경사항 반영)
                   → 대기

종료:
  heartbeat("offline")
```

---

## 7. 셀렉터 외부화 (자가 복구의 토대)

### 핵심 설계: 셀렉터를 코드에서 분리

```python
# ❌ 기존 (하드코딩)
results = page.query_selector_all(".api_subject_bx .title")

# ✅ 개선 (셀렉터 외부화)
selectors = load_selectors("blog_serp")  # Supabase에서 로드
results = page.query_selector_all(selectors["result_container"])
```

### 셀렉터 JSON 구조

```json
{
  "blog_serp": {
    "version": "2026-03-31",
    "result_container": ".search_result_box",
    "title": ".title_area > a",
    "url": ".title_area > a@href",
    "description": ".dsc_area",
    "blog_name": ".user_info .name"
  },
  "kin": {
    "version": "2026-03-31",
    "result_list": ".lst_total > li",
    "title": ".question_text",
    "answer_count": ".answer_area .count"
  }
}
```

이렇게 하면 **Repair Agent가 코드를 수정하지 않고 셀렉터 JSON만 업데이트**하면 복구된다.

---

## 8. 자가 복구 흐름 (종합)

```
크롤링 워커 C: blog_serp 크롤링 실패 (파싱 결과 0건)
  │
  ├─ HTML 스냅샷 + 스크린샷 자동 저장
  │
  ▼
Monitor Agent: "워커 C 실패 감지, 다른 워커도 동일 실패"
  │
  ▼ Doctor Agent 호출
  │
Diagnostician:
  ├─ HTTP 200 OK (차단 아님)
  ├─ 셀렉터 매칭 0건 (구조 바뀜)
  ├─ 모든 워커 동일 실패 (전체 문제)
  └─ 진단: "문제 2 — 구조 변경" (신뢰도 97%)
     │
     ▼
Repair Agent:
  ├─ 이전 성공 HTML vs 현재 HTML 비교
  ├─ ".api_subject_bx" → 매칭 0건
  ├─ 유사 구조 탐색 → ".search_result_box" 발견
  ├─ 새 셀렉터로 파싱 테스트 → 10건 추출 성공
  └─ 수정안 생성 (신뢰도 95%)
     │
     ▼
Deployer:
  ├─ 셀렉터 변경 (신뢰도 95% > 90%)
  ├─ selectors 테이블 자동 UPDATE
  ├─ 테스트 크롤링 1건 실행 → 성공 ✅
  └─ 전체 워커에 반영 완료
     "blog_serp 셀렉터 자동 복구됨" (소요: ~3분)
```

---

## 9. 외부 앱 연동 시스템

외부 앱(desk-web, kin-web, 기타)이 CrawlStation에 크롤링을 요청하고 결과를 받아가는 구조.

### 연동 구조

```
┌──────────┐    ┌──────────────┐    ┌──────────────┐
│ desk-web │───▶│ CrawlStation │───▶│ 크롤링 워커   │
│ kin-web  │◀───│   API        │◀───│ (각 PC)      │
│ 기타 앱   │    │              │    │              │
└──────────┘    └──────────────┘    └──────────────┘

연동 방법 2가지:
  A. CrawlStation API 사용 (권장)
  B. Supabase 직접 연결
```

### A. CrawlStation API (권장)

| 엔드포인트 | 메서드 | 용도 |
|------------|--------|------|
| `/api/crawl` | POST | 크롤링 요청 등록 |
| `/api/crawl?request_id=xxx` | GET | 특정 요청 상태+결과 조회 |
| `/api/crawl?keyword=xxx` | GET | 키워드로 결과 검색 |
| `/api/workers` | GET | 워커 상태 조회 |
| `/api/dispatch` | POST | 작업 자동 분배 (라운드로빈) |

### B. Supabase 직접 연결

```
외부 앱 환경변수:
  CRAWL_SUPABASE_URL=https://xxx.supabase.co
  CRAWL_SUPABASE_KEY=eyJ...

흐름:
  1. 외부 앱 → crawl_requests에 INSERT (status: "pending")
  2. 크롤링 워커가 자동 감지 → status: "running"
  3. 완료 → crawl_results에 결과 INSERT + status: "completed"
  4. 외부 앱 → crawl_results에서 SELECT (request_id로 조회)
```

### 연동 클라이언트 예시 (Next.js)

```typescript
// src/lib/crawl-client.ts
import { createClient } from "@supabase/supabase-js";

const crawlDb = createClient(
  process.env.CRAWL_SUPABASE_URL!,
  process.env.CRAWL_SUPABASE_KEY!
);

// 크롤링 요청
export async function requestCrawl(keywords: string[], type: string) {
  const rows = keywords.map((keyword) => ({
    keyword, type, status: "pending", priority: 0,
  }));
  const { data } = await crawlDb.from("crawl_requests").insert(rows).select("id, keyword");
  return data;
}

// 결과 대기 (polling)
export async function waitForResult(requestId: string, timeoutMs = 120000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const { data: req } = await crawlDb
      .from("crawl_requests").select("status").eq("id", requestId).single();
    if (req?.status === "completed" || req?.status === "failed") {
      const { data: results } = await crawlDb
        .from("crawl_results").select("*").eq("request_id", requestId);
      return { status: req.status, results };
    }
    await new Promise((r) => setTimeout(r, 3000));
  }
  return { status: "timeout", results: null };
}
```

### CrawlStation 설치/연동 가이드 위치

CrawlStation 대시보드 > **설치 / 연동** 페이지에서 확인 가능:
- **크롤링 워커 설치** 탭: 통합 인스톨러(installer.py) 가이드
- **외부 앱 연동 가이드** 탭: Next.js / Python / Supabase 직접 연결 예시 + API 레퍼런스

---

## 10. 크롤링 타입

### 현재 구현

| type | 설명 | 용도 |
|------|------|------|
| `kin_analysis` | 지식인 닥톡/일반 판별 | kin-web |
| `blog_crawl` | 블로그 본문 크롤링 | desk-web 사전분석 |
| `blog_serp` | 블로그 순위 수집 | 순위 모니터링 |

### 추가 예정

| type | 설명 | 용도 |
|------|------|------|
| `rank_check` | 통합검색 영역별 순위 | 키워드 모니터링 |
| `our_rank` | 우리 콘텐츠 순위 추적 | 성과 측정 |
| `cafe_crawl` | 카페 본문 크롤링 | 경쟁 분석 |
| `news_crawl` | 뉴스 본문 크롤링 | 트렌드 분석 |
| `shopping_crawl` | 쇼핑 상품 정보 | 쇼핑 모니터링 |

### rank_check 상세 (핵심 신규 타입)

```
요청:
{
  "type": "rank_check",
  "keyword": "당뇨에 좋은 음식",
  "options": {
    "our_urls": ["blog.naver.com/our_blog/123", "kin.naver.com/..."],
    "check_sections": true
  }
}

결과:
{
  "keyword": "당뇨에 좋은 음식",
  "check_date": "2026-03-31",
  "section_order": ["파워링크", "블로그 (인기글)", "지식인", "블로그 (최신)", "쇼핑", "뉴스"],
  "sections": {
    "블로그 (인기글)": {
      "position": 2,
      "items": [
        {"rank": 1, "title": "...", "url": "...", "is_ours": false},
        {"rank": 2, "title": "...", "url": "...", "is_ours": true}
      ]
    }
  },
  "our_positions": [
    {"url": "blog.naver.com/our_blog/123", "section": "블로그 (인기글)", "rank": 2, "section_position": 2}
  ]
}
```

---

## 11. DB 스키마

### workers (워커 등록 — 자동/수동)

```sql
CREATE TABLE workers (
  id TEXT PRIMARY KEY,                   -- "worker-a1b2c3d4" (UUID 기반)
  name TEXT,                             -- "서울사무실-PC" (hostname 기반, 변경 가능)
  os TEXT,                               -- "Windows 11" / "macOS 15.3"
  hostname TEXT,                         -- 머신 hostname
  python_version TEXT,                   -- "3.12.3"
  status TEXT DEFAULT 'offline',         -- online/idle/crawling/blocked/offline
  last_seen TIMESTAMPTZ,
  ip_address TEXT,
  current_task_id UUID,
  current_keyword TEXT,
  total_processed INTEGER DEFAULT 0,
  error_count INTEGER DEFAULT 0,
  blocked_until TIMESTAMPTZ,
  registered_at TIMESTAMPTZ DEFAULT now(),  -- 최초 등록 시각
  registered_by TEXT DEFAULT 'auto',        -- "auto" / "manual"
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### crawl_requests (작업 요청) — 확장

```sql
ALTER TABLE crawl_requests ADD COLUMN assigned_worker TEXT;
ALTER TABLE crawl_requests ADD COLUMN priority INTEGER DEFAULT 0;
ALTER TABLE crawl_requests ADD COLUMN schedule_id UUID;
```

### schedules (스케줄)

```sql
CREATE TABLE schedules (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  name TEXT,                             -- "일일 순위 모니터링"
  type TEXT,
  keywords TEXT[],
  options JSONB DEFAULT '{}',
  cron TEXT,                             -- "0 2 * * *"
  enabled BOOLEAN DEFAULT true,
  last_run TIMESTAMPTZ,
  next_run TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### rank_tracking (순위 기록)

```sql
CREATE TABLE rank_tracking (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  keyword TEXT NOT NULL,
  check_date DATE NOT NULL,
  section_order TEXT[],
  sections JSONB,
  our_positions JSONB,
  crawled_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(keyword, check_date)
);
```

### worker_config (워커 설정 — Evasion Agent가 관리)

```sql
CREATE TABLE worker_config (
  id TEXT PRIMARY KEY,                   -- "global" 또는 워커별 ID
  ua_pool TEXT[],                        -- UA 목록
  typing_speed_min INTEGER DEFAULT 60,   -- ms
  typing_speed_max INTEGER DEFAULT 180,
  scroll_min INTEGER DEFAULT 200,        -- px
  scroll_max INTEGER DEFAULT 600,
  batch_size INTEGER DEFAULT 30,
  batch_rest_seconds INTEGER DEFAULT 180,
  keyword_delay_min INTEGER DEFAULT 15,  -- 초
  keyword_delay_max INTEGER DEFAULT 30,
  updated_at TIMESTAMPTZ DEFAULT now(),
  updated_by TEXT                         -- "evasion_agent" / "manual"
);
```

### selectors (셀렉터 맵 — Repair Agent가 관리)

```sql
CREATE TABLE selectors (
  handler TEXT NOT NULL,                 -- "blog_serp", "kin", "blog"
  version TEXT NOT NULL,                 -- "2026-03-31"
  selectors JSONB NOT NULL,              -- 셀렉터 맵
  is_active BOOLEAN DEFAULT true,
  updated_by TEXT,                       -- "repair_agent" / "manual"
  created_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (handler, version)
);
```

### html_snapshots (진단용 스냅샷)

```sql
CREATE TABLE html_snapshots (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  request_id UUID,
  handler TEXT,
  keyword TEXT,
  html TEXT,                             -- 페이지 소스 (50KB 제한)
  screenshot_url TEXT,
  success BOOLEAN,
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### doctor_logs (진단 기록)

```sql
CREATE TABLE doctor_logs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  diagnosis_type TEXT,                   -- "blocked" / "structure_change"
  confidence FLOAT,
  evidence JSONB,
  action_taken TEXT,                     -- "config_update" / "selector_update" / "pr_created"
  result TEXT,                           -- "auto_fixed" / "pending_approval" / "failed"
  affected_handlers TEXT[],
  created_at TIMESTAMPTZ DEFAULT now()
);
```

---

## 12. 개발 로드맵

### Phase 0 ✅ 완료

```
단일 워커 + 기본 크롤링
- 지식인 분석 (kin_analysis)
- 블로그 크롤링 (blog_crawl)
- 블로그 순위 (blog_serp)
- Supabase 연동
- desk-web 사전분석 연동
- kin-web 웹앱
- 봇 탐지 우회 (사람 흉내)
```

### Phase 1 — CrawlStation + 워커 인프라

```
Supabase 인프라:
- workers 테이블 생성 (자동등록 지원)
- crawl_requests에 assigned_worker, priority 추가
- heartbeat 체계 정비 (workers 테이블로 통합)
- worker_config 테이블 (전역/워커별 설정)

크롤링 워커 (worker.py) 개선:
- 첫 실행 시 자동등록 (UUID 생성, 머신 정보 수집, UPSERT)
- assigned_worker = me 인 작업만 가져가기
- heartbeat 주기적 전송 (idle/crawling/offline)
- 매 배치 시작 시 config 재로드

크로스 플랫폼 설치 스크립트:
- install.sh (macOS/Linux)
- install.ps1 (Windows)
- Python 체크 → pip 설치 → Playwright 설치 → 파일 다운로드 → .env 생성

CrawlStation 웹 대시보드 (crawl-station/):
- 워커 목록 + 실시간 상태 (자동등록 워커 즉시 표시)
- 수동 워커 등록 메뉴
- 작업 큐 현황 (pending/running/completed/failed)
- 수동 크롤링 요청 등록 (키워드 + 타입 선택)
- 작업 분배 (워커 선택 or 자동 라운드로빈)
- 로그/에러 확인
- 크롤링 워커 다운로드 페이지 + 설치 가이드
```

### Phase 2 — 셀렉터 외부화 + 스냅샷 수집

```
자가 복구의 토대:
- selectors 테이블 + 기존 핸들러에서 셀렉터 분리
- html_snapshots 테이블 + 실패 시 자동 캡처
- 워커가 매 배치 시 최신 selectors 로드
- 스냅샷 뷰어 (CrawlStation 대시보드에 추가)
```

### Phase 3 — rank_check 핸들러

```
통합검색 순위 크롤링:
- rank_check 핸들러 구현 (셀렉터 외부화 방식)
- 영역 감지 (파워링크/블로그/지식인/쇼핑/뉴스)
- 영역 순서 + 우리 콘텐츠 순위 추적
- rank_tracking 테이블 저장
- CrawlStation 대시보드에 순위 차트 추가
```

### Phase 4 — Doctor Agent (자가 복구)

```
진단 시스템:
- Diagnostician: 문제 1 vs 2 자동 분류
- 실패 패턴 분석 (교차 검증, HTML diff)

Evasion Agent (문제 1 — 차단 대응):
- worker_config 자동 조정 (UA, 딜레이, 배치 크기)
- IP 차단 시 워커 쿨다운 + 재할당

Repair Agent (문제 2 — 구조 변경 대응):
- HTML 스냅샷 비교 분석
- 새 셀렉터 자동 추론 + 검증
- selectors 테이블 자동 업데이트

Deployer:
- Config 변경 → 자동 배포
- Selector 변경 → 테스트 후 자동 배포 (신뢰도 기반)
- 코드 변경 → PR 생성, 사람 승인 필요

doctor_logs 기록 + CrawlStation 대시보드 진단 이력
```

### Phase 5 — 스케줄링 + 알림

```
스케줄 엔진:
- cron 기반 정기 실행
- 키워드 대량 등록 → 자동 배치 분배

알림:
- 순위 급변 (3위 이상 변동)
- 워커 장애
- IP 차단 감지
- Doctor Agent 자동 복구 보고
- 새 워커 등록 알림
```

---

## 13. 프로젝트 구조

```
Claude_Agent/
├── naver-crawler/                ← 크롤링 워커 (Sub-Agent, 각 PC에 설치)
│   ├── worker.py                 ← 워커 메인 (자동등록 + 크롤링)
│   ├── handlers/
│   │   ├── __init__.py           ← 핸들러 등록
│   │   ├── base.py               ← 공통 (사람 흉내, 봇 우회)
│   │   ├── kin.py                ← 지식인 분석
│   │   ├── blog.py               ← 블로그 본문 크롤링
│   │   └── serp.py               ← 블로그 순위 수집
│   ├── installer.py              ← 통합 인스톨러 (Win/Mac 공통)
│   ├── install.sh                ← macOS/Linux 설치 스크립트 (레거시)
│   ├── install.ps1               ← Windows 설치 스크립트 (레거시)
│   ├── .env                      ← Supabase 인증 + WORKER_ID
│   ├── ARCHITECTURE.md           ← 이 문서
│   └── SYSTEM_DIAGRAM.md         ← 시스템 도식화
│
├── crawl-station/                ← CrawlStation (컨트롤타워)
│   ├── app/
│   │   ├── dashboard/            ← 메인 대시보드 (워커 현황, 작업 큐)
│   │   ├── workers/              ← 워커 관리 (자동/수동 등록, 상태)
│   │   ├── install/              ← 크롤링 워커 다운로드 + 가이드
│   │   └── api/
│   │       ├── crawl/            ← 연동 API (요청/결과 조회)
│   │       ├── workers/          ← 워커 상태 API
│   │       ├── dispatch/         ← Dispatch Agent (작업 분배)
│   │       ├── monitor/          ← Monitor Agent
│   │       ├── schedule/         ← Schedule Agent
│   │       └── doctor/           ← Doctor Agent (진단/복구)
│   └── lib/
│       ├── orchestrator.ts       ← 중앙 판단 로직
│       ├── dispatch.ts           ← 분배 알고리즘
│       ├── monitor.ts            ← 감시 로직
│       ├── schedule.ts           ← 스케줄 엔진
│       └── doctor/
│           ├── diagnostician.ts  ← 진단관
│           ├── evasion.ts        ← 우회 전문가
│           ├── repair.ts         ← 수리 전문가
│           └── deployer.ts       ← 배포관
│
├── kin-web/                      ← 지식인 분석 웹앱
├── desk-web/                     ← 콘텐츠 분석 웹앱
└── local-crawler/                ← 로컬 크롤러
```

---

## 14. 계층별 역할 요약

| 계층 | 역할 | 판단 | 실행 | 위치 |
|------|------|------|------|------|
| CrawlStation | 컨트롤타워 | 웹 UI + 에이전트 | — | crawl-station/ |
| Orchestrator | 총괄 | **모든 최종 결정** | ✗ | crawl-station/ |
| Dispatch Agent | 작업 분배 | 영역 내 판단 | 간접 (DB 조작) | crawl-station/ |
| Monitor Agent | 상태 감시 | 이상 감지/보고 | 간접 (DB 조작) | crawl-station/ |
| Schedule Agent | 스케줄 실행 | 시간 판단 | 간접 (DB 조작) | crawl-station/ |
| Doctor Agent | 진단/복구 | 문제 분류/수정안 | 간접 (DB/PR) | crawl-station/ |
| 크롤링 워커 | 크롤링 | **✗ 판단 없음** | 직접 실행 | naver-crawler/ |

---

## 15. 실행 방법

### CrawlStation 실행

```bash
cd ~/Desktop/Claude_Agent/crawl-station
npm run dev
# → http://localhost:3000 에서 대시보드 접속
```

### 크롤링 워커 설치 (새 PC)

```bash
# macOS
curl -sL https://crawlstation.app/install.sh | bash
cd ~/CrawlWorker && python3.12 worker.py

# Windows (PowerShell 관리자)
Invoke-WebRequest -Uri "https://crawlstation.app/install.ps1" -OutFile install.ps1
.\install.ps1
cd C:\CrawlWorker
python worker.py

# → CrawlStation 대시보드에 자동 등록됨 ✅
```

### 워커 ID 변경 (수동)

```bash
# .env 파일에서
WORKER_ID=worker-custom-name

# 또는 환경변수로
WORKER_ID=worker-custom-name python3.12 worker.py
```
