#!/usr/bin/env python3
"""
CrawlStation — 크롤링 워커 통합 인스톨러
Windows/macOS 모두 이 파일 하나로 설치 완료

사용법:
  python installer.py
  python3 installer.py

옵션:
  --url URL    Supabase URL (대화형 입력 대신 지정)
  --key KEY    Supabase Key (대화형 입력 대신 지정)
  --id  ID     워커 ID (지정하지 않으면 자동 생성)
  --dir DIR    설치 디렉토리 (기본: ~/CrawlWorker 또는 C:\CrawlWorker)
  --update     워커 파일만 업데이트 (재설치 없이)
"""
import subprocess
import sys
import os
import platform
import uuid
import shutil
import argparse

# ── 설정 ──────────────────────────────────────
INSTALL_DIR_MAC = os.path.expanduser("~/CrawlWorker")
INSTALL_DIR_WIN = r"C:\CrawlWorker"
MIN_PYTHON = (3, 10)
PACKAGES = ["playwright", "supabase"]

WORKER_FILES = [
    "worker.py",
    os.path.join("handlers", "__init__.py"),
    os.path.join("handlers", "base.py"),
    os.path.join("handlers", "kin.py"),
    os.path.join("handlers", "blog.py"),
    os.path.join("handlers", "serp.py"),
]


def main():
    args = parse_args()

    print()
    print("━" * 45)
    print("  CrawlStation — 크롤링 워커 설치")
    print("━" * 45)
    print()

    install_dir = args.dir or (INSTALL_DIR_WIN if is_windows() else INSTALL_DIR_MAC)

    if args.update:
        # 업데이트 모드: 워커 파일만 덮어쓰기
        print(f"📄 워커 파일 업데이트 모드 ({install_dir})")
        copy_worker_files(install_dir)
        print("\n  ✅ 업데이트 완료! worker.py를 재시작하세요.")
        return

    # 전체 설치
    check_python()
    setup_directory(install_dir)
    install_packages()
    install_browser()
    copy_worker_files(install_dir)
    create_env(install_dir, args)
    test_connection(install_dir)

    # 완료
    print()
    print("━" * 45)
    print("  ✅ 설치 완료!")
    print("━" * 45)
    print()
    print(f"  설치 위치: {install_dir}")
    py_cmd = "python" if is_windows() else "python3"
    print()
    print(f"  실행:")
    print(f"    cd {install_dir}")
    print(f"    {py_cmd} worker.py")
    print()
    print("  → CrawlStation 대시보드에 자동 등록됩니다.")
    print()


def parse_args():
    parser = argparse.ArgumentParser(description="CrawlStation 크롤링 워커 설치")
    parser.add_argument("--url", help="Supabase URL")
    parser.add_argument("--key", help="Supabase Service Key")
    parser.add_argument("--id", help="워커 ID (미지정 시 자동 생성)")
    parser.add_argument("--dir", help="설치 디렉토리")
    parser.add_argument("--update", action="store_true", help="워커 파일만 업데이트")
    return parser.parse_args()


# ── Step 1: Python 체크 ──────────────────────
def check_python():
    print("🔍 Python 버전 확인...")
    v = sys.version_info
    if v.major < MIN_PYTHON[0] or (v.major == MIN_PYTHON[0] and v.minor < MIN_PYTHON[1]):
        print(f"  ❌ Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ 필요 (현재: {v.major}.{v.minor})")
        if is_mac():
            print("     설치: brew install python@3.12")
        else:
            print("     설치: https://www.python.org/downloads/")
            print("     ※ 'Add Python to PATH' 반드시 체크!")
        sys.exit(1)
    print(f"  ✅ Python {v.major}.{v.minor}.{v.micro}")


# ── Step 2: 디렉토리 ─────────────────────────
def setup_directory(install_dir):
    print()
    print(f"📁 설치 디렉토리: {install_dir}")
    os.makedirs(os.path.join(install_dir, "handlers"), exist_ok=True)
    print(f"  ✅ 준비 완료")


# ── Step 3: pip 패키지 ───────────────────────
def install_packages():
    print()
    print("📦 패키지 설치 중...")
    for pkg in PACKAGES:
        try:
            cmd = [sys.executable, "-m", "pip", "install", "--quiet", pkg]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0 and "break-system-packages" in result.stderr:
                cmd.insert(4, "--break-system-packages")
                subprocess.run(cmd, check=True, capture_output=True)
            elif result.returncode != 0:
                # venv 시도
                raise RuntimeError(result.stderr[:200])
            print(f"  ✅ {pkg}")
        except Exception as e:
            print(f"  ⚠️ {pkg} 설치 실패: {e}")
            print(f"     수동 설치: pip install {pkg}")


# ── Step 4: Playwright 브라우저 ──────────────
def install_browser():
    print()
    print("🌐 Chromium 브라우저 설치 중... (1~2분 소요)")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, capture_output=True,
        )
        print("  ✅ Chromium 설치 완료")
    except subprocess.CalledProcessError:
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
                check=True, capture_output=True,
            )
            print("  ✅ Chromium + 의존성 설치 완료")
        except Exception as e:
            print(f"  ⚠️ Chromium 설치 실패: {e}")
            print("     수동 설치: python -m playwright install chromium")


# ── Step 5: 워커 파일 복사 ───────────────────
def copy_worker_files(install_dir):
    print()
    print("📄 워커 파일 설치 중...")
    script_dir = os.path.dirname(os.path.abspath(__file__))

    copied = 0
    for f in WORKER_FILES:
        src = os.path.join(script_dir, f)
        dst = os.path.join(install_dir, f)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    if copied > 0:
        print(f"  ✅ {copied}개 파일 복사 완료")
    else:
        print("  ⚠️ 소스 파일을 찾을 수 없습니다.")
        print(f"     worker.py와 handlers/를 {install_dir}에 수동 복사해주세요.")


# ── Step 6: .env 파일 생성 ───────────────────
def create_env(install_dir, args):
    print()
    env_path = os.path.join(install_dir, ".env")

    # 기존 .env 읽기
    existing = {}
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()

    # 값 결정 (우선순위: CLI 인자 → 기존 .env → 대화형 입력)
    supabase_url = args.url or existing.get("SUPABASE_URL", "")
    supabase_key = args.key or existing.get("SUPABASE_KEY", "")
    worker_id = args.id or existing.get("WORKER_ID", "")

    if not supabase_url:
        print("🔑 Supabase 연결 정보 입력")
        print("  (CrawlStation > 설치/연동 > 연결 정보에서 확인)")
        print()
        supabase_url = input("  SUPABASE_URL: ").strip()

    if not supabase_key:
        supabase_key = input("  SUPABASE_KEY: ").strip()

    if not worker_id:
        worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        print(f"\n  워커 ID 자동 생성: {worker_id}")

    # .env 쓰기
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f"""# CrawlStation 크롤링 워커 설정
SUPABASE_URL={supabase_url}
SUPABASE_KEY={supabase_key}
WORKER_ID={worker_id}
""")
    print(f"  ✅ .env 저장 완료")


# ── Step 7: 연결 테스트 ──────────────────────
def test_connection(install_dir):
    print()
    print("🔗 Supabase 연결 테스트...")

    env_path = os.path.join(install_dir, ".env")
    env = {}
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()

    url = env.get("SUPABASE_URL", "")
    key = env.get("SUPABASE_KEY", "")

    if not url or not key:
        print("  ⏭️ Supabase 미설정 — 나중에 .env를 수정하세요.")
        return

    try:
        from supabase import create_client
        sb = create_client(url, key)
        # worker_config 테이블 조회로 연결 확인
        res = sb.table("worker_config").select("id").limit(1).execute()
        print(f"  ✅ 연결 성공! (config: {len(res.data)}개)")
    except Exception as e:
        print(f"  ⚠️ 연결 실패: {e}")
        print("     .env 파일의 SUPABASE_URL, SUPABASE_KEY를 확인하세요.")


# ── 유틸 ─────────────────────────────────────
def is_windows():
    return platform.system() == "Windows"

def is_mac():
    return platform.system() == "Darwin"


if __name__ == "__main__":
    main()
