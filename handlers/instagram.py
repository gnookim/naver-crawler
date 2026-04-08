"""인스타그램 프로필 핸들러 — 공개 프로필에서 기본 정보 수집
로그인 계정 풀을 사용하여 차단 대응 — 계정 회전 지원
"""
import re
import json
import random
import os
import urllib.request
from .base import BaseCrawler

CRAWL_STATION_URL = os.environ.get("CRAWL_STATION_URL", "")
CRAWL_STATION_KEY = os.environ.get("CRAWL_STATION_KEY", "")


def _parse_num(s):
    """숫자 문자열 파싱 — M/K/B 단위 변환"""
    s = str(s).replace(",", "").replace("명", "").replace("개", "").strip()
    if s.upper().endswith("M"):
        return int(float(s[:-1]) * 1_000_000)
    if s.upper().endswith("K"):
        return int(float(s[:-1]) * 1_000)
    if s.upper().endswith("B"):
        return int(float(s[:-1]) * 1_000_000_000)
    try:
        return int(float(s))
    except Exception:
        return 0


class InstagramProfileHandler(BaseCrawler):

    INSTA_UA = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    ]

    async def handle(self, keyword, options, log_cb=None):
        from playwright.async_api import async_playwright
        import asyncio

        usernames = options.get("usernames") or [u.strip() for u in keyword.split(",") if u.strip()]
        fetch_reels = options.get("fetchReelsCount", True)
        worker_id = options.get("worker_id", os.environ.get("WORKER_ID", ""))
        results = []

        # Station에서 Instagram 계정 발급 시도
        account = self._pick_account(worker_id, log_cb)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)

            # 계정이 있으면 저장된 세션(storageState) 복원, 없으면 익명
            ctx_kwargs = dict(
                user_agent=random.choice(self.INSTA_UA),
                viewport={"width": 1440, "height": 900},
                locale="ko-KR",
            )
            if account and account.get("session_state"):
                ctx_kwargs["storage_state"] = account["session_state"]

            ctx = await browser.new_context(**ctx_kwargs)

            # 계정 있으면 로그인 상태 확인 + 필요 시 재로그인
            if account:
                logged_in = await self._ensure_login(ctx, account, log_cb)
                if not logged_in:
                    # 로그인 실패 → 차단 보고 후 익명으로 진행
                    self._report_block(account["id"], log_cb)
                    account = None

            for i, username in enumerate(usernames):
                if log_cb:
                    log_cb(f"  [{i+1}/{len(usernames)}] @{username}")

                profile = await self._fetch_profile(ctx, username, fetch_reels, log_cb)

                # 로그인 페이지로 리다이렉트 됐다면 차단
                if profile is None and account:
                    if log_cb:
                        log_cb(f"     ⚠️ 계정 차단 감지 — Station에 보고")
                    self._report_block(account["id"], log_cb)
                    account = None  # 이후는 익명으로 진행

                if profile:
                    results.append(profile)

                if i < len(usernames) - 1:
                    await asyncio.sleep(random.uniform(2, 5))

            # 세션 저장 (로그인 계정 사용 시)
            if account:
                try:
                    state = await ctx.storage_state()
                    self._save_session(account["id"], state, log_cb)
                except Exception:
                    pass

            await browser.close()

        if log_cb:
            log_cb(f"     {len(results)}/{len(usernames)}개 수집")
        return results

    def _pick_account(self, worker_id: str, log_cb=None) -> dict | None:
        """Station에서 사용 가능한 Instagram 계정 1개 발급"""
        if not CRAWL_STATION_URL:
            return None
        try:
            import json as _json
            data = _json.dumps({"worker_id": worker_id}).encode()
            req = urllib.request.Request(
                f"{CRAWL_STATION_URL}/api/instagram-accounts?action=pick",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = _json.loads(resp.read())
                acc = body.get("account")
                if acc and log_cb:
                    log_cb(f"  [Instagram] 계정 @{acc['username']} 사용")
                return acc
        except Exception as e:
            if log_cb:
                log_cb(f"  [Instagram] 계정 발급 실패 ({e}) — 익명으로 진행")
            return None

    def _report_block(self, account_id: str, log_cb=None):
        """Station에 계정 차단 보고"""
        if not CRAWL_STATION_URL or not account_id:
            return
        try:
            import json as _json
            data = _json.dumps({"account_id": account_id, "cooldown_minutes": 120}).encode()
            req = urllib.request.Request(
                f"{CRAWL_STATION_URL}/api/instagram-accounts?action=block",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    def _save_session(self, account_id: str, state: dict, log_cb=None):
        """Station에 storageState 저장"""
        if not CRAWL_STATION_URL or not account_id:
            return
        try:
            import json as _json
            data = _json.dumps({"account_id": account_id, "session_state": state}).encode()
            req = urllib.request.Request(
                f"{CRAWL_STATION_URL}/api/instagram-accounts?action=session",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            if log_cb:
                log_cb(f"  [Instagram] 세션 저장 완료")
        except Exception:
            pass

    async def _ensure_login(self, ctx, account: dict, log_cb=None) -> bool:
        """인스타그램 로그인 상태 확인, 필요 시 재로그인. True=성공, False=실패"""
        page = await ctx.new_page()
        try:
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)

            # 로그인 페이지로 리다이렉트 됐으면 미로그인
            current_url = page.url
            is_login_page = "accounts/login" in current_url or await page.query_selector('input[name="username"]') is not None

            if not is_login_page:
                # 이미 로그인 상태
                await page.close()
                return True

            if not account.get("password"):
                await page.close()
                return False

            if log_cb:
                log_cb(f"  [Instagram] @{account['username']} 로그인 중...")

            # 로그인 폼 입력
            await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(random.randint(1000, 2000))
            await page.fill('input[name="username"]', account["username"])
            await page.wait_for_timeout(random.randint(500, 1000))
            await page.fill('input[name="password"]', account["password"])
            await page.wait_for_timeout(random.randint(500, 1000))
            await page.click('button[type="submit"]')
            await page.wait_for_timeout(4000)

            # 로그인 성공 여부 확인
            final_url = page.url
            if "accounts/login" in final_url or "challenge" in final_url:
                if log_cb:
                    log_cb(f"  [Instagram] 로그인 실패 — {final_url}")
                await page.close()
                return False

            if log_cb:
                log_cb(f"  [Instagram] 로그인 성공")
            await page.close()
            return True

        except Exception as e:
            if log_cb:
                log_cb(f"  [Instagram] 로그인 오류: {str(e)[:80]}")
            await page.close()
            return False

    async def _fetch_profile(self, ctx, username, fetch_reels, log_cb=None):
        page = await ctx.new_page()
        api_data = {}  # web_profile_info 응답 캐시

        # 프로필 로드 시 Instagram 내부 API 응답 가로채기
        async def _on_response(response):
            try:
                if "web_profile_info" in response.url and response.status == 200:
                    body = await response.json()
                    user = body.get("data", {}).get("user", {})
                    if user:
                        api_data["user"] = user
            except Exception:
                pass

        page.on("response", _on_response)

        try:
            profile = await self._try_page_parse(page, username, log_cb, api_data)
            if profile and fetch_reels:
                # API 응답에서 릴스 수 추출 (가장 정확)
                user = api_data.get("user", {})
                reels_count = (
                    user.get("edge_felix_video_timeline", {}).get("count")
                    or user.get("clips_count")
                    or 0
                )
                if reels_count == 0:
                    # fallback: 릴스 탭 스크롤 카운트
                    reels_count = await self._count_reels_scroll(ctx, username, log_cb)
                profile["reels_count"] = reels_count
                if log_cb:
                    log_cb(f"     릴스 {reels_count}개")
            await page.close()
            return profile
        except Exception as e:
            if log_cb:
                log_cb(f"     ⚠️ @{username} 실패: {str(e)[:80]}")
            await page.close()
            return None

    async def _try_page_parse(self, page, username, log_cb=None, api_data=None):
        """프로필 페이지 파싱 — og:description + DOM fallback"""
        try:
            await page.goto(
                f"https://www.instagram.com/{username}/",
                wait_until="domcontentloaded",
                timeout=20000,
            )
            await page.wait_for_timeout(random.randint(2000, 4000))

            # 메타태그 + DOM 숫자 동시 수집
            meta = await page.evaluate("""() => {
                var r = {};
                var d = document.querySelector('meta[property="og:description"]');
                if (d) r.desc = d.content || '';
                var t = document.querySelector('meta[property="og:title"]');
                if (t) r.title = t.content || '';
                var i = document.querySelector('meta[property="og:image"]');
                if (i) r.img = i.content || '';

                // DOM에서 프로필 통계 직접 파싱
                // 인스타그램 프로필 헤더 내 ul > li 구조
                var stats = [];
                var listItems = document.querySelectorAll('header section ul li');
                listItems.forEach(function(li) {
                    var spans = li.querySelectorAll('span');
                    var nums = [];
                    spans.forEach(function(sp) {
                        var txt = (sp.title || sp.innerText || '').trim();
                        if (txt) nums.push(txt);
                    });
                    stats.push(nums.join('|'));
                });
                r.stats = stats;

                // header 영역 전체 텍스트도 백업
                var header = document.querySelector('header');
                r.header_text = header ? header.innerText : '';

                return r;
            }""")

            desc = meta.get("desc", "")
            title = meta.get("title", "")
            stats = meta.get("stats", [])
            header_text = meta.get("header_text", "")

            profile = {
                "username": username,
                "follower_count": 0,
                "following_count": 0,
                "post_count": 0,
                "full_name": "",
                "profile_url": meta.get("img", ""),
                "bio": "",
                "instagram_pk": 0,
                "is_verified": False,
                "is_private": False,
            }

            # 1) og:description 파싱
            if desc:
                # 영어: "701M Followers, 234 Following, 8,390 Posts"
                for m in re.finditer(r'([\d,.]+[MKBmkb]?)\s+(Follower|Following|Post)', desc, re.I):
                    count = _parse_num(m.group(1))
                    label = m.group(2).lower()
                    if label.startswith("follower"):
                        profile["follower_count"] = count
                    elif label.startswith("following"):
                        profile["following_count"] = count
                    elif label.startswith("post"):
                        profile["post_count"] = count

                # 한국어: "팔로워 701M명, 팔로잉 234명, 게시물 8,390개"
                for m in re.finditer(r'(팔로워|팔로잉|게시물)\s+([\d,.]+[MKBmkb]?)', desc, re.I):
                    label, raw = m.group(1), m.group(2)
                    count = _parse_num(raw)
                    if "팔로워" in label:
                        profile["follower_count"] = count
                    elif "팔로잉" in label:
                        profile["following_count"] = count
                    elif "게시물" in label:
                        profile["post_count"] = count

            # 2) DOM stats fallback — og:description가 비어있거나 일부 0인 경우
            # stats 배열: [게시물|숫자, 팔로워|숫자, 팔로잉|숫자] 순서
            if stats and (profile["follower_count"] == 0 or profile["post_count"] == 0):
                for stat in stats:
                    parts = [p.strip() for p in stat.split("|") if p.strip()]
                    # span.title에 숫자가 있는 경우 (쉼표 포함 정확한 값)
                    nums = [p for p in parts if re.match(r'^[\d,]+[MKB]?$', p.replace(',', '').replace('.', ''))]
                    labels = [p for p in parts if not re.match(r'^[\d,]+[MKB]?$', p.replace(',', '').replace('.', ''))]
                    if not nums:
                        continue
                    num = _parse_num(nums[0])
                    label_str = " ".join(labels).lower()
                    if "팔로워" in label_str or "follower" in label_str:
                        if profile["follower_count"] == 0:
                            profile["follower_count"] = num
                    elif "팔로잉" in label_str or "following" in label_str:
                        if profile["following_count"] == 0:
                            profile["following_count"] = num
                    elif "게시물" in label_str or "post" in label_str:
                        if profile["post_count"] == 0:
                            profile["post_count"] = num

            # 3) header 텍스트 fallback (위에서도 못 찾은 경우)
            if header_text and profile["post_count"] == 0:
                # "1,234 게시물" 또는 "1,234 posts" 패턴
                for m in re.finditer(r'([\d,.]+[MKB]?)\s*(게시물|posts?)', header_text, re.I):
                    profile["post_count"] = _parse_num(m.group(1))
                for m in re.finditer(r'([\d,.]+[MKB]?)\s*(팔로워|followers?)', header_text, re.I):
                    if profile["follower_count"] == 0:
                        profile["follower_count"] = _parse_num(m.group(1))
                for m in re.finditer(r'([\d,.]+[MKB]?)\s*(팔로잉|following)', header_text, re.I):
                    if profile["following_count"] == 0:
                        profile["following_count"] = _parse_num(m.group(1))

            # 이름 파싱: "Nike • Instagram (@nike)" 또는 "Nike (@nike)"
            if title:
                name_match = re.match(r'^(.+?)\s*[•·@(]', title)
                if name_match:
                    profile["full_name"] = name_match.group(1).strip()

            # bio — header 텍스트에서 이름/통계 제거 후 추출 (간략히)
            # (인스타는 bio를 메타에 포함하지 않아 정확한 추출 어려움)

            # API 응답 데이터로 덮어쓰기 (더 정확)
            if api_data:
                user = api_data.get("user", {})
                if user:
                    if user.get("edge_followed_by", {}).get("count"):
                        profile["follower_count"] = user["edge_followed_by"]["count"]
                    if user.get("edge_follow", {}).get("count"):
                        profile["following_count"] = user["edge_follow"]["count"]
                    if user.get("edge_owner_to_timeline_media", {}).get("count"):
                        profile["post_count"] = user["edge_owner_to_timeline_media"]["count"]
                    if user.get("full_name"):
                        profile["full_name"] = user["full_name"]
                    if user.get("biography"):
                        profile["bio"] = user["biography"]
                    if user.get("profile_pic_url_hd") or user.get("profile_pic_url"):
                        profile["profile_url"] = user.get("profile_pic_url_hd") or user.get("profile_pic_url")
                    if user.get("id"):
                        profile["instagram_pk"] = int(user["id"])
                    profile["is_verified"] = user.get("is_verified", False)
                    profile["is_private"] = user.get("is_private", False)

            if log_cb:
                log_cb(f"     팔로워 {profile['follower_count']:,} | 팔로잉 {profile['following_count']:,} | 게시물 {profile['post_count']:,}")

            return profile

        except Exception as e:
            if log_cb:
                log_cb(f"     ⚠️ 파싱 실패: {str(e)[:80]}")
            return None

    async def _count_reels_scroll(self, ctx, username, log_cb=None):
        """릴스 탭 스크롤 카운트 (API fallback)"""
        page = await ctx.new_page()
        reels_api_data = {}

        async def _on_response(response):
            try:
                if "web_profile_info" in response.url and response.status == 200:
                    body = await response.json()
                    user = body.get("data", {}).get("user", {})
                    if user:
                        reels_api_data["user"] = user
            except Exception:
                pass

        page.on("response", _on_response)

        try:
            await page.goto(
                f"https://www.instagram.com/{username}/reels/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await page.wait_for_timeout(random.randint(2000, 3000))

            # 릴스 탭 로드 후 API 응답 재확인
            user = reels_api_data.get("user", {})
            reels_count = (
                user.get("edge_felix_video_timeline", {}).get("count")
                or user.get("clips_count")
                or 0
            )
            if reels_count:
                await page.close()
                return reels_count

            # DOM 스크롤 카운트 (최후 수단)
            prev_count = 0
            no_change = 0
            for _ in range(8):
                count = await page.evaluate("""() => {
                    var items = document.querySelectorAll('a[href*="/reel/"], article a[role]');
                    return items.length;
                }""")
                if count == prev_count:
                    no_change += 1
                    if no_change >= 2:
                        break
                else:
                    no_change = 0
                    prev_count = count
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(random.randint(800, 1500))

            await page.close()
            return prev_count
        except Exception:
            await page.close()
            return 0
