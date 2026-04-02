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
from datetime import datetime, timezone

# ── 버전 ──────────────────────────────────────
VERSION = "0.7.0"
WORKER_DIR = os.path.dirname(os.path.abspath(__file__))

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

# 업데이트 체크 간격 (heartbeat 횟수 기준)
_UPDATE_CHECK_INTERVAL = 6  # 5초 × 6 = 30초마다 업데이트/명령 체크


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
    """워커를 CrawlStation에 자동 등록 (UPSERT)"""
    info = collect_machine_info()
    now = datetime.now(timezone.utc).isoformat()

    try:
        sb.table("workers").upsert({
            "id": WORKER_ID,
            "name": info["hostname"],
            "os": info["os"],
            "hostname": info["hostname"],
            "python_version": info["python_version"],
            "version": VERSION,
            "status": "online",
            "last_seen": now,
            "registered_at": now,
            "registered_by": "auto",
            "command": None,
        }, on_conflict="id").execute()
        return True
    except Exception as e:
        print(f"  ⚠️ 워커 등록 실패: {e}")
        return False


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
    except Exception:
        pass

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
    """최신 버전으로 업데이트 — 파일 다운로드 후 자동 재실행"""
    new_version = release["version"]
    files = release.get("files") or {}
    print(f"\n🔄 업데이트 v{VERSION} → v{new_version}")
    print(f"   {release.get('changelog', '')}")

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

    # 버전 보고
    sb.table("workers").update({
        "version": new_version,
    }).eq("id", WORKER_ID).execute()

    print(f"   ✅ {updated}개 파일 업데이트 완료")
    return True


def restart_worker():
    """워커 프로세스 종료 → LaunchAgent/서비스가 자동 재시작"""
    print("\n🔄 워커 재시작 중...")

    # 먼저 새 코드가 import 가능한지 검증
    python = sys.executable
    worker_script = os.path.join(WORKER_DIR, "worker.py")
    try:
        r = subprocess.run(
            [python, "-c", f"import sys; sys.path.insert(0, '{WORKER_DIR}'); from handlers import HANDLERS; print('OK', len(HANDLERS))"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            print(f"   ⚠️ 새 코드 import 실패 — 재시작 취소")
            print(f"   {r.stderr[:300]}")
            return
        print(f"   ✅ import 검증 통과: {r.stdout.strip()}")
    except Exception as e:
        print(f"   ⚠️ 검증 실패: {e}")
        return

    # 프로세스 종료 (exit code 0이 아닌 값 → LaunchAgent/서비스가 재시작)
    print("   프로세스 종료 → 서비스 매니저가 재시작합니다")
    sys.exit(42)  # 비정상 종료 코드 → KeepAlive 트리거


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
                restart_worker()
            else:
                print("   ⚠️ 업데이트 적용 실패")
        else:
            print("   ✅ 이미 최신 버전입니다")

    return False


# ── Heartbeat ─────────────────────────────────
def heartbeat(sb, status="idle", keyword=None, ctype=None):
    try:
        sb.table("workers").update({
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "current_keyword": keyword,
            "current_type": ctype,
        }).eq("id", WORKER_ID).execute()
    except Exception:
        pass


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
        results = await handler.handle(keyword, options, log_cb=log_cb)

        _meta["response_time_ms"] = int((time.time() - _t0) * 1000)
        _meta["result_count"] = len(results) if results else 0
        _meta["empty_result"] = _meta["result_count"] == 0

        # 차단/캡챠 감지 (결과에서)
        if results:
            for item in results:
                item_str = str(item).lower()
                if "captcha" in item_str or "보안문자" in item_str:
                    _meta["captcha"] = True
                    _meta["blocked"] = True
                if "차단" in item_str or "blocked" in item_str:
                    _meta["blocked"] = True

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
        if "captcha" in err_str or "보안문자" in err_str:
            _meta["captcha"] = True
            _meta["blocked"] = True
        if "timeout" in err_str or "navigation" in err_str:
            _meta["blocked"] = True

        if log_cb: log_cb(f"  ❌ 오류: {e}")
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

    from supabase import create_client
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # CrawlStation에 자동 등록
    info = collect_machine_info()
    print("=" * 50)
    print(f"  CrawlStation 크롤링 워커 v{VERSION}")
    print(f"  ID: {WORKER_ID}")
    print(f"  OS: {info['os']}")
    print(f"  호스트: {info['hostname']}")
    print(f"  지원: {', '.join(HANDLERS.keys())}")
    print("=" * 50)

    if register_worker(sb):
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

    # 시작 시 자동 업데이트 (묻지 않음)
    update = check_update(sb)
    if update:
        print(f"\n  📦 새 버전 발견: v{update['version']} (현재: v{VERSION})")
        print(f"     {update.get('changelog', '')}")
        if apply_update(sb, update):
            restart_worker()  # 자동 재시작
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

    heartbeat(sb, "idle")

    # 내게 할당된 미처리 요청 처리
    res = sb.table("crawl_requests").select("*") \
        .eq("assigned_worker", WORKER_ID) \
        .eq("status", "assigned") \
        .order("priority", desc=True) \
        .order("created_at") \
        .execute()
    assigned = res.data or []

    # 할당 안 된 pending 요청도 가져오기 (하위 호환)
    res2 = sb.table("crawl_requests").select("*") \
        .is_("assigned_worker", "null") \
        .eq("status", "pending") \
        .order("priority", desc=True) \
        .order("created_at") \
        .execute()
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

    batch_count = 0
    loop_count = 0
    while True:
        try:
            heartbeat(sb, "idle")
            loop_count += 1

            # 주기적으로 원격 명령 + 업데이트 체크 (매 ~30초)
            if loop_count % _UPDATE_CHECK_INTERVAL == 0:
                # 원격 명령 체크
                cmd = check_and_execute_command(sb)
                if cmd:
                    handle_command(sb, cmd)

                # 업데이트 체크
                update = check_update(sb)
                if update:
                    print(f"\n  📦 새 버전 v{update['version']} 발견 — 자동 업데이트")
                    if apply_update(sb, update):
                        restart_worker()

                # config 주기적 재로드 (quota 등 반영)
                config = load_config(sb)

            # ── 새벽 휴식 (KST 3~5시) ──
            kst_now = datetime.now(timezone.utc).astimezone()
            kst_hour = (kst_now.hour + 9) % 24  # UTC → KST 간이 변환
            rest_hours = config.get("rest_hours", [3, 4, 5])
            if kst_hour in rest_hours:
                if loop_count % 60 == 1:  # 5분마다 로그
                    print(f"  😴 새벽 휴식 중 (KST {kst_hour}시) — 작업 중단")
                await asyncio.sleep(60)
                continue

            # ── 일일 할당량 체크 ──
            daily_quota = config.get("daily_quota", 500)
            daily_used = config.get("daily_used", 0)
            quota_reset_at = config.get("quota_reset_at", "")

            # KST 자정 리셋 체크
            if quota_reset_at:
                try:
                    from datetime import date as _date
                    reset_date = _date.fromisoformat(quota_reset_at[:10])
                    # KST = UTC+9
                    kst_now = datetime.now(timezone.utc).astimezone()
                    kst_today = kst_now.date()
                    if reset_date < kst_today:
                        sb.rpc("reset_daily_quota_if_needed", {"wid": WORKER_ID}).execute()
                        daily_used = 0
                        config["daily_used"] = 0
                except Exception:
                    pass

            if daily_used >= daily_quota:
                if loop_count % 12 == 1:  # 1분마다 로그
                    print(f"  ⏸️ 일일 할당량 소진 ({daily_used}/{daily_quota}) — 대기 중")
                await asyncio.sleep(60)
                continue

            # 1) 내게 할당된 작업
            res = sb.table("crawl_requests").select("*") \
                .eq("assigned_worker", WORKER_ID) \
                .eq("status", "assigned") \
                .order("priority", desc=True) \
                .order("created_at") \
                .limit(1).execute()

            task = None
            if res.data:
                task = res.data[0]
            else:
                # 2) 미할당 pending 작업 (하위 호환)
                res2 = sb.table("crawl_requests").select("*") \
                    .is_("assigned_worker", "null") \
                    .eq("status", "pending") \
                    .order("priority", desc=True) \
                    .order("created_at") \
                    .limit(1).execute()
                if res2.data:
                    task = res2.data[0]
                    sb.table("crawl_requests").update({
                        "assigned_worker": WORKER_ID,
                        "status": "assigned",
                    }).eq("id", task["id"]).execute()

            if task:
                await process_request(sb, task, config, log_cb=print)
                batch_count += 1

                # 일일 사용량 increment (atomic)
                try:
                    sb.rpc("increment_daily_used", {"wid": WORKER_ID}).execute()
                    config["daily_used"] = config.get("daily_used", 0) + 1
                except Exception:
                    pass

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
                await asyncio.sleep(5)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"⚠️ {e}")
            await asyncio.sleep(10)

    heartbeat(sb, "offline")
    print("\n👋 종료")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 종료")
