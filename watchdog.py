"""
CrawlStation Worker 감시자 (Watchdog)
작업 스케줄러가 5분마다 실행 → 워커가 꺼져 있으면 자동 복구 후 재시작
"""
import subprocess
import sys
import os
import urllib.request
import time

INSTALL_DIR = r"C:\CrawlWorker"
STATION_URL = "https://crawl-station.vercel.app"
FILES = [
    "worker.py",
    "supabase_rest.py",
    "handlers/__init__.py",
    "handlers/base.py",
    "handlers/kin.py",
    "handlers/kin_post.py",
    "handlers/blog.py",
    "handlers/serp.py",
    "handlers/area.py",
    "handlers/deep.py",
    "handlers/rank.py",
    "handlers/instagram.py",
    "handlers/instagram_post.py",
    "handlers/oclick.py",
]

LOG_FILE = os.path.join(INSTALL_DIR, "logs", "watchdog.log")


def _log(msg: str):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
        # 로그 파일 5MB 초과 시 초기화
        if os.path.getsize(LOG_FILE) > 5 * 1024 * 1024:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write(f"[{ts}] 로그 초기화\n")
    except Exception:
        pass


def is_worker_running() -> bool:
    """worker_gui.pyw 또는 worker.py 프로세스가 실행 중인지 확인"""
    try:
        result = subprocess.run(
            ["tasklist", "/v", "/fo", "csv"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.lower()
        return "worker_gui" in output or (
            "pythonw.exe" in output and "worker" in output
        )
    except Exception:
        return False


def download_latest_files() -> bool:
    """Station에서 최신 파일 다운로드"""
    ok = 0
    for f in FILES:
        try:
            target = os.path.join(INSTALL_DIR, f.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            urllib.request.urlretrieve(
                f"{STATION_URL}/api/download?file={f}", target
            )
            ok += 1
        except Exception as e:
            _log(f"  다운로드 실패: {f} — {e}")
    _log(f"  파일 업데이트: {ok}/{len(FILES)}")
    return ok > 0


def start_worker():
    """worker_gui.pyw 시작"""
    try:
        gui_path = os.path.join(INSTALL_DIR, "worker_gui.pyw")
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        subprocess.Popen(
            [pythonw, gui_path],
            cwd=INSTALL_DIR,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        _log("  워커 시작 완료")
    except Exception as e:
        _log(f"  워커 시작 실패: {e}")


def main():
    if is_worker_running():
        return  # 정상 실행 중 — 아무것도 안 함

    _log("워커가 꺼져 있음 → 복구 시작")
    download_latest_files()
    time.sleep(1)
    start_worker()


if __name__ == "__main__":
    main()
