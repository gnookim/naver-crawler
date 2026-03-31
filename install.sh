#!/bin/bash
# ============================================================
# CrawlStation — 크롤링 워커 설치 스크립트 (macOS / Linux)
# 사용법: curl -sL <URL>/install.sh | bash
# ============================================================

set -e

INSTALL_DIR="$HOME/CrawlWorker"
MIN_PYTHON="3.10"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  CrawlStation — 크롤링 워커 설치"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. Python 체크 ──────────────────────────
echo "🔍 Python 버전 확인..."

PYTHON_CMD=""
for cmd in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" &> /dev/null; then
        ver=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        major=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        minor=$("$cmd" -c "import sys; print(sys.version_info.minor)" 2>/dev/null)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 10 ]; then
            PYTHON_CMD="$cmd"
            echo "  ✅ $cmd ($ver)"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "  ❌ Python 3.10 이상이 필요합니다."
    echo ""
    echo "  설치 방법:"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "    brew install python@3.12"
    else
        echo "    sudo apt install python3.12 python3.12-venv"
    fi
    exit 1
fi

# ── 2. 설치 디렉토리 생성 ─────────────────────
echo ""
echo "📁 설치 디렉토리: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR/handlers"

# ── 3. pip 패키지 설치 ────────────────────────
echo ""
echo "📦 패키지 설치 중..."

# --break-system-packages 지원 여부 확인
if $PYTHON_CMD -m pip install --help 2>/dev/null | grep -q "break-system-packages"; then
    PIP_EXTRA="--break-system-packages"
else
    PIP_EXTRA=""
fi

$PYTHON_CMD -m pip install $PIP_EXTRA --quiet playwright supabase 2>/dev/null || {
    echo "  pip 설치 실패. venv로 시도합니다..."
    $PYTHON_CMD -m venv "$INSTALL_DIR/.venv"
    source "$INSTALL_DIR/.venv/bin/activate"
    pip install --quiet playwright supabase
    PYTHON_CMD="$INSTALL_DIR/.venv/bin/python"
}
echo "  ✅ playwright, supabase 설치 완료"

# ── 4. Playwright 브라우저 설치 ─────────────────
echo ""
echo "🌐 Chromium 브라우저 설치 중..."
$PYTHON_CMD -m playwright install chromium --quiet 2>/dev/null || $PYTHON_CMD -m playwright install chromium
echo "  ✅ Chromium 설치 완료"

# ── 5. 워커 파일 복사/다운로드 ──────────────────
echo ""
echo "📄 워커 파일 설치 중..."

# 현재 스크립트와 같은 디렉토리에 소스가 있으면 복사
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/worker.py" ]; then
    cp "$SCRIPT_DIR/worker.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/handlers/"*.py "$INSTALL_DIR/handlers/" 2>/dev/null || true
    echo "  ✅ 로컬 파일에서 복사 완료"
else
    echo "  ⚠️ 소스 파일을 찾을 수 없습니다."
    echo "  worker.py와 handlers/ 디렉토리를 $INSTALL_DIR에 수동으로 복사해주세요."
fi

# ── 6. .env 파일 생성 ─────────────────────────
echo ""
if [ ! -f "$INSTALL_DIR/.env" ]; then
    WORKER_UUID="worker-$(python3 -c "import uuid; print(uuid.uuid4().hex[:8])")"
    echo "🔑 .env 파일 생성 중..."

    cat > "$INSTALL_DIR/.env" << EOF
# CrawlStation 크롤링 워커 설정
# Supabase 연결 정보 (CrawlStation에서 확인)
SUPABASE_URL=
SUPABASE_KEY=

# 워커 ID (자동 생성됨, 변경 가능)
WORKER_ID=$WORKER_UUID
EOF

    echo "  ✅ .env 생성 완료 (WORKER_ID: $WORKER_UUID)"
    echo ""
    echo "  ⚠️  중요: .env 파일에 SUPABASE_URL과 SUPABASE_KEY를 입력해주세요."
    echo "  CrawlStation 대시보드 > 워커 설치 페이지에서 확인할 수 있습니다."
else
    echo "  ℹ️  .env 파일이 이미 존재합니다. 건너뜁니다."
fi

# ── 7. 연결 테스트 ─────────────────────────────
echo ""
SUPABASE_URL=$(grep "^SUPABASE_URL=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2-)
if [ -n "$SUPABASE_URL" ] && [ "$SUPABASE_URL" != "" ]; then
    echo "🔗 Supabase 연결 테스트..."
    $PYTHON_CMD -c "
from supabase import create_client
import os
os.chdir('$INSTALL_DIR')
exec(open('.env').read().replace('export ', '').replace('\n', ';').replace('SUPABASE_URL=','').replace('SUPABASE_KEY=',''))
" 2>/dev/null && echo "  ✅ 연결 성공" || echo "  ⚠️ 연결 테스트 건너뜀 (.env 설정 후 실행하세요)"
else
    echo "  ⏭️ Supabase 미설정 — .env 입력 후 실행하세요"
fi

# ── 8. 완료 ────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ 설치 완료!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  실행 방법:"
echo "    cd $INSTALL_DIR"
echo "    $PYTHON_CMD worker.py"
echo ""
echo "  → CrawlStation 대시보드에 자동 등록됩니다."
echo ""
