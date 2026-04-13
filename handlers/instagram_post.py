"""인스타그램 게시물 성과 수집 핸들러
업로드된 게시물(릴스/이미지)의 좋아요/댓글/조회수 수집

crawl_requests 형식:
  type: "instagram_post"
  keyword: shortcode (예: "DH8IVpUy66t")
  options: {
    shortcode: str,
    url: str,           # 원본 게시물 URL
    seeding_id: str,    # lnb.seedings ID
    source: "insta-desk-metrics"
  }

결과 (crawl_results.data):
  { shortcode, url, seeding_id, likes, comments, views, impressions }
"""
import re
import json
import random
import math
import os
import urllib.request
from .base import BaseCrawler


def _nd(mu: float, sigma: float, lo: float, hi: float) -> float:
    """정규분포 기반 랜덤 지연 (클램핑 포함)"""
    v = random.gauss(mu, sigma)
    return max(lo, min(hi, v))

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


class InstagramPostHandler(BaseCrawler):

    INSTA_UA = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    ]

    async def handle(self, keyword, options, log_cb=None):
        from playwright.async_api import async_playwright
        import asyncio

        shortcode = options.get("shortcode") or keyword.strip()
        url = options.get("url") or f"https://www.instagram.com/reel/{shortcode}/"
        seeding_id = options.get("seeding_id", "")

        if log_cb:
            log_cb(f"  [instagram_post] shortcode={shortcode} seeding_id={seeding_id[:8] if seeding_id else '?'}")

        # 로그인 계정 발급
        worker_id = options.get("worker_id", os.environ.get("WORKER_ID", ""))
        account = self._pick_account(worker_id, log_cb)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)

            ctx_kwargs = dict(
                user_agent=random.choice(self.INSTA_UA),
                viewport={"width": 1440, "height": 900},
                locale="ko-KR",
            )
            if account and account.get("session_state"):
                ctx_kwargs["storage_state"] = account["session_state"]

            ctx = await browser.new_context(**ctx_kwargs)

            if account:
                logged_in = await self._ensure_login(ctx, account, log_cb)
                if not logged_in:
                    self._report_block(account["id"], log_cb)
                    account = None

            result = await self._fetch_post_metrics(ctx, shortcode, url, log_cb)

            if account:
                try:
                    state = await ctx.storage_state()
                    self._save_session(account["id"], state, log_cb)
                except Exception:
                    pass

            await browser.close()

        if result is None:
            if log_cb:
                log_cb(f"  [instagram_post] 수집 실패 — shortcode={shortcode}")
            return []

        result["seeding_id"] = seeding_id
        result["shortcode"] = shortcode
        result["url"] = url

        if log_cb:
            log_cb(f"  [instagram_post] likes={result.get('likes')} comments={result.get('comments')} views={result.get('views')}")

        return [result]

    async def _fetch_post_metrics(self, ctx, shortcode, url, log_cb=None):
        """게시물 페이지에서 성과 수집 — API 인터셉트 우선, DOM fallback"""
        page = await ctx.new_page()
        api_data = {}

        async def _on_response(response):
            try:
                if response.status != 200:
                    return
                resp_url = response.url
                # GraphQL / API 응답 인터셉트
                if any(kw in resp_url for kw in ["graphql", "api/v1/media", "web/get_ruling"]):
                    body = await response.json()
                    # media 객체 탐색
                    media = (
                        body.get("graphql", {}).get("shortcode_media")
                        or body.get("data", {}).get("shortcode_media")
                        or body.get("items", [{}])[0] if body.get("items") else None
                    )
                    if media:
                        api_data["media"] = media
            except Exception:
                pass

        page.on("response", _on_response)

        try:
            # 홈 피드 경유 — 직접 게시물 URL 접근 시 봇 감지 위험 완화
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(int(_nd(2200, 400, 1200, 3500)))

            # 로그인 게이트 빠른 체크
            if "accounts/login" in page.url:
                if log_cb:
                    log_cb(f"  [instagram_post] 홈 접속 시 로그인 필요")
                await page.close()
                return None

            # 게시물 URL 결정 (reel / p 둘 다 시도)
            post_url = url if url else f"https://www.instagram.com/reel/{shortcode}/"
            await page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(int(_nd(2800, 500, 1800, 4500)))

            # 로그인 게이트 감지
            current_url = page.url
            if "accounts/login" in current_url:
                if log_cb:
                    log_cb(f"  [instagram_post] 로그인 필요")
                await page.close()
                return None

            # DOM에서 성과 수집
            metrics = await page.evaluate("""() => {
                var r = { likes: null, comments: null, views: null };

                // 좋아요 수 — "좋아요 N개" 또는 "N likes"
                var allText = document.body.innerText || '';

                // 좋아요 패턴 (한국어)
                var likeMatch = allText.match(/좋아요\\s+([\\d,]+)\\s*개/);
                if (likeMatch) r.likes = likeMatch[1].replace(/,/g, '');

                // 좋아요 패턴 (영어)
                if (!r.likes) {
                    var likeEn = allText.match(/([\\d,]+)\\s+likes?/i);
                    if (likeEn) r.likes = likeEn[1].replace(/,/g, '');
                }

                // 댓글 수 — "댓글 N개 모두 보기" 또는 section 내 "N comments"
                var commentMatch = allText.match(/댓글\\s+([\\d,]+)\\s*개/);
                if (commentMatch) r.comments = commentMatch[1].replace(/,/g, '');
                if (!r.comments) {
                    var cmEn = allText.match(/([\\d,]+)\\s+comments?/i);
                    if (cmEn) r.comments = cmEn[1].replace(/,/g, '');
                }

                // 조회수 — "조회 N회" 또는 "N views"
                var viewMatch = allText.match(/조회\\s+([\\d,]+)\\s*회/);
                if (viewMatch) r.views = viewMatch[1].replace(/,/g, '');
                if (!r.views) {
                    var vwEn = allText.match(/([\\d,]+)\\s+views?/i);
                    if (vwEn) r.views = vwEn[1].replace(/,/g, '');
                }

                // aria-label 속성에서 추출 (접근성 텍스트)
                var ariaEls = document.querySelectorAll('[aria-label]');
                ariaEls.forEach(function(el) {
                    var label = el.getAttribute('aria-label') || '';
                    if (!r.likes) {
                        var m = label.match(/([\\d,]+)\\s*(좋아요|likes?)/i);
                        if (m) r.likes = m[1].replace(/,/g, '');
                    }
                    if (!r.views) {
                        var m2 = label.match(/([\\d,]+)\\s*(조회|views?)/i);
                        if (m2) r.views = m2[1].replace(/,/g, '');
                    }
                });

                return r;
            }""")

            result = {
                "likes": int(metrics["likes"]) if metrics.get("likes") else None,
                "comments": int(metrics["comments"]) if metrics.get("comments") else None,
                "views": int(metrics["views"]) if metrics.get("views") else None,
                "impressions": None,
            }

            # API 인터셉트 데이터로 덮어쓰기 (더 정확)
            media = api_data.get("media", {})
            if media:
                likes = (
                    media.get("edge_media_preview_like", {}).get("count")
                    or media.get("like_count")
                )
                comments = (
                    media.get("edge_media_to_parent_comment", {}).get("count")
                    or media.get("comment_count")
                )
                views = (
                    media.get("video_view_count")
                    or media.get("play_count")
                    or media.get("view_count")
                )
                if likes is not None:
                    result["likes"] = likes
                if comments is not None:
                    result["comments"] = comments
                if views is not None:
                    result["views"] = views

            await page.close()
            return result

        except Exception as e:
            if log_cb:
                log_cb(f"  [instagram_post] 파싱 오류: {str(e)[:80]}")
            await page.close()
            return None

    # ── 계정 관리 (instagram.py와 동일 구조) ─────────────────────

    def _sb_headers(self):
        return {
            "apikey": _SUPABASE_KEY,
            "Authorization": f"Bearer {_SUPABASE_KEY}",
            "Content-Type": "application/json",
        }

    def _pick_account(self, worker_id: str, log_cb=None) -> dict | None:
        if not _SUPABASE_URL:
            return None
        import ssl
        _ssl_ctx = ssl.create_default_context()
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
        try:
            params = (
                f"is_active=eq.true&status=eq.active"
                f"&or=(assigned_worker_id.eq.{worker_id},assigned_worker_id.is.null)"
                f"&order=last_used_at.asc.nullsfirst&limit=1"
                f"&select=id,username,password,session_state"
            )
            req = urllib.request.Request(
                f"{_SUPABASE_URL}/rest/v1/instagram_accounts?{params}",
                headers={**self._sb_headers(), "Prefer": "return=representation"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
                rows = json.loads(resp.read())
                if not rows:
                    return None
                acc = rows[0]
            now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
            patch_req = urllib.request.Request(
                f"{_SUPABASE_URL}/rest/v1/instagram_accounts?id=eq.{acc['id']}",
                data=json.dumps({"last_used_at": now}).encode(),
                headers={**self._sb_headers(), "Prefer": "return=minimal"},
                method="PATCH",
            )
            urllib.request.urlopen(patch_req, timeout=5, context=_ssl_ctx)
            if log_cb:
                log_cb(f"  [instagram_post] 계정 @{acc['username']} 사용")
            return acc
        except Exception as e:
            if log_cb:
                log_cb(f"  [instagram_post] 계정 발급 실패 ({e}) — 익명으로 진행")
            return None

    def _report_block(self, account_id: str, log_cb=None):
        if not _SUPABASE_URL or not account_id:
            return
        import ssl
        _ssl_ctx = ssl.create_default_context()
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
        try:
            from datetime import datetime, timedelta
            blocked_until = (datetime.utcnow() + timedelta(minutes=120)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
            body = json.dumps({
                "status": "cooling",
                "last_blocked_at": now,
                "blocked_until": blocked_until,
                "session_state": None,
            }).encode()
            req = urllib.request.Request(
                f"{_SUPABASE_URL}/rest/v1/instagram_accounts?id=eq.{account_id}",
                data=body,
                headers={**self._sb_headers(), "Prefer": "return=minimal"},
                method="PATCH",
            )
            urllib.request.urlopen(req, timeout=5, context=_ssl_ctx)
        except Exception:
            pass

    def _save_session(self, account_id: str, state: dict, log_cb=None):
        if not _SUPABASE_URL or not account_id:
            return
        import ssl
        _ssl_ctx = ssl.create_default_context()
        _ssl_ctx.check_hostname = False
        _ssl_ctx.verify_mode = ssl.CERT_NONE
        try:
            now = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
            body = json.dumps({"session_state": state, "last_login_at": now}).encode()
            req = urllib.request.Request(
                f"{_SUPABASE_URL}/rest/v1/instagram_accounts?id=eq.{account_id}",
                data=body,
                headers={**self._sb_headers(), "Prefer": "return=minimal"},
                method="PATCH",
            )
            urllib.request.urlopen(req, timeout=10, context=_ssl_ctx)
        except Exception:
            pass

    async def _ensure_login(self, ctx, account: dict, log_cb=None) -> bool:
        page = await ctx.new_page()
        try:
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
            current_url = page.url
            is_login_page = "accounts/login" in current_url or await page.query_selector('input[name="username"]') is not None
            if not is_login_page:
                await page.close()
                return True
            if not account.get("password"):
                await page.close()
                return False
            await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(int(_nd(1500, 300, 900, 2500)))
            await page.fill('input[name="username"]', account["username"])
            await page.wait_for_timeout(int(_nd(700, 150, 400, 1200)))
            await page.fill('input[name="password"]', account["password"])
            await page.wait_for_timeout(int(_nd(700, 150, 400, 1200)))
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(int(_nd(4500, 600, 3000, 6000)))
            final_url = page.url
            if "accounts/login" in final_url or "challenge" in final_url:
                await page.close()
                return False
            await page.close()
            return True
        except Exception as e:
            if log_cb:
                log_cb(f"  [instagram_post] 로그인 오류: {str(e)[:80]}")
            await page.close()
            return False
