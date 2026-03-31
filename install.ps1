# ============================================================
# CrawlStation — 크롤링 워커 설치 스크립트 (Windows PowerShell)
# 사용법: PowerShell 관리자 권한으로 실행
# ============================================================

$ErrorActionPreference = "Stop"
$INSTALL_DIR = "C:\CrawlWorker"

Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "  CrawlStation — 크롤링 워커 설치" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""

# ── 1. Python 체크 ──────────────────────────
Write-Host "🔍 Python 버전 확인..." -ForegroundColor Yellow

$PYTHON_CMD = $null
foreach ($cmd in @("python3.12", "python3.11", "python3.10", "python3", "python")) {
    try {
        $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        $major = & $cmd -c "import sys; print(sys.version_info.major)" 2>$null
        $minor = & $cmd -c "import sys; print(sys.version_info.minor)" 2>$null
        if ([int]$major -ge 3 -and [int]$minor -ge 10) {
            $PYTHON_CMD = $cmd
            Write-Host "  ✅ $cmd ($ver)" -ForegroundColor Green
            break
        }
    } catch {
        continue
    }
}

if (-not $PYTHON_CMD) {
    Write-Host "  ❌ Python 3.10 이상이 필요합니다." -ForegroundColor Red
    Write-Host ""
    Write-Host "  https://www.python.org/downloads/ 에서 설치해주세요."
    Write-Host "  설치 시 'Add Python to PATH' 체크 필수!"
    exit 1
}

# ── 2. 설치 디렉토리 생성 ─────────────────────
Write-Host ""
Write-Host "📁 설치 디렉토리: $INSTALL_DIR" -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path "$INSTALL_DIR\handlers" | Out-Null

# ── 3. pip 패키지 설치 ────────────────────────
Write-Host ""
Write-Host "📦 패키지 설치 중..." -ForegroundColor Yellow

try {
    & $PYTHON_CMD -m pip install --quiet playwright supabase 2>$null
    Write-Host "  ✅ playwright, supabase 설치 완료" -ForegroundColor Green
} catch {
    Write-Host "  pip 설치 실패. venv로 시도합니다..." -ForegroundColor Yellow
    & $PYTHON_CMD -m venv "$INSTALL_DIR\.venv"
    & "$INSTALL_DIR\.venv\Scripts\Activate.ps1"
    pip install --quiet playwright supabase
    $PYTHON_CMD = "$INSTALL_DIR\.venv\Scripts\python.exe"
    Write-Host "  ✅ venv 환경에 설치 완료" -ForegroundColor Green
}

# ── 4. Playwright 브라우저 설치 ─────────────────
Write-Host ""
Write-Host "🌐 Chromium 브라우저 설치 중..." -ForegroundColor Yellow
& $PYTHON_CMD -m playwright install chromium
Write-Host "  ✅ Chromium 설치 완료" -ForegroundColor Green

# ── 5. 워커 파일 복사 ──────────────────────────
Write-Host ""
Write-Host "📄 워커 파일 설치 중..." -ForegroundColor Yellow

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
if (Test-Path "$SCRIPT_DIR\worker.py") {
    Copy-Item "$SCRIPT_DIR\worker.py" "$INSTALL_DIR\" -Force
    Copy-Item "$SCRIPT_DIR\handlers\*.py" "$INSTALL_DIR\handlers\" -Force -ErrorAction SilentlyContinue
    Write-Host "  ✅ 로컬 파일에서 복사 완료" -ForegroundColor Green
} else {
    Write-Host "  ⚠️ 소스 파일을 찾을 수 없습니다." -ForegroundColor Yellow
    Write-Host "  worker.py와 handlers/ 디렉토리를 $INSTALL_DIR에 수동으로 복사해주세요."
}

# ── 6. .env 파일 생성 ─────────────────────────
Write-Host ""
if (-not (Test-Path "$INSTALL_DIR\.env")) {
    $WORKER_UUID = "worker-" + ([guid]::NewGuid().ToString().Substring(0, 8))
    Write-Host "🔑 .env 파일 생성 중..." -ForegroundColor Yellow

    @"
# CrawlStation 크롤링 워커 설정
# Supabase 연결 정보 (CrawlStation에서 확인)
SUPABASE_URL=
SUPABASE_KEY=

# 워커 ID (자동 생성됨, 변경 가능)
WORKER_ID=$WORKER_UUID
"@ | Set-Content "$INSTALL_DIR\.env" -Encoding UTF8

    Write-Host "  ✅ .env 생성 완료 (WORKER_ID: $WORKER_UUID)" -ForegroundColor Green
    Write-Host ""
    Write-Host "  ⚠️  중요: .env 파일에 SUPABASE_URL과 SUPABASE_KEY를 입력해주세요." -ForegroundColor Yellow
    Write-Host "  CrawlStation 대시보드 > 워커 설치 페이지에서 확인할 수 있습니다."
} else {
    Write-Host "  ℹ️  .env 파일이 이미 존재합니다. 건너뜁니다." -ForegroundColor Cyan
}

# ── 7. 완료 ────────────────────────────────────
Write-Host ""
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "  ✅ 설치 완료!" -ForegroundColor Green
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host ""
Write-Host "  실행 방법:"
Write-Host "    cd $INSTALL_DIR" -ForegroundColor White
Write-Host "    $PYTHON_CMD worker.py" -ForegroundColor White
Write-Host ""
Write-Host "  → CrawlStation 대시보드에 자동 등록됩니다."
Write-Host ""
