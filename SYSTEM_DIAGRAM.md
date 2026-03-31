# Naver Crawler System — 시스템 도식화

## 1. 전체 에이전트 조직도

```mermaid
graph TB
    subgraph "CrawlStation (컨트롤타워)"
        ORC["🧠 Orchestrator<br/>부서장 — 최종 판단"]

        DA["📦 Dispatch Agent<br/>배분팀장"]
        MA["👁️ Monitor Agent<br/>감시팀장"]
        SA["⏰ Schedule Agent<br/>스케줄러"]

        subgraph "Doctor Agent (자가 복구)"
            DIAG["🔍 Diagnostician<br/>진단관"]
            EVA["🛡️ Evasion Agent<br/>우회 전문가"]
            REP["🔧 Repair Agent<br/>수리 전문가"]
            DEP["🚀 Deployer<br/>배포관"]
        end

        UI["🖥️ 웹 대시보드<br/>워커 현황 · 작업 큐 · 진단 이력<br/>워커 다운로드 · 설치 가이드"]
    end

    subgraph "Supabase (신경계)"
        CR[(crawl_requests)]
        CRES[(crawl_results)]
        WK[(workers)]
        WC[(worker_config)]
        SEL[(selectors)]
        HS[(html_snapshots)]
        DL[(doctor_logs)]
        RT[(rank_tracking)]
        SC[(schedules)]
    end

    subgraph "크롤링 워커 풀 (Sub-Agent)"
        W1["🪟 크롤링 워커 A<br/>Windows PC<br/>자동등록됨"]
        W2["🍎 크롤링 워커 B<br/>맥북<br/>자동등록됨"]
        W3["🪟 크롤링 워커 C<br/>사무실 PC<br/>수동등록됨"]
    end

    subgraph "외부 시스템 (요청자)"
        DW[desk-web<br/>사전분석]
        KW[kin-web<br/>지식인]
    end

    ORC --> DA
    ORC --> MA
    ORC --> SA
    MA -->|이상 감지| DIAG
    DIAG -->|문제 1: 차단| EVA
    DIAG -->|문제 2: 구조변경| REP
    EVA --> DEP
    REP --> DEP

    DA -->|작업 할당| CR
    SA -->|스케줄 실행| CR
    DEP -->|config 업데이트| WC
    DEP -->|selector 업데이트| SEL
    DIAG -->|진단 기록| DL
    DIAG -->|스냅샷 조회| HS

    DW -->|요청 등록| CR
    KW -->|요청 등록| CR

    CR -->|할당된 작업| W1 & W2 & W3
    W1 & W2 & W3 -->|결과 저장| CRES
    W1 & W2 & W3 -->|heartbeat + 자동등록| WK
    W1 & W2 & W3 -->|실패 시 스냅샷| HS
    W1 & W2 & W3 -.->|config 로드| WC
    W1 & W2 & W3 -.->|selector 로드| SEL

    CRES -->|결과 조회| DW & KW
    CRES -->|순위 저장| RT

    MA -.->|상태 감시| WK
```

## 2. 크롤링 워커 등록 흐름 (자동 + 수동)

```mermaid
flowchart TD
    subgraph "자동등록 (기본)"
        START["새 PC에 크롤링 워커 설치"]
        START --> RUN["python worker.py 첫 실행"]
        RUN --> CHECK_ID{".env에<br/>WORKER_ID 있나?"}
        CHECK_ID -->|없음| GEN["UUID 자동 생성<br/>worker-a1b2c3d4"]
        CHECK_ID -->|있음| USE["기존 ID 사용"]
        GEN --> SAVE_ENV[".env에 WORKER_ID 저장"]
        SAVE_ENV --> COLLECT
        USE --> COLLECT["머신 정보 수집<br/>OS · hostname · IP · Python버전"]
        COLLECT --> UPSERT["workers 테이블에 UPSERT<br/>registered_by = 'auto'"]
        UPSERT --> HB["heartbeat 시작 (5초마다)"]
        HB --> DASH["✅ CrawlStation 대시보드에<br/>즉시 표시"]
    end

    subgraph "수동등록"
        MANUAL["CrawlStation > 워커 관리<br/>> 수동 등록"]
        MANUAL --> INPUT["이름, ID 입력"]
        INPUT --> INSERT["workers 테이블에 INSERT<br/>registered_by = 'manual'<br/>status = 'pending'"]
        INSERT --> GEN_ENV[".env 파일 생성<br/>(WORKER_ID + Supabase 인증)"]
        GEN_ENV --> COPY["워커 PC에 .env 복사"]
        COPY --> RUN2["python worker.py 실행"]
        RUN2 --> HB2["heartbeat 시작"]
        HB2 --> DASH2["✅ 상태: pending → online"]
    end

    style DASH fill:#c8e6c9
    style DASH2 fill:#c8e6c9
```

## 3. 크로스 플랫폼 설치 흐름

```mermaid
flowchart LR
    subgraph "CrawlStation 다운로드 페이지"
        DL_PAGE["crawl-station/install"]
        DL_PAGE --> WIN["🪟 Windows<br/>install.ps1 다운로드"]
        DL_PAGE --> MAC["🍎 macOS<br/>install.sh 다운로드"]
        DL_PAGE --> CURL["또는 터미널에서:<br/>curl -sL .../install.sh | bash"]
    end

    subgraph "설치 스크립트 실행"
        WIN --> SCRIPT
        MAC --> SCRIPT
        CURL --> SCRIPT
        SCRIPT["1. Python 3.10+ 확인<br/>2. pip install playwright supabase<br/>3. playwright install chromium<br/>4. 워커 파일 다운로드<br/>5. .env 생성 (자동 WORKER_ID)<br/>6. Supabase 연결 테스트"]
    end

    SCRIPT --> READY["✅ 설치 완료<br/>python worker.py 로 실행"]
    READY --> AUTO["CrawlStation에<br/>자동 등록"]

    style AUTO fill:#c8e6c9
```

## 4. 자가 복구 흐름 (Doctor Agent)

```mermaid
flowchart TD
    FAIL["❌ 크롤링 실패 발생<br/>(워커가 html_snapshots에 저장)"]

    FAIL --> MON["👁️ Monitor Agent<br/>실패 감지"]
    MON --> CROSS{교차 확인}
    CROSS -->|특정 워커만 실패| P1_QUICK["문제 1: IP 차단 (빠른 판단)"]
    CROSS -->|모든 워커 실패| DIAG["🔍 Diagnostician 호출"]

    DIAG --> CHECK{HTTP 상태 + 파싱 결과}
    CHECK -->|403 / 캡차 / 빈 응답| P1["문제 1: 차단/우회"]
    CHECK -->|200 OK + 파싱 0건| P2["문제 2: 구조 변경"]
    CHECK -->|애매함| VERIFY["교차 검증<br/>다른 워커로 테스트 크롤링"]
    VERIFY -->|다른 워커 성공| P1
    VERIFY -->|다른 워커도 실패| P2

    P1_QUICK --> EVA
    P1 --> EVA["🛡️ Evasion Agent"]
    P2 --> REP["🔧 Repair Agent"]

    EVA --> EVA_ACT{차단 유형}
    EVA_ACT -->|IP 차단| E1["워커 쿨다운 30분<br/>+ 작업 재할당"]
    EVA_ACT -->|UA 패턴| E2["UA 풀 교체<br/>worker_config 업데이트"]
    EVA_ACT -->|행동 패턴| E3["딜레이/스크롤 파라미터 조정"]
    EVA_ACT -->|속도 제한| E4["배치 크기↓ 휴식↑"]
    EVA_ACT -->|전면 차단| E5["전체 중단<br/>→ 사람 판단 대기 ⚠️"]

    REP --> REP1["이전 HTML vs 현재 HTML 비교"]
    REP1 --> REP2["깨진 셀렉터 탐지<br/>새 셀렉터 추론"]
    REP2 --> REP3["새 셀렉터로 파싱 테스트"]
    REP3 --> REP_OK{일치율}
    REP_OK -->|90% 이상| AUTO["자동 배포 ✅"]
    REP_OK -->|90% 미만| MANUAL_A["사람 승인 대기 ⚠️"]

    E1 & E2 & E3 & E4 --> DEP["🚀 Deployer"]
    AUTO --> DEP
    MANUAL_A -->|승인| DEP

    DEP --> DEPLOY{배포 유형}
    DEPLOY -->|Config 변경| D1["worker_config UPDATE<br/>→ 워커 자동 반영"]
    DEPLOY -->|Selector 변경| D2["selectors UPDATE<br/>→ 테스트 크롤링 → 전체 반영"]
    DEPLOY -->|코드 변경| D3["Git PR 생성<br/>→ 사람 승인 필요"]

    D1 & D2 --> VERIFY_FINAL["✅ 테스트 크롤링 1건<br/>성공 확인"]
    VERIFY_FINAL --> DONE["🎉 자동 복구 완료<br/>doctor_logs에 기록"]
```

## 5. 크롤링 워커 동작 흐름

```mermaid
sequenceDiagram
    participant CS as CrawlStation
    participant DB as Supabase
    participant W as 크롤링 워커
    participant N as 네이버

    Note over W: === 첫 실행: 자동등록 ===
    W->>W: WORKER_ID 확인 (없으면 UUID 생성)
    W->>W: 머신 정보 수집 (OS, hostname, IP)
    W->>DB: UPSERT workers (자동등록)
    DB-->>CS: 새 워커 등록 알림
    Note over CS: "새 워커: 서울사무실-PC (Windows 11)"

    Note over W: === config/selectors 로드 ===
    W->>DB: SELECT * FROM worker_config
    W->>DB: SELECT * FROM selectors WHERE is_active=true
    DB-->>W: config + selectors

    W->>DB: heartbeat(idle)

    Note over CS: Dispatch Agent: 작업 분배
    CS->>DB: INSERT crawl_requests<br/>(assigned_worker=worker-a1b2c3d4)

    loop 5초마다
        W->>DB: SELECT WHERE assigned_worker=me AND status=assigned
    end

    DB-->>W: 새 작업 발견!
    W->>DB: UPDATE status='running'
    W->>DB: heartbeat(crawling, 키워드)

    W->>N: 네이버 접속 (동적 셀렉터 사용)
    Note over W,N: 사람처럼 타이핑 → 검색 → 스크롤<br/>(config 기반 파라미터)
    N-->>W: 검색 결과 HTML

    alt 파싱 성공
        W->>DB: INSERT crawl_results
        W->>DB: UPDATE status='completed'
    else 파싱 실패
        W->>DB: INSERT html_snapshots (HTML + 스크린샷)
        W->>DB: UPDATE status='failed'
    end

    W->>DB: heartbeat(idle)

    Note over W: 배치 끝 → config/selectors 재로드
    W->>DB: SELECT * FROM worker_config
    W->>DB: SELECT * FROM selectors

    Note over CS: Monitor Agent: 실패 감시
    CS->>DB: 실패율 확인
    alt 이상 감지
        CS->>CS: Doctor Agent 호출 → 자동 복구
    end
```

## 6. 작업 분배 흐름 (Dispatch Agent)

```mermaid
flowchart TD
    A["새 요청 도착<br/>(desk-web / kin-web / 수동 / 스케줄)"] --> B{활성 워커<br/>있나?}
    B -->|없음| C["에러: 활성 워커 없음<br/>→ CrawlStation 알림"]
    B -->|있음| D["분배 전략 선택"]

    D --> E{전략}
    E -->|라운드로빈| F["순서대로 할당"]
    E -->|부하 기반| G["큐 적은 워커 우선"]
    E -->|IP 분산| H["같은 키워드 → 다른 IP"]

    F & G & H --> I["assigned_worker 설정<br/>status = 'assigned'"]

    I --> J{워커 실행 결과}
    J -->|완료| K["결과 저장 ✅"]
    J -->|실패| L{연속 실패?}
    L -->|3회 미만| M["재시도<br/>(같은 워커)"]
    L -->|3회 이상| N["IP 차단 의심"]
    N --> O["워커 blocked 처리<br/>+ 쿨다운"]
    O --> P["다른 워커에 재할당"]
    N --> Q["Doctor Agent 호출"]
```

## 7. 셀렉터 외부화 구조

```mermaid
flowchart LR
    subgraph "기존 (하드코딩) ❌"
        CODE1["handlers/serp.py<br/>.api_subject_bx"] --> PARSE1["파싱"]
    end

    subgraph "개선 (외부화) ✅"
        SEL_DB[(selectors 테이블<br/>handler: blog_serp<br/>version: 2026-03-31)] --> LOAD["워커: load_selectors()"]
        LOAD --> CODE2["handlers/serp.py<br/>selectors 'result_container'"]
        CODE2 --> PARSE2["파싱"]

        REP["🔧 Repair Agent"] -->|구조 변경 감지 시<br/>자동 업데이트| SEL_DB
    end

    style SEL_DB fill:#e1f5fe
    style REP fill:#fff3e0
```

## 8. 순위 모니터링 구조 (rank_check)

```mermaid
graph LR
    subgraph "네이버 통합검색 페이지"
        S1["1위 영역: 파워링크"]
        S2["2위 영역: 블로그 (인기글)"]
        S3["3위 영역: 지식인"]
        S4["4위 영역: 쇼핑"]
        S5["5위 영역: 블로그 (최신)"]
        S6["6위 영역: 뉴스"]
    end

    subgraph "수집 데이터"
        D1["영역 순서<br/>[파워링크→블로그→지식인→...]"]
        D2["영역별 순위<br/>블로그 1위, 2위, 3위..."]
        D3["우리 콘텐츠 위치<br/>블로그 3위 ⭐"]
    end

    S1 & S2 & S3 & S4 & S5 & S6 --> D1
    S2 --> D2
    D2 --> D3
```

## 9. 일일 모니터링 타임라인

```mermaid
gantt
    title 일일 3,000 키워드 모니터링 (워커 5대)
    dateFormat HH:mm
    axisFormat %H:%M

    section 크롤링 워커 A (600개)
    배치 1 (30개)      :a1, 02:00, 15m
    휴식               :a2, after a1, 3m
    배치 2 (30개)      :a3, after a2, 15m
    휴식               :a4, after a3, 3m
    ...계속 (20배치)    :a5, after a4, 180m

    section 크롤링 워커 B (600개)
    배치 1             :b1, 02:00, 15m
    휴식               :b2, after b1, 3m
    배치 2             :b3, after b2, 15m
    ...계속            :b5, after b3, 183m

    section 크롤링 워커 C~E
    동시 실행           :c1, 02:00, 240m
```

## 10. 프로젝트 관계도

```mermaid
graph TB
    subgraph "크롤링 워커 (Sub-Agent, 각 PC에 설치)"
        NC["naver-crawler/<br/>크롤링 워커 (Python)"]
        NC --> BASE[handlers/base.py<br/>사람 흉내 + 봇 우회]
        NC --> KIN[handlers/kin.py<br/>지식인 핸들러]
        NC --> BLOG[handlers/blog.py<br/>블로그 핸들러]
        NC --> SERP[handlers/serp.py<br/>순위 핸들러]
        NC --> INST[install.sh / install.ps1<br/>크로스 플랫폼 설치]
    end

    subgraph "CrawlStation (컨트롤타워)"
        CS["crawl-station/<br/>대시보드 + 에이전트"]
        CS --> ORCH[lib/orchestrator.ts]
        CS --> DISP[lib/dispatch.ts]
        CS --> MONI[lib/monitor.ts]
        CS --> SCHED[lib/schedule.ts]
        CS --> DOC[lib/doctor/]
        CS --> DLPAGE[app/install/<br/>워커 다운로드 + 가이드]
    end

    subgraph "웹 레이어 (요청자)"
        KW2["kin-web/<br/>지식인 분석기"]
        DW2["desk-web/<br/>콘텐츠 분석"]
    end

    subgraph "DB 레이어"
        SB[(Supabase<br/>workers · crawl_requests<br/>crawl_results · worker_config<br/>selectors · html_snapshots<br/>doctor_logs · schedules<br/>rank_tracking)]
    end

    NC <-->|요청/결과/config/selectors<br/>자동등록/heartbeat| SB
    CS <-->|관리/감시/진단| SB
    KW2 <-->|지식인 분석| SB
    DW2 <-->|블로그 크롤링| SB
```

## 11. 봇 탐지 우회 전략 (Config 기반)

```mermaid
mindmap
  root((봇 우회<br/>worker_config 기반))
    브라우저
      navigator.webdriver = false
      window.chrome 위장
      랜덤 뷰포트
      UA 로테이션 ← ua_pool[]
    행동
      마우스 랜덤 이동
      클릭 좌표 흔들림
      타이핑 ← typing_speed_min~max
      오타 5% → 백스페이스
      자동완성 구경
    스크롤
      불규칙 ← scroll_min~max
      가끔 위로 되돌림
      smooth 스크롤
    시간
      키워드간 ← keyword_delay_min~max
      배치 ← batch_size + batch_rest_seconds
      일일 한도
    네트워크
      세션 분리 (키워드마다)
      멀티 IP (워커 분산)
      시간대 분산
    자동 조정
      Evasion Agent가 차단 감지 시
      worker_config 자동 업데이트
      워커가 다음 배치에 반영
```

## 12. CrawlStation 대시보드 화면 구성

```mermaid
flowchart TB
    subgraph "CrawlStation 메인 메뉴"
        DASH["📊 대시보드<br/>전체 현황 · 통계"]
        WORKERS["🖥️ 워커 관리<br/>목록 · 자동등록 · 수동등록"]
        QUEUE["📋 작업 큐<br/>pending · running · done · failed"]
        DIAG_LOG["🏥 진단 이력<br/>Doctor Agent 기록"]
        RANK["📈 순위 추적<br/>키워드별 차트"]
        SCHED_UI["⏰ 스케줄<br/>cron 관리"]
        INSTALL["⬇️ 워커 설치<br/>다운로드 · 가이드"]
    end

    DASH --> DASH_DETAIL["워커 상태 요약<br/>작업 처리량<br/>실패율 · 복구 현황"]
    WORKERS --> WORKER_DETAIL["🟢 idle · 🔵 crawling<br/>🟡 blocked · 🔴 offline<br/>자동등록됨 · 수동등록"]
    INSTALL --> INSTALL_DETAIL["🪟 Windows 설치<br/>🍎 macOS 설치<br/>단계별 가이드"]
```

## 13. 개발 로드맵 흐름

```mermaid
flowchart LR
    P0["Phase 0 ✅<br/>단일 워커<br/>기본 크롤링"]
    P1["Phase 1<br/>CrawlStation<br/>워커 인프라"]
    P2["Phase 2<br/>셀렉터 외부화<br/>스냅샷 수집"]
    P3["Phase 3<br/>rank_check<br/>핸들러"]
    P4["Phase 4<br/>Doctor Agent<br/>자가 복구"]
    P5["Phase 5<br/>스케줄링<br/>알림"]

    P0 --> P1 --> P2 --> P3 --> P4 --> P5

    P1 -.- N1["workers 테이블<br/>워커 자동/수동 등록<br/>설치 스크립트 Win/Mac<br/>CrawlStation 대시보드<br/>워커 다운로드 페이지"]
    P2 -.- N2["selectors 테이블<br/>html_snapshots<br/>핸들러 셀렉터 분리"]
    P3 -.- N3["통합검색 영역 감지<br/>순위 추적<br/>차트"]
    P4 -.- N4["Diagnostician<br/>Evasion Agent<br/>Repair Agent<br/>Deployer"]
    P5 -.- N5["cron 엔진<br/>알림 시스템"]
```
