"""
lifenbio_auth.py — LifeNBio SSO Python SDK
모든 Python 스크립트/CLI에서 import해서 사용

사용법:
    from lifenbio_auth import get_headers, log_activity

    # API 요청 시 헤더 자동 첨부
    resp = requests.get(url, headers=get_headers())

    # 사용 기록 남기기
    log_activity("crawl.job_start", {"keyword": kw})
"""

import os
import json
import requests
from pathlib import Path
from datetime import datetime, timezone

SSO_BASE   = os.environ.get("LIFENBIO_SSO_URL", "https://sso.lifenbio.com")
TOKEN_FILE = Path.home() / ".lifenbio" / "token.json"


def login(email: str, password: str, app_id: str = "cli") -> dict:
    """로그인 후 토큰을 파일에 저장. 이후 get_headers()로 사용."""
    res = requests.post(
        f"{SSO_BASE}/auth/login",
        json={"email": email, "password": password, "app_id": app_id},
        timeout=10,
    )
    res.raise_for_status()
    data = res.json()
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data))
    TOKEN_FILE.chmod(0o600)
    print(f"로그인 성공: {email}")
    return data


def _load_token() -> dict:
    if not TOKEN_FILE.exists():
        raise RuntimeError(
            "로그인이 필요합니다.\n"
            "  from lifenbio_auth import login\n"
            "  login('your@email.com', 'password')"
        )
    return json.loads(TOKEN_FILE.read_text())


def _refresh() -> dict:
    token = _load_token()
    res = requests.post(
        f"{SSO_BASE}/auth/refresh",
        json={"refresh_token": token["refresh_token"]},
        timeout=10,
    )
    if res.status_code == 401:
        TOKEN_FILE.unlink(missing_ok=True)
        raise RuntimeError("세션이 만료되었습니다. 다시 로그인하세요.")
    res.raise_for_status()
    data = res.json()
    TOKEN_FILE.write_text(json.dumps({**token, **data}))
    return data


def get_headers() -> dict:
    """Authorization 헤더 반환. Access Token 만료 시 자동 갱신."""
    token = _load_token()
    # 간단한 만료 체크 (jose 없이 base64로 payload 파싱)
    try:
        import base64
        payload_b64 = token["access_token"].split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp", 0)
        now = datetime.now(timezone.utc).timestamp()
        if exp - now < 60:  # 만료 1분 전이면 갱신
            token = _refresh()
    except Exception:
        pass  # 파싱 실패 시 그냥 기존 토큰 사용
    return {"Authorization": f"Bearer {token['access_token']}"}


def log_activity(action: str, metadata: dict = {}) -> None:
    """사용 기록을 SSO 서버에 전송. 실패해도 메인 로직에 영향 없음."""
    try:
        requests.post(
            f"{SSO_BASE}/activity/log",
            headers=get_headers(),
            json={"action": action, "metadata": metadata},
            timeout=5,
        )
    except Exception:
        pass  # 기록 실패는 무시


def logout() -> None:
    """로그아웃 및 로컬 토큰 삭제."""
    try:
        requests.post(f"{SSO_BASE}/auth/logout", headers=get_headers(), timeout=5)
    except Exception:
        pass
    TOKEN_FILE.unlink(missing_ok=True)
    print("로그아웃 완료")
