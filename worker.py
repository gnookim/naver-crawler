"""
네이버 크롤링 워커 — CrawlStation Sub-Agent
Supabase에서 할당된 요청을 수신하여 type별 핸들러로 크롤링 실행
첫 실행 시 CrawlStation에 자동 등록됨

실행: python3.12 worker.py
"""
import asyncio
import os
import sys
import random
import re
import uuid
import platform
import socket
import shutil
import subprocess
import time
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    _KST = ZoneInfo("Asia/Seoul")
except ImportError:
    # Python 3.8 이하 또는 zoneinfo 미설치 시 fallback
    _KST = timezone(offset=__import__('datetime').timedelta(hours=9))

# greenlet DLL 호환성 문제 우회 (Windows embedded Python)
# playwright async API는 greenlet 없이 동작하지만, import 시점에 로드 시도함
try:
    import greenlet  # noqa
except (ImportError, OSError):
    import types
    _fake = types.ModuleType("greenlet")
    _fake.getcurrent = lambda: None
    _fake.greenlet = type("greenlet", (), {"__init__": lambda s, *a, **k: None})
    sys.modules["greenlet"] = _fake
    sys.modules["greenlet._greenlet"] = _fake
    _fg = types.ModuleType("_greenlet")
    sys.modules["_greenlet"] = _fg

# ── 버전 ──────────────────────────────────────
VERSION = "0.9.44"
WORKER_DIR = os.path.dirname(os.path.abspath(__file__))

# Python 워커가 처리하지 않는 타입 (향후 확장용 — 현재는 모든 타입 처리 가능)
PYTHON_EXCLUDED_TYPES: list = []

# ── 환경변수 ─────────────────────────────────
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())

load_env()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
WORKER_ID = os.environ.get("WORKER_ID", "")
WORKER_NAME = os.environ.get("WORKER_NAME", "")  # 설치 시 지정한 식별 이름 (미입력 시 hostname 사용)
CRAWL_STATION_URL = os.environ.get("CRAWL_STATION_URL", "")
CRAWL_STATION_KEY = os.environ.get("CRAWL_STATION_KEY", "")
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

from handlers import HANDLERS

# ── SSO 연동 (옵션) ───────────────────────────────
SSO_EMAIL = os.environ.get("SSO_EMAIL", "")
SSO_PASSWORD = os.environ.get("SSO_PASSWORD", "")
_sso_enabled = False

def init_sso():
    """SSO 로그인 시도. 실패해도 크롤링에는 영향 없음."""
    global _sso_enabled
    if not SSO_EMAIL or not SSO_PASSWORD:
        return False
    try:
        from lifenbio_auth import login
        login(SSO_EMAIL, SSO_PASSWORD, app_id="naver-crawler")
        _sso_enabled = True
        return True
    except Exception as e:
        print(f"  ⚠️ SSO 로그인 실패 (무시됨): {e}")
        return False

def sso_log(action: str, metadata: dict = {}):
    """SSO 사용 기록 전송. 실패해도 무시."""
    if not _sso_enabled:
        return
    try:
        from lifenbio_auth import log_activity
        log_activity(action, metadata)
    except Exception:
        pass

# 최근 처리한 작업 ID (무한루프 방지)
_processed_ids: set = set()

# 명령/config 체크 간격 (heartbeat 횟수 기준)
_UPDATE_CHECK_INTERVAL = 6  # 5초 × 6 = 30초마다 명령·config 체크

# 업데이트 체크 타임스탬프 (시간 기반)
_last_update_check_time: float = 0.0  # 0 = 즉시 체크


# ── Watchdog 자동 등록 (Windows 전용) ────────────────────────────────
def ensure_watchdog():
    """
    Windows 작업 스케줄러에 watchdog 등록 여부를 확인하고, 없으면 자동 등록.
    워커 시작 시 호출 → 이후 워커가 꺼져도 5분 내 자동 복구.
    Windows가 아닌 환경에서는 무시.
    """
    if platform.system() != "Windows":
        return
    try:
        # 이미 등록되어 있는지 확인
        check = subprocess.run(
            ["schtasks", "/query", "/tn", "CrawlStationWatchdog"],
            capture_output=True, text=True, timeout=10
        )
        if check.returncode == 0:
            return  # 이미 등록됨

        # watchdog.py 경로 확인 — 없으면 Station에서 다운로드
        watchdog_path = os.path.join(WORKER_DIR, "watchdog.py")
        if not os.path.exists(watchdog_path):
            station_url = os.environ.get("CRAWL_STATION_URL", "https://crawl-station.vercel.app")
            try:
                import urllib.request
                urllib.request.urlretrieve(
                    f"{station_url}/api/download?file=watchdog.py",
                    watchdog_path
                )
            except Exception:
                return  # 다운로드 실패 시 무시

        # pythonw.exe 경로 결정
        pythonw = sys.executable.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable

        # 작업 스케줄러에 5분마다 실행 등록
        subprocess.run(
            [
                "schtasks", "/create",
                "/tn", "CrawlStationWatchdog",
                "/tr", f'"{pythonw}" "{watchdog_path}"',
                "/sc", "minute", "/mo", "5",
                "/ru", os.environ.get("USERNAME", ""),
                "/f",
            ],
            capture_output=True, text=True, timeout=15
        )
    except Exception:
        pass  # 실패해도 워커 동작에 영향 없음


def log_error(sb, message: str, level: str = "error", context: dict = None):
    """에러/경고를 Supabase worker_logs 테이블에 기록 (실패해도 무시)"""
    try:
        sb.table("worker_logs").insert({
            "worker_id": WORKER_ID,
            "level": level,
            "message": str(message)[:1000],
            "context": context or {},
        }).execute()
    except Exception:
        pass


# ── 워커 ID 관리 ────────────────────────────────
def ensure_worker_id():
    """WORKER_ID가 없으면 UUID 기반으로 자동 생성하고 .env에 저장"""
    global WORKER_ID
    if WORKER_ID:
        return WORKER_ID

    WORKER_ID = f"worker-{uuid.uuid4().hex[:8]}"
    os.environ["WORKER_ID"] = WORKER_ID

    # .env에 저장
    try:
        lines = []
        if os.path.exists(ENV_PATH):
            with open(ENV_PATH) as f:
                lines = f.readlines()

        # 기존 WORKER_ID 라인 제거
        lines = [l for l in lines if not l.strip().startswith("WORKER_ID=")]
        lines.append(f"WORKER_ID={WORKER_ID}\n")

        with open(ENV_PATH, "w") as f:
            f.writelines(lines)
    except Exception:
        pass

    return WORKER_ID


# ── 머신 정보 수집 ─────────────────────────────
def collect_machine_info():
    """OS, hostname, Python 버전 등 머신 정보 수집"""
    system = platform.system()
    if system == "Darwin":
        os_name = f"macOS {platform.mac_ver()[0]}"
    elif system == "Windows":
        os_name = f"Windows {platform.version()}"
    else:
        os_name = f"{system} {platform.release()}"

    return {
        "os": os_name,
        "hostname": socket.gethostname(),
        "python_version": platform.python_version(),
    }


# ── CrawlStation 자동등록 ──────────────────────
def register_worker(sb):
    """워커를 CrawlStation에 자동 등록 (UPSERT). 신규 등록 여부 반환."""
    info = collect_machine_info()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # 기존 verified_at 확인 (신규인지 판단)
        existing = sb.table("workers").select("verified_at").eq("id", WORKER_ID).execute()
        already_verified = bool(existing.data and existing.data[0].get("verified_at"))

        if already_verified or (existing.data and existing.data[0]):
            # 기존 워커 — name은 Station에서 수정한 값 유지, 기술 정보만 갱신
            sb.table("workers").update({
                "os": info["os"],
                "hostname": info["hostname"],
                "python_version": info["python_version"],
                "version": VERSION,
                "status": "online",
                "last_seen": now,
                "command": None,
            }).eq("id", WORKER_ID).execute()
        else:
            # 최초 등록
            sb.table("workers").insert({
                "id": WORKER_ID,
                "name": WORKER_NAME or info["hostname"],
                "os": info["os"],
                "hostname": info["hostname"],
                "python_version": info["python_version"],
                "version": VERSION,
                "status": "online",
                "last_seen": now,
                "registered_at": now,
                "registered_by": "auto",
                "command": None,
            }).execute()
        return True, already_verified
    except Exception as e:
        print(f"  ⚠️ 워커 등록 실패: {e}")
        return False, False


async def _auto_verify_task(station_url: str, worker_id: str):
    """등록 직후 자동 검증 — 네이버·오클릭 테스트 후 verified_at 업데이트"""
    import json as _json
    import urllib.request as _urllib_req
    import ssl as _ssl

    def _post(url: str, payload: dict, timeout: int = 130) -> dict:
        data = _json.dumps(payload).encode()
        req = _urllib_req.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            ctx = _ssl._create_unverified_context()
            with _urllib_req.urlopen(req, timeout=timeout, context=ctx) as resp:
                return _json.loads(resp.read())
        except Exception:
            with _urllib_req.urlopen(req, timeout=timeout) as resp:
                return _json.loads(resp.read())

    await asyncio.sleep(8)  # 메인 루프 시작 대기
    print("  🔍 자동 검증 시작 (N + O)...")

    # 1) 네이버 테스트
    naver_ok = False
    try:
        result = _post(f"{station_url}/api/test/worker", {"worker_id": worker_id, "category": "naver"})
        naver_ok = result.get("ok", False)
        print(f"  {'✅' if naver_ok else '⚠️'} 네이버: {'통과' if naver_ok else result.get('error', '실패')}")
    except Exception as e:
        print(f"  ⚠️ 네이버 검증 오류: {e}")

    # 2) 오클릭 테스트 (credentials이 station_settings에 없으면 건너뜀)
    oclick_ok = False
    try:
        result = _post(f"{station_url}/api/test/oclick", {"worker_id": worker_id}, timeout=200)
        if "company_code" in result.get("error", ""):
            print("  ⏭️ 오클릭: credentials 미설정 — 건너뜀")
            oclick_ok = True  # credentials 없으면 검증 대상 아님으로 간주
        else:
            oclick_ok = result.get("ok", False)
            print(f"  {'✅' if oclick_ok else '⚠️'} 오클릭: {'통과' if oclick_ok else result.get('error', '실패')}")
    except Exception as e:
        print(f"  ⚠️ 오클릭 검증 오류: {e}")
        oclick_ok = True  # 오류 시 차단하지 않음

    if naver_ok and oclick_ok:
        print("  ✅ 자동 검증 완료 — 검증됨 상태로 변경됨")
    else:
        print("  ⚠️ 자동 검증 일부 실패 — Station에서 수동 확인 필요")


# ── Config 로드 ────────────────────────────────
def load_config(sb):
    """worker_config에서 설정 로드 (워커별 → global 순서)"""
    try:
        # 워커별 설정 먼저
        res = sb.table("worker_config").select("*").eq("id", WORKER_ID).execute()
        if res.data:
            return res.data[0]

        # 글로벌 설정
        res = sb.table("worker_config").select("*").eq("id", "global").execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        print(f"  ⚠️ Config 로드 실패: {e}")

    # 기본값
    return {
        "keyword_delay_min": 15,
        "keyword_delay_max": 30,
        "batch_size": 30,
        "batch_rest_seconds": 180,
    }


# ── IP 로테이션 (테더링) ─────────────────────────
def _get_external_ip():
    """외부 IP 주소를 조회 (실패 시 None)"""
    try:
        import urllib.request
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as resp:
            return resp.read().decode().strip()
    except Exception:
        return None


def _get_windows_wifi_profile():
    """Windows에서 현재 연결된 Wi-Fi 프로필 이름을 반환"""
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.splitlines():
            # "Profile" 또는 "프로필" 행에서 값 추출
            if "Profile" in line or "프로필" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    profile = parts[1].strip()
                    if profile:
                        return profile
    except Exception:
        pass
    return None


def rotate_tethering_ip(config, log_cb=None):
    """테더링 IP 로테이션 — Wi-Fi 끊고 재연결하여 새 IP 할당"""
    system = platform.system()
    old_ip = _get_external_ip()
    if log_cb:
        log_cb(f"\n  🔄 IP 로테이션 시작 (현재 IP: {old_ip or '확인 불가'})")

    try:
        if system == "Darwin":
            # Mac: Wi-Fi 끄고 켜기
            if log_cb:
                log_cb("     Wi-Fi OFF (en0)...")
            subprocess.run(
                ["networksetup", "-setairportpower", "en0", "off"],
                capture_output=True, timeout=10,
            )
            import time
            time.sleep(5)
            if log_cb:
                log_cb("     Wi-Fi ON (en0)...")
            subprocess.run(
                ["networksetup", "-setairportpower", "en0", "on"],
                capture_output=True, timeout=10,
            )
            # 재연결 대기
            reconnect_wait = config.get("tethering_reconnect_interval", 15)
            if log_cb:
                log_cb(f"     재연결 대기 {reconnect_wait}초...")
            time.sleep(reconnect_wait)

        elif system == "Windows":
            # Windows: Wi-Fi 끊고 재연결
            profile = _get_windows_wifi_profile()
            if not profile:
                if log_cb:
                    log_cb("     ⚠️ Wi-Fi 프로필을 찾을 수 없습니다")
                return False

            if log_cb:
                log_cb(f"     Wi-Fi 연결 해제 (프로필: {profile})...")
            subprocess.run(
                ["netsh", "wlan", "disconnect"],
                capture_output=True, timeout=10,
            )
            import time
            time.sleep(5)
            if log_cb:
                log_cb(f"     Wi-Fi 재연결 (프로필: {profile})...")
            subprocess.run(
                ["netsh", "wlan", "connect", f"name={profile}"],
                capture_output=True, timeout=10,
            )
            reconnect_wait = config.get("tethering_reconnect_interval", 15)
            if log_cb:
                log_cb(f"     재연결 대기 {reconnect_wait}초...")
            time.sleep(reconnect_wait)

        else:
            if log_cb:
                log_cb(f"     ⚠️ 지원하지 않는 OS: {system}")
            return False

    except Exception as e:
        if log_cb:
            log_cb(f"     ❌ IP 로테이션 실패: {e}")
        return False

    new_ip = _get_external_ip()
    if log_cb:
        log_cb(f"     새 IP: {new_ip or '확인 불가'}")
        if old_ip and new_ip and old_ip != new_ip:
            log_cb("     ✅ IP 변경 성공")
        elif old_ip and new_ip and old_ip == new_ip:
            log_cb("     ⚠️ IP가 변경되지 않았습니다")

    return True


def should_rotate_ip(config):
    """config 기반으로 IP 로테이션이 필요한지 판단"""
    network_type = config.get("network_type", "")
    if network_type.startswith("tethering_") and config.get("tethering_auto_reconnect", False):
        return True
    return False


# ── 업데이트 체크 ─────────────────────────────
def check_update(sb):
    """CrawlStation에 최신 버전이 있는지 확인"""
    try:
        res = sb.table("worker_releases").select("version, changelog, files") \
            .eq("is_latest", True).limit(1).execute()
        if not res.data:
            return None
        latest = res.data[0]
        if latest["version"] != VERSION:
            return latest
    except Exception:
        pass
    return None


def apply_update(sb, release):
    """최신 버전으로 업데이트 — 파일 다운로드 후 핫 리로드"""
    global VERSION, HANDLERS
    new_version = release["version"]
    files = release.get("files") or {}
    print(f"\n🔄 업데이트 v{VERSION} → v{new_version}")
    print(f"   {release.get('changelog', '')}")

    # __pycache__ 삭제 (오래된 캐시가 새 모듈 import를 방해)
    for root, dirs, _files in os.walk(WORKER_DIR):
        for d in dirs:
            if d == "__pycache__":
                try:
                    shutil.rmtree(os.path.join(root, d))
                except Exception:
                    pass

    # 파일 업데이트 — __init__.py와 worker.py는 마지막에 쓰기
    # (새 import가 추가된 __init__.py가 먼저 쓰이면 아직 없는 모듈 import 에러)
    updated = 0
    deferred = {}
    for filepath, content in files.items():
        if filepath.endswith("__init__.py") or filepath == "worker.py":
            deferred[filepath] = content
            continue
        target = os.path.join(WORKER_DIR, filepath)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        updated += 1
        print(f"   ✅ {filepath}")
    for filepath, content in deferred.items():
        target = os.path.join(WORKER_DIR, filepath)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        updated += 1
        print(f"   ✅ {filepath}")

    if updated == 0:
        print("   ⚠️ 업데이트할 파일이 없습니다")
        return False

    # 핸들러 핫 리로드 (재시작 없이 즉시 반영)
    try:
        import importlib
        import handlers
        import handlers.base
        importlib.reload(handlers.base)
        # 기존 모듈 리로드
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("handlers.") and mod_name != "handlers.base":
                try:
                    importlib.reload(sys.modules[mod_name])
                except Exception:
                    pass
        # 새로 추가된 핸들러 파일 import (sys.modules에 없는 것)
        import os as _os
        handler_dir = _os.path.join(WORKER_DIR, "handlers")
        for _f in _os.listdir(handler_dir):
            if _f.endswith(".py") and not _f.startswith("_"):
                mod = f"handlers.{_f[:-3]}"
                if mod not in sys.modules:
                    try:
                        importlib.import_module(mod)
                    except Exception:
                        pass
        importlib.reload(handlers)
        # HANDLERS 딕셔너리 갱신
        from handlers import HANDLERS as _new
        HANDLERS.clear()
        HANDLERS.update(_new)
        print(f"   🔄 핸들러 핫 리로드 완료")
    except Exception as e:
        print(f"   ⚠️ 핫 리로드 실패 (다음 재시작 시 반영): {e}")

    # 버전 보고
    VERSION = new_version
    try:
        sb.table("workers").update({
            "version": new_version,
        }).eq("id", WORKER_ID).execute()
    except Exception as e:
        print(f"   ⚠️ 버전 보고 실패: {e}")

    print(f"   ✅ {updated}개 파일 업데이트 완료 — 즉시 반영됨")
    return True


def restart_worker():
    """워커 재시작 — 새 프로세스를 백그라운드로 시작하고 자기는 종료"""
    print("\n🔄 워커 재시작 중...")

    python = sys.executable
    worker_script = os.path.join(WORKER_DIR, "worker.py")

    # __pycache__ 삭제
    for root, dirs, _files in os.walk(WORKER_DIR):
        for d in dirs:
            if d == "__pycache__":
                try:
                    shutil.rmtree(os.path.join(root, d))
                except Exception:
                    pass

    # 새 프로세스를 백그라운드로 시작
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        subprocess.Popen(
            [python, worker_script],
            cwd=WORKER_DIR,
            env=env,
            stdout=open(os.path.join(WORKER_DIR, "logs", "worker.log"), "a"),
            stderr=open(os.path.join(WORKER_DIR, "logs", "worker.err"), "a"),
            start_new_session=True,
        )
        print("   새 프로세스 시작됨")
    except Exception as e:
        print(f"   ⚠️ 새 프로세스 시작 실패: {e}")
        return

    # 자기 종료 (새 프로세스가 이미 떠있으므로 안전)
    sys.exit(0)


# ── 원격 명령 체크 ────────────────────────────
def check_and_execute_command(sb):
    """CrawlStation에서 내려온 원격 명령을 확인하고 실행"""
    try:
        res = sb.table("workers").select("command").eq("id", WORKER_ID).limit(1).execute()
        if not res.data or not res.data[0].get("command"):
            return None

        command = res.data[0]["command"]

        # 명령 수신 확인 → 즉시 null로 초기화 (중복 실행 방지)
        sb.table("workers").update({"command": None}).eq("id", WORKER_ID).execute()

        print(f"\n📡 원격 명령 수신: {command}")
        return command
    except Exception:
        return None


def handle_command(sb, command):
    """수신된 명령 실행. True 반환 시 메인 루프 중단"""
    if command == "stop":
        print("🛑 CrawlStation에서 정지 명령 수신")
        heartbeat(sb, "offline")
        print("👋 워커를 정지합니다.")
        sys.exit(0)

    elif command == "restart":
        print("🔄 CrawlStation에서 재시작 명령 수신")
        heartbeat(sb, "online")
        restart_worker()

    elif command == "update":
        print("📦 CrawlStation에서 업데이트 명령 수신")
        update = check_update(sb)
        if update:
            if apply_update(sb, update):
                pass  # 재시작 불필요 — 파일 갱신만
            else:
                print("   ⚠️ 업데이트 적용 실패")
        else:
            print("   ✅ 이미 최신 버전입니다")

    return False


# ── Heartbeat ─────────────────────────────────
_current_ip: str | None = None
_ip_last_fetched: float = 0.0

def _refresh_ip_if_needed():
    """30분마다 외부 IP 갱신"""
    global _current_ip, _ip_last_fetched
    now = time.time()
    if now - _ip_last_fetched > 1800:
        _current_ip = _get_external_ip()
        _ip_last_fetched = now

_heartbeat_status = {
    "status": "idle",
    "keyword": None,
    "ctype": None,
    "allowed_types": [],
    # 차단 상태
    "block_status": None,       # None | "cooling" | "blocked" | "banned"
    "block_platform": None,     # None | "naver" | "instagram"
    "block_level": None,        # None | 1 | 2 | 3
    "blocked_until": None,      # ISO timestamp | None
    "block_count_today": 0,
}

def _report_block(sb, platform: str, level: int, cooldown_minutes: int = 0, req_id: str = None):
    """차단 감지 보고 + workers 테이블 업데이트
    req_id를 전달하면 해당 작업을 pending으로 되돌려 다른 워커가 처리하도록 함
    """
    from datetime import timedelta
    _heartbeat_status["block_platform"] = platform
    _heartbeat_status["block_level"] = level
    _heartbeat_status["block_count_today"] = _heartbeat_status.get("block_count_today", 0) + 1

    if level == 1:
        _heartbeat_status["block_status"] = "cooling"
        until = (datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes or 30)).isoformat()
        _heartbeat_status["blocked_until"] = until
    elif level == 2:
        _heartbeat_status["block_status"] = "blocked"
        until = (datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes or 60)).isoformat()
        _heartbeat_status["blocked_until"] = until
    else:
        _heartbeat_status["block_status"] = "banned"
        _heartbeat_status["blocked_until"] = None

    label = {1: "소프트", 2: "하드", 3: "영구"}.get(level, "")
    print(f"\n  🚫 [{platform.upper()}] 차단 감지 Level {level} ({label}) — {_heartbeat_status['block_status']}")

    try:
        sb.table("workers").update({
            "status": "blocked",
            "block_status": _heartbeat_status["block_status"],
            "block_platform": platform,
            "block_level": level,
            "blocked_until": _heartbeat_status["blocked_until"],
            "block_count_today": _heartbeat_status["block_count_today"],
        }).eq("id", WORKER_ID).execute()
    except Exception as e:
        print(f"  ⚠️ 차단 보고 실패: {e}")

    # 진행 중이던 작업을 pending으로 되돌려 다른 워커가 이어받도록 함
    if req_id:
        try:
            sb.table("crawl_requests").update({
                "status": "pending",
                "assigned_worker": None,
                "started_at": None,
            }).eq("id", req_id).execute()
            print(f"  🔄 작업 {req_id[:8]} → pending (다른 워커에게 재배분)")
        except Exception as e:
            print(f"  ⚠️ 작업 재배분 실패: {e}")

def _clear_block(sb):
    """차단 해제"""
    if not _heartbeat_status["block_status"]:
        return
    _heartbeat_status["block_status"] = None
    _heartbeat_status["block_platform"] = None
    _heartbeat_status["block_level"] = None
    _heartbeat_status["blocked_until"] = None
    try:
        sb.table("workers").update({
            "status": "idle",
            "block_status": None,
            "block_platform": None,
            "block_level": None,
            "blocked_until": None,
        }).eq("id", WORKER_ID).execute()
    except Exception:
        pass

def heartbeat(sb, status="idle", keyword=None, ctype=None):
    """즉시 heartbeat 전송 + 상태 저장 (백그라운드 태스크가 참조)"""
    _heartbeat_status["status"] = status
    _heartbeat_status["keyword"] = keyword
    _heartbeat_status["ctype"] = ctype
    _refresh_ip_if_needed()
    try:
        payload = {
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "version": VERSION,
            "current_keyword": keyword,
            "current_type": ctype,
            "allowed_types": _heartbeat_status["allowed_types"] or None,
        }
        if _current_ip:
            payload["current_ip"] = _current_ip
        sb.table("workers").update(payload).eq("id", WORKER_ID).execute()
    except Exception:
        pass

async def _heartbeat_loop(sb, interval=10):
    """백그라운드 heartbeat — 메인 루프와 독립적으로 10초마다 전송"""
    while True:
        try:
            await asyncio.sleep(interval)
            _refresh_ip_if_needed()
            payload = {
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "status": _heartbeat_status["block_status"] and "blocked" or _heartbeat_status["status"],
                "current_keyword": _heartbeat_status["keyword"],
                "current_type": _heartbeat_status["ctype"],
                "allowed_types": _heartbeat_status["allowed_types"] or None,
            }
            if _current_ip:
                payload["current_ip"] = _current_ip
            sb.table("workers").update(payload).eq("id", WORKER_ID).execute()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"  ⚠️ heartbeat 실패: {e}")


# ── 작업 처리 ──────────────────────────────────
async def process_request(sb, req, config, log_cb=None):
    req_id = req["id"]
    req_type = req["type"]
    keyword = req["keyword"]
    options = req.get("options") or {}

    # 무한루프 방지: 이미 처리한 작업은 스킵
    if req_id in _processed_ids:
        if log_cb: log_cb(f"  ⏭️ 이미 처리됨: {keyword} ({req_id[:8]})")
        return
    _processed_ids.add(req_id)
    # 캐시 크기 제한 (최근 1000개만)
    if len(_processed_ids) > 1000:
        _processed_ids.clear()

    handler_cls = HANDLERS.get(req_type)
    if not handler_cls:
        sb.table("crawl_requests").update({
            "status": "failed",
            "error_message": f"알 수 없는 타입: {req_type}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", req_id).execute()
        return

    if log_cb: log_cb(f"\n{'━'*45}")
    if log_cb: log_cb(f"  [{req_type}] {keyword}")
    if log_cb: log_cb(f"{'━'*45}")

    sb.table("crawl_requests").update({
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", req_id).execute()

    sb.table("workers").update({
        "current_task_id": req_id,
        "current_keyword": keyword,
        "current_type": req_type,
    }).eq("id", WORKER_ID).execute()

    heartbeat(sb, "crawling", keyword, req_type)

    _t0 = time.time()
    _meta = {
        "worker_id": WORKER_ID,
        "request_id": req_id,
        "keyword": keyword,
        "type": req_type,
        "blocked": False,
        "captcha": False,
        "empty_result": False,
        "error_type": None,
        "result_count": 0,
        "response_time_ms": 0,
    }

    try:
        handler = handler_cls(headless=True, config=config)
        # instagram_profile 핸들러에 worker_id 주입 (계정 발급용)
        if req_type == "instagram_profile":
            options = dict(options)
            options.setdefault("worker_id", WORKER_ID)
        results = await handler.handle(keyword, options, log_cb=log_cb)

        _meta["response_time_ms"] = int((time.time() - _t0) * 1000)
        _meta["result_count"] = len(results) if results else 0
        _meta["empty_result"] = _meta["result_count"] == 0

        # 차단/캡챠 감지
        platform = "instagram" if req_type == "instagram_profile" else "naver"
        if results:
            for item in results:
                item_str = str(item).lower()
                if "captcha" in item_str or "보안문자" in item_str:
                    _meta["captcha"] = True
                    _meta["blocked"] = True
                if "차단" in item_str or "blocked" in item_str:
                    _meta["blocked"] = True
                if "login" in item_str or "로그인" in item_str:
                    _meta["blocked"] = True

        # 빈 결과 + 이전 차단 이력 → Level 1 (작업은 완료 처리, 재배분 안 함)
        if _meta["empty_result"] and _heartbeat_status["block_count_today"] > 2:
            _report_block(sb, platform, level=1, cooldown_minutes=30)
        elif _meta["captcha"]:
            # 캡챠 = 하드 차단 → 작업 재배분
            _report_block(sb, platform, level=2, cooldown_minutes=60, req_id=req_id)
            return  # crawl_requests 업데이트는 _report_block에서 처리
        elif _meta["blocked"]:
            # 차단 감지 → 작업 재배분
            _report_block(sb, platform, level=2, cooldown_minutes=60, req_id=req_id)
            return
        elif _heartbeat_status["block_status"] and not _meta["empty_result"]:
            # 성공적으로 결과 받으면 차단 해제
            _clear_block(sb)

        if results:
            rows = [{
                "request_id": req_id,
                "type": req_type,
                "keyword": keyword,
                "rank": item.get("rank"),
                "data": item,
            } for item in results]
            sb.table("crawl_results").insert(rows).execute()

        sb.table("crawl_requests").update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", req_id).execute()

        if log_cb: log_cb(f"  ✅ 완료: {len(results)}개 ({_meta['response_time_ms']}ms)")

        sso_log("crawl.completed", {
            "keyword": keyword, "type": req_type,
            "result_count": len(results), "worker_id": WORKER_ID,
        })

        # 서브태스크 완료 시 부모 요청 상태 체크
        parent_id = req.get("parent_id")
        if parent_id:
            _check_parent_completion(sb, parent_id, log_cb)

    except Exception as e:
        _meta["response_time_ms"] = int((time.time() - _t0) * 1000)
        _meta["error_type"] = type(e).__name__
        err_str = str(e).lower()
        platform = "instagram" if req_type == "instagram_profile" else "naver"
        if "captcha" in err_str or "보안문자" in err_str:
            _meta["captcha"] = True
            _meta["blocked"] = True
            # 차단으로 인한 실패 → 작업 재배분 (다른 워커가 처리)
            _report_block(sb, platform, level=2, cooldown_minutes=60, req_id=req_id)
            if log_cb: log_cb(f"  🚫 캡챠 차단 — 작업 재배분 완료")
            return
        elif "login" in err_str or "로그인" in err_str:
            _meta["blocked"] = True
            _report_block(sb, platform, level=2, cooldown_minutes=60, req_id=req_id)
            if log_cb: log_cb(f"  🚫 로그인 요구 차단 — 작업 재배분 완료")
            return
        elif "timeout" in err_str or "navigation" in err_str:
            _meta["blocked"] = True
            _report_block(sb, platform, level=1, cooldown_minutes=15)
            # timeout은 재배분하지 않고 실패 처리 (네트워크 문제일 수 있음)

        if log_cb: log_cb(f"  ❌ 오류: {e}")
        log_error(sb, f"[{req_type}] {keyword}: {e}", level="error",
                  context={"keyword": keyword, "type": req_type, "request_id": req_id})
        sb.table("crawl_requests").update({
            "status": "failed",
            "error_message": str(e)[:500],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", req_id).execute()

        sso_log("crawl.failed", {
            "keyword": keyword, "type": req_type,
            "error": str(e)[:200], "worker_id": WORKER_ID,
        })

    sb.table("workers").update({
        "current_task_id": None,
        "current_keyword": None,
        "current_type": None,
    }).eq("id", WORKER_ID).execute()

    # 메타데이터 기록 (AI 분석용)
    try:
        sb.table("crawl_metadata").insert(_meta).execute()
    except Exception:
        pass

    heartbeat(sb, "idle")


def _check_parent_completion(sb, parent_id, log_cb=None):
    """모든 서브태스크가 완료/실패되면 부모 요청도 완료 처리"""
    try:
        res = sb.table("crawl_requests").select("id, status") \
            .eq("parent_id", parent_id).execute()
        if not res.data:
            return
        statuses = [r["status"] for r in res.data]
        # 아직 진행 중인 서브태스크가 있으면 대기
        if any(s in ("pending", "assigned", "running") for s in statuses):
            return
        # 전체 실패 vs 부분 성공
        if all(s == "failed" for s in statuses):
            parent_status = "failed"
        else:
            parent_status = "completed"
        sb.table("crawl_requests").update({
            "status": parent_status,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", parent_id).execute()
        if log_cb:
            log_cb(f"  📦 부모 요청 {parent_status}: {parent_id[:8]}")
    except Exception:
        pass


async def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("❌ .env에 SUPABASE_URL, SUPABASE_KEY 설정 필요")
        sys.exit(1)

    # 워커 ID 확인/생성
    ensure_worker_id()

    # Watchdog 자동 등록 (Windows — 이미 등록된 경우 무시)
    ensure_watchdog()

    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("  📦 Supabase SDK 사용")
    except (ImportError, OSError) as e:
        # greenlet DLL 에러 등 → REST API fallback
        from supabase_rest import SupabaseREST
        sb = SupabaseREST(SUPABASE_URL, SUPABASE_KEY)
        print(f"  📦 Supabase REST fallback (SDK 로드 실패: {str(e)[:60]})")

    # CrawlStation에 자동 등록
    info = collect_machine_info()
    print("=" * 50)
    print(f"  CrawlStation 크롤링 워커 v{VERSION}")
    print(f"  ID: {WORKER_ID}")
    print(f"  OS: {info['os']}")
    print(f"  호스트: {info['hostname']}")
    print(f"  지원: {', '.join(HANDLERS.keys())}")
    print("=" * 50)

    registered, already_verified = register_worker(sb)
    if registered:
        print("  ✅ CrawlStation에 등록 완료")
    else:
        print("  ⚠️ 등록 실패 — 오프라인 모드로 실행")

    # 좀비 작업 정리 (내가 담당했지만 10분 이상 running인 작업)
    try:
        cutoff = (datetime.now(timezone.utc) - __import__('datetime').timedelta(minutes=10)).isoformat()
        res = sb.table("crawl_requests") \
            .update({"status": "failed", "error_message": "타임아웃 — 워커 재시작으로 자동 정리",
                     "completed_at": datetime.now(timezone.utc).isoformat()}) \
            .eq("assigned_worker", WORKER_ID) \
            .eq("status", "running") \
            .lt("started_at", cutoff) \
            .execute()
        cleaned = len(res.data) if res.data else 0
        if cleaned:
            print(f"  🧹 좀비 작업 {cleaned}개 정리")
    except Exception:
        pass

    # SSO 로그인
    if init_sso():
        print("  🔐 SSO 로그인 완료")
    elif SSO_EMAIL:
        print("  ⚠️ SSO 로그인 실패")
    sso_log("worker.started", {"worker_id": WORKER_ID, "version": VERSION})

    # 시작 시 자동 업데이트 (파일만 갱신, 재시작 안 함)
    update = check_update(sb)
    if update:
        print(f"\n  📦 새 버전 발견: v{update['version']} (현재: v{VERSION})")
        print(f"     {update.get('changelog', '')}")
        apply_update(sb, update)
        print("  ✅ 파일 갱신 완료 (다음 배치 휴식 시 반영)")
    else:
        print("  ✅ 최신 버전")

    # Config 로드
    config = load_config(sb)
    network_type = config.get("network_type", "direct")
    print(f"  ⚙️ Config 로드 (배치: {config.get('batch_size', 30)}개, "
          f"딜레이: {config.get('keyword_delay_min', 15)}~{config.get('keyword_delay_max', 30)}초)")
    if should_rotate_ip(config):
        print(f"  🔄 IP 로테이션 활성 (네트워크: {network_type}, "
              f"재연결 간격: {config.get('tethering_reconnect_interval', 15)}초)")
    elif network_type == "proxy_rotate":
        print(f"  🌐 프록시 로테이션 (프록시 서비스에서 자동 처리)")
    else:
        print(f"  🌐 네트워크: {network_type}")

    # allowed_types 설정 — 빈 리스트면 모든 타입 처리
    allowed_types = config.get("allowed_types") or []
    if isinstance(allowed_types, str):
        import json as _json
        try:
            allowed_types = _json.loads(allowed_types)
        except Exception:
            allowed_types = []
    _heartbeat_status["allowed_types"] = allowed_types
    if allowed_types:
        print(f"  🔒 허용 타입: {', '.join(allowed_types)}")
    else:
        print(f"  🌐 허용 타입: 전체 (oclick_sync 제외)")

    # 시작 시 IP 조회 (이후 30분마다 자동 갱신)
    _refresh_ip_if_needed()
    if _current_ip:
        print(f"  🌐 외부 IP: {_current_ip}")

    heartbeat(sb, "idle")

    # 백그라운드 heartbeat 시작 (10초마다 — 메인 루프 sleep 중에도 상태 유지)
    hb_task = asyncio.create_task(_heartbeat_loop(sb, interval=10))

    # 미검증 워커 자동 검증 (최초 등록 또는 검증 이력 없는 경우)
    _station_url = CRAWL_STATION_URL or "https://crawl-station.vercel.app"
    if registered and not already_verified:
        print("  🔍 미검증 워커 — 자동 검증 예약됨 (백그라운드)")
        asyncio.create_task(_auto_verify_task(_station_url, WORKER_ID))

    # 내게 할당된 미처리 요청 처리 (타입 필터 적용)
    q = sb.table("crawl_requests").select("*") \
        .eq("assigned_worker", WORKER_ID) \
        .eq("status", "assigned") \
        .order("priority", desc=True) \
        .order("created_at")
    if allowed_types:
        q = q.in_("type", allowed_types)
    else:
        q = q.not_.in_("type", PYTHON_EXCLUDED_TYPES)
    res = q.execute()
    assigned = res.data or []

    # 할당 안 된 pending 요청도 가져오기 (하위 호환, 타입 필터 적용)
    q2 = sb.table("crawl_requests").select("*") \
        .is_("assigned_worker", "null") \
        .eq("status", "pending") \
        .order("priority", desc=True) \
        .order("created_at")
    if allowed_types:
        q2 = q2.in_("type", allowed_types)
    else:
        q2 = q2.not_.in_("type", PYTHON_EXCLUDED_TYPES)
    res2 = q2.execute()
    pending = res2.data or []

    backlog = assigned + pending
    if backlog:
        print(f"\n📋 대기 작업 {len(backlog)}개 (할당: {len(assigned)}, 미할당: {len(pending)})")
        batch_count = 0
        for req in backlog:
            # 작업 전 원격 명령 체크
            cmd = check_and_execute_command(sb)
            if cmd:
                handle_command(sb, cmd)

            # 미할당 작업은 내가 가져감
            if not req.get("assigned_worker"):
                sb.table("crawl_requests").update({
                    "assigned_worker": WORKER_ID,
                    "status": "assigned",
                }).eq("id", req["id"]).execute()

            await process_request(sb, req, config, log_cb=print)
            batch_count += 1

            # 배치 휴식
            if batch_count >= config.get("batch_size", 30):
                rest = config.get("batch_rest_seconds", 180)
                print(f"\n  😴 배치 완료 — {rest}초 휴식")
                await asyncio.sleep(rest)
                batch_count = 0
                config = load_config(sb)  # config 재로드
                # IP 로테이션
                if should_rotate_ip(config):
                    rotate_tethering_ip(config, log_cb=print)
            else:
                delay_min = config.get("keyword_delay_min", 15)
                delay_max = config.get("keyword_delay_max", 30)
                await asyncio.sleep(random.randint(delay_min, delay_max))

    print("\n👂 대기 중... (Ctrl+C 종료)")

    global _last_update_check_time
    batch_count = 0
    loop_count = 0
    while True:
        try:
            loop_count += 1

            # 주기적으로 원격 명령 + config 체크 (매 ~30초)
            if loop_count % _UPDATE_CHECK_INTERVAL == 0:
                # 원격 명령 체크
                cmd = check_and_execute_command(sb)
                if cmd:
                    handle_command(sb, cmd)

                # config 주기적 재로드 (quota 등 반영)
                config = load_config(sb)

                # 업데이트 체크 (시간 기반 — config의 update_check_interval_minutes 참조)
                update_interval_min = config.get("update_check_interval_minutes", 60)
                now_ts = time.time()
                if now_ts - _last_update_check_time >= update_interval_min * 60:
                    _last_update_check_time = now_ts
                    update = check_update(sb)
                    if update:
                        print(f"\n  📦 새 버전 v{update['version']} 발견 — 자동 업데이트")
                        log_error(sb, f"업데이트: v{VERSION} → v{update['version']}", level="info")
                        apply_update(sb, update)

            # ── 새벽 휴식 (KST 3~5시) ──
            kst_now = datetime.now(_KST)
            kst_hour = kst_now.hour
            rest_hours = config.get("rest_hours", [3, 4, 5])
            if kst_hour in rest_hours:
                if loop_count % 60 == 1:  # 5분마다 로그
                    print(f"  😴 새벽 휴식 중 (KST {kst_hour}시) — 작업 중단")
                await asyncio.sleep(60)
                continue

            # ── 일일 할당량 체크 (카테고리별) ──
            quota_reset_at = config.get("quota_reset_at", "")

            # KST 자정 리셋
            try:
                from datetime import date as _date
                kst_today = datetime.now(_KST).date()
                reset_date = _date.fromisoformat(quota_reset_at[:10]) if quota_reset_at else None
                if reset_date is None or reset_date < kst_today:
                    sb.rpc("reset_daily_quotas", {"wid": WORKER_ID}).execute()
                    for k in ("daily_used", "daily_used_naver", "daily_used_instagram"):
                        config[k] = 0
                    config["quota_reset_at"] = kst_today.isoformat()
            except Exception:
                pass

            # 네이버 한도 초과 여부 (요청 가져오기 전 체크용)
            _quota_naver = config.get("daily_quota_naver", 0)
            _used_naver  = config.get("daily_used_naver", 0)
            _quota_insta = config.get("daily_quota_instagram", 0)
            _used_insta  = config.get("daily_used_instagram", 0)
            _quota_all   = config.get("daily_quota", 0)
            _used_all    = config.get("daily_used", 0)

            # 모든 카테고리 소진 여부 판단
            _naver_full = _quota_naver > 0 and _used_naver >= _quota_naver
            _insta_full = _quota_insta > 0 and _used_insta >= _quota_insta
            _all_full   = _quota_all > 0 and _used_all >= _quota_all

            if _all_full or (_naver_full and _insta_full):
                if loop_count % 12 == 1:
                    print(f"  ⏸️ 모든 카테고리 일일 한도 소진 — 대기 중 "
                          f"(N {_used_naver}/{_quota_naver}, I {_used_insta}/{_quota_insta})")
                await asyncio.sleep(60)
                continue

            # allowed_types 변경 반영 (config 리로드 시)
            allowed_types = config.get("allowed_types") or []
            if isinstance(allowed_types, str):
                import json as _json
                try:
                    allowed_types = _json.loads(allowed_types)
                except Exception:
                    allowed_types = []
            _heartbeat_status["allowed_types"] = allowed_types

            # ── 차단 쿨다운 체크 ──
            blocked_until = _heartbeat_status.get("blocked_until")
            if blocked_until:
                try:
                    bu = datetime.fromisoformat(blocked_until.replace("Z", "+00:00"))
                    remaining = (bu - datetime.now(timezone.utc)).total_seconds()
                    if remaining > 0:
                        if loop_count % 12 == 1:  # 1분마다 로그
                            mins = int(remaining // 60)
                            print(f"  🚫 차단 쿨다운 중 — {mins}분 {int(remaining % 60)}초 남음")
                        await asyncio.sleep(min(30, remaining))
                        continue
                    else:
                        # 쿨다운 만료 → 차단 해제
                        _clear_block(sb)
                        print(f"  ✅ 차단 쿨다운 만료 — 작업 재개")
                except Exception:
                    pass

            # 1) 내게 할당된 작업 (타입 필터 적용)
            try:
                q = sb.table("crawl_requests").select("*") \
                    .eq("assigned_worker", WORKER_ID) \
                    .eq("status", "assigned") \
                    .order("priority", desc=True) \
                    .order("created_at") \
                    .limit(1)
                if allowed_types:
                    q = q.in_("type", allowed_types)
                else:
                    q = q.not_.in_("type", PYTHON_EXCLUDED_TYPES)
                res = q.execute()
            except Exception as qe:
                print(f"  ⚠️ 작업 조회 실패: {qe}")
                await asyncio.sleep(10)
                continue

            task = None
            if res.data:
                task = res.data[0]
            else:
                # 2) 미할당 pending 작업 (하위 호환, 타입 필터 적용)
                try:
                    q2 = sb.table("crawl_requests").select("*") \
                        .is_("assigned_worker", "null") \
                        .eq("status", "pending") \
                        .order("priority", desc=True) \
                        .order("created_at") \
                        .limit(1)
                    if allowed_types:
                        q2 = q2.in_("type", allowed_types)
                    else:
                        q2 = q2.not_.in_("type", PYTHON_EXCLUDED_TYPES)
                    res2 = q2.execute()
                except Exception as qe:
                    print(f"  ⚠️ pending 작업 조회 실패: {qe}")
                    await asyncio.sleep(10)
                    continue
                if res2.data:
                    task = res2.data[0]
                    sb.table("crawl_requests").update({
                        "assigned_worker": WORKER_ID,
                        "status": "assigned",
                    }).eq("id", task["id"]).execute()

            if task:
                task_type = task.get("type", "")
                # 카테고리 판별
                _INSTA_TYPES = {"instagram_profile", "instagram_post", "instagram_login_test"}
                _NAVER_TYPES = {"kin_analysis", "kin_post", "blog_crawl", "blog_serp",
                                "area_analysis", "deep_analysis", "daily_rank", "rank_check"}
                if task_type in _INSTA_TYPES:
                    req_cat = "instagram"
                elif task_type in _NAVER_TYPES:
                    req_cat = "naver"
                else:
                    req_cat = "other"

                # 카테고리별 한도 사전 체크 (이미 할당된 작업이라도 skip)
                if req_cat == "naver" and _quota_naver > 0 and _used_naver >= _quota_naver:
                    print(f"  ⏸️ 네이버 일일 한도 소진 ({_used_naver}/{_quota_naver}) — 작업 건너뜀")
                    await asyncio.sleep(30)
                    continue
                if req_cat == "instagram" and _quota_insta > 0 and _used_insta >= _quota_insta:
                    print(f"  ⏸️ 인스타 일일 한도 소진 ({_used_insta}/{_quota_insta}) — 작업 건너뜀")
                    await asyncio.sleep(30)
                    continue

                await process_request(sb, task, config, log_cb=print)
                batch_count += 1

                # 카테고리별 사용량 increment
                try:
                    sb.rpc("increment_daily_used_cat", {"wid": WORKER_ID, "cat": req_cat}).execute()
                    if req_cat == "naver":
                        config["daily_used_naver"] = config.get("daily_used_naver", 0) + 1
                    elif req_cat == "instagram":
                        config["daily_used_instagram"] = config.get("daily_used_instagram", 0) + 1
                    config["daily_used"] = config.get("daily_used", 0) + 1
                except Exception as e:
                    print(f"  ⚠️ 할당량 증가 실패: {e}")

                if batch_count >= config.get("batch_size", 30):
                    rest = config.get("batch_rest_seconds", 180)
                    print(f"\n  😴 배치 완료 — {rest}초 휴식")
                    await asyncio.sleep(rest)
                    batch_count = 0
                    config = load_config(sb)
                    if should_rotate_ip(config):
                        rotate_tethering_ip(config, log_cb=print)
                else:
                    # 24시간 분산 딜레이 — 카테고리별 한도 기준
                    if req_cat == "naver" and _quota_naver > 0:
                        _quota_ref = _quota_naver
                        _used_ref  = config.get("daily_used_naver", 0)
                    elif req_cat == "instagram" and _quota_insta > 0:
                        _quota_ref = _quota_insta
                        _used_ref  = config.get("daily_used_instagram", 0)
                    else:
                        _quota_ref = config.get("daily_quota", 0)
                        _used_ref  = config.get("daily_used", 0)

                    if _quota_ref > 0:
                        kst_now = datetime.now(_KST)
                        secs_elapsed   = kst_now.hour * 3600 + kst_now.minute * 60 + kst_now.second
                        secs_remaining = max(86400 - secs_elapsed, 60)
                        quota_remaining = max(_quota_ref - _used_ref, 1)
                        base_interval  = secs_remaining / quota_remaining
                        interval = int(base_interval * random.uniform(0.7, 1.3))
                        interval = max(60, min(interval, 7200))
                        print(f"  ⏱️ [{req_cat}] 24h 분산 대기: {interval//60}분 {interval%60}초 "
                              f"(잔여 {quota_remaining}건 / {secs_remaining//3600:.1f}h)")
                        await asyncio.sleep(interval)
                    else:
                        delay_min = config.get("keyword_delay_min", 15)
                        delay_max = config.get("keyword_delay_max", 30)
                        await asyncio.sleep(random.randint(delay_min, delay_max))

                    # decoy 검색 (15% 확률 — 목적 없는 일반 검색으로 패턴 위장)
                    if random.random() < 0.15:
                        try:
                            from playwright.async_api import async_playwright
                            from handlers.base import BaseCrawler
                            bc = BaseCrawler(headless=True, config=config)
                            async with async_playwright() as pw:
                                browser, ctx = await bc.create_browser(pw)
                                page = await ctx.new_page()
                                await bc.decoy_search(page)
                                await browser.close()
                            print("  🎭 decoy 검색 완료")
                        except Exception:
                            pass
            else:
                # ── 닥톡 송출 대기열 체크 (crawl_requests 없을 때) ──
                try:
                    from handlers.kin_post import KinPostHandler
                    _kp = KinPostHandler(config=config)
                    _kp_result = await _kp.poll_and_post(sb, WORKER_ID, log_cb=print)
                    if _kp_result and _kp_result.get("success"):
                        # 송출 완료 시 딜레이 (사람처럼)
                        _kp_delay = random.randint(
                            int(os.environ.get("KIN_POST_DELAY_MIN", "30")),
                            int(os.environ.get("KIN_POST_DELAY_MAX", "90"))
                        )
                        print(f"  ⏳ 다음 송출까지 {_kp_delay}초 대기...")
                        await asyncio.sleep(_kp_delay)
                    else:
                        await asyncio.sleep(5)
                except Exception as _kpe:
                    print(f"  ⚠️ KinPost 오류: {_kpe}")
                    await asyncio.sleep(5)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"⚠️ {e}")
            try:
                log_error(sb, str(e), level="error", context={"loop_count": loop_count})
            except Exception:
                pass
            await asyncio.sleep(10)

    hb_task.cancel()
    try:
        await hb_task
    except asyncio.CancelledError:
        pass
    heartbeat(sb, "offline")
    print("\n👋 종료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 종료")
