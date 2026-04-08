"""
기본 크롤러 — 사람 흉내 + 봇 탐지 우회 + 프록시 지원
모든 핸들러가 이 클래스를 상속받는다.
"""
import os
import random
import time


class BaseCrawler:
    UA_LIST = [
        # Chrome (Mac)
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        # Safari (Mac)
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        # Chrome (Windows)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        # Edge (Windows)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
        # Chrome (Mobile - 가끔 섞기)
        "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    ]

    # 실제 사용자 화면 해상도 분포 반영
    VIEWPORT_PRESETS = [
        (1280, 720), (1366, 768), (1440, 900), (1536, 864),
        (1920, 1080), (2560, 1440),
        # 모바일 (모바일 UA와 매칭)
        (390, 844), (393, 873), (412, 915),
    ]

    # 검색 전 방문할 수 있는 네이버 페이지들
    WARMUP_URLS = [
        "https://www.naver.com",
        "https://news.naver.com",
        "https://finance.naver.com",
        "https://sports.naver.com",
        "https://shopping.naver.com",
    ]

    def __init__(self, headless=True, config=None):
        self.headless = headless
        self.config = config or {}
        self.proxy = self.config.get("proxy_url")
        self._session_count = 0

    async def create_browser(self, pw):
        ua = random.choice(self.config.get("ua_pool") or self.UA_LIST)
        is_mobile = "Mobile" in ua or "iPhone" in ua

        if is_mobile:
            vw, vh = random.choice(self.VIEWPORT_PRESETS[-3:])
        else:
            vw, vh = random.choice(self.VIEWPORT_PRESETS[:-3])

        launch_args = ["--disable-blink-features=AutomationControlled"]

        # 프록시 설정
        proxy_config = None
        if self.proxy:
            proxy_config = {"server": self.proxy}
            # 인증 프록시 (user:pass@host:port)
            if "@" in self.proxy:
                parts = self.proxy.replace("http://", "").replace("https://", "")
                if "@" in parts:
                    auth, server = parts.rsplit("@", 1)
                    user, pwd = auth.split(":", 1)
                    proxy_config = {
                        "server": f"http://{server}",
                        "username": user,
                        "password": pwd,
                    }

        browser = await pw.chromium.launch(
            headless=self.headless,
            slow_mo=random.randint(100, 300) if not self.headless else 0,
            args=launch_args,
            proxy=proxy_config,
        )

        # 타임존 랜덤화
        timezones = ["Asia/Seoul", "Asia/Seoul", "Asia/Seoul", "Asia/Tokyo", "Asia/Shanghai"]
        ctx = await browser.new_context(
            user_agent=ua,
            locale="ko-KR",
            timezone_id=random.choice(timezones),
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
            viewport={"width": vw, "height": vh},
            is_mobile=is_mobile,
            has_touch=is_mobile,
        )

        # 봇 탐지 우회 스크립트
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5].map(() => ({ name: 'plugin', filename: 'plugin.so' }))
            });
            Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => [4, 8, 12, 16][Math.floor(Math.random() * 4)] });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => [4, 8, 16][Math.floor(Math.random() * 3)] });
            window.chrome = { runtime: {}, csi: () => {} };

            // canvas fingerprint 노이즈
            const origToBlob = HTMLCanvasElement.prototype.toBlob;
            HTMLCanvasElement.prototype.toBlob = function(cb, type, quality) {
                const ctx = this.getContext('2d');
                if (ctx) {
                    const p = ctx.getImageData(0, 0, 1, 1);
                    p.data[0] = p.data[0] ^ (Math.random() * 2 | 0);
                    ctx.putImageData(p, 0, 0);
                }
                return origToBlob.call(this, cb, type, quality);
            };

            // WebGL fingerprint 노이즈
            const getParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(param) {
                if (param === 37445) return 'Intel Inc.';
                if (param === 37446) return 'Intel Iris OpenGL Engine';
                return getParam.call(this, param);
            };
        """)

        self._session_count += 1
        return browser, ctx

    async def random_mouse(self, page):
        """자연스러운 마우스 움직임 — 베지어 곡선 느낌"""
        moves = random.randint(2, 5)
        for _ in range(moves):
            x = random.randint(50, 1200)
            y = random.randint(50, 700)
            steps = random.randint(10, 25)
            await page.mouse.move(x, y, steps=steps)
            await page.wait_for_timeout(random.randint(30, 150))

    async def human_scroll(self, page, times=4):
        """자연스러운 스크롤 — 속도 변화 + 멈춤 + 되돌아보기"""
        for i in range(times):
            amount = random.randint(150, 500)
            await page.evaluate(f"window.scrollBy({{top: {amount}, behavior: 'smooth'}})")
            await page.wait_for_timeout(random.randint(400, 1200))

            # 읽는 척 멈추기 (30% 확률)
            if random.random() < 0.3:
                await page.wait_for_timeout(random.randint(1000, 3000))

            # 마우스 움직임 (40% 확률)
            if random.random() < 0.4:
                await self.random_mouse(page)

            # 위로 되돌아보기 (20% 확률, 첫 번째 아님)
            if random.random() < 0.2 and i > 0:
                back_amount = random.randint(50, 200)
                await page.evaluate(f"window.scrollBy({{top: -{back_amount}, behavior: 'smooth'}})")
                await page.wait_for_timeout(random.randint(300, 800))

    async def warmup_session(self, page):
        """검색 전 네이버 워밍업 — 세션 쿠키 생성 + 자연스러운 진입"""
        if random.random() < 0.6:
            warmup_url = random.choice(self.WARMUP_URLS)
            try:
                await page.goto(warmup_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(random.randint(1500, 4000))
                await self.random_mouse(page)
                await self.human_scroll(page, times=random.randint(1, 3))
            except Exception:
                pass

    async def click_random_result(self, page):
        """검색 결과 중 하나를 랜덤 클릭 후 돌아오기 — 자연스러운 행동"""
        if random.random() < 0.25:
            try:
                links = await page.query_selector_all("a[href*='blog.naver'], a[href*='kin.naver']")
                if links and len(links) > 2:
                    target = random.choice(links[2:])  # 상위 2개 제외
                    if await target.is_visible():
                        box = await target.bounding_box()
                        if box:
                            await page.mouse.move(
                                box["x"] + box["width"] * random.uniform(0.1, 0.9),
                                box["y"] + box["height"] * random.uniform(0.2, 0.8),
                                steps=random.randint(10, 20))
                        await target.click(timeout=5000)
                        await page.wait_for_timeout(random.randint(2000, 5000))
                        await self.human_scroll(page, times=random.randint(1, 3))
                        await page.go_back(timeout=10000)
                        await page.wait_for_timeout(random.randint(500, 1500))
            except Exception:
                pass

    # 무작위 decoy 검색 키워드 (다양한 관심사)
    # 워커별 관심사 프로필 — 같은 워커는 일관된 관심사로 검색
    DECOY_PROFILES = {
        "생활": ["오늘 날씨", "맛집 추천", "카페 추천", "치킨 배달", "주말 나들이",
                 "한강 공원", "자취 꿀팁", "인테리어 비용", "생일 선물", "세탁기 추천"],
        "IT": ["맥북 할인", "아이폰 케이스", "가성비 이어폰", "코딩 배우기", "AI 뉴스",
               "모니터 추천", "기계식 키보드", "클라우드 서비스", "앱 개발", "데이터 분석"],
        "건강": ["다이어트 방법", "운동 루틴", "헬스장 가격", "단백질 보충제", "요가 효과",
                 "러닝화 추천", "건강검진 비용", "수면 팁", "스트레칭", "비타민 추천"],
        "재테크": ["주식 시세", "전세 매물", "연말정산", "적금 금리", "부동산 전망",
                  "ETF 추천", "신용카드 혜택", "중고차 시세", "보험 비교", "재테크 초보"],
        "취미": ["영화 순위", "넷플릭스 추천", "여행 추천", "캠핑 장비", "독서 추천",
                "게임 추천", "콘서트 일정", "등산 코스", "낚시 포인트", "사진 촬영 팁"],
    }
    # fallback — 프로필 미지정 시
    DECOY_QUERIES = [q for qs in DECOY_PROFILES.values() for q in qs]

    def _get_decoy_queries(self):
        """워커 config의 decoy_profile에 따라 키워드 세트 반환"""
        profile = self.config.get("decoy_profile", "")
        if profile and profile in self.DECOY_PROFILES:
            return self.DECOY_PROFILES[profile]
        # 프로필 미지정 시 워커 ID 기반으로 자동 배정
        worker_id = os.environ.get("WORKER_ID", "")
        if worker_id:
            profiles = list(self.DECOY_PROFILES.keys())
            idx = hash(worker_id) % len(profiles)
            return self.DECOY_PROFILES[profiles[idx]]
        return self.DECOY_QUERIES

    async def decoy_search(self, page):
        """목적 없는 검색 — 워커별 관심사 프로필로 패턴 위장"""
        query = random.choice(self._get_decoy_queries())
        try:
            await page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(random.randint(500, 1500))
            si = await page.query_selector("#query, #search, input[name='query']")
            if si:
                await si.click()
                await si.fill("")
                for ch in query:
                    await page.keyboard.type(ch, delay=random.randint(50, 150))
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(random.randint(1500, 3000))
                await self.human_scroll(page, times=random.randint(2, 4))
                # 가끔 결과 클릭
                if random.random() < 0.3:
                    await self.click_random_result(page)
                await page.wait_for_timeout(random.randint(500, 2000))
        except Exception:
            pass

    async def human_search(self, page, query, where=None):
        """사람처럼 네이버 검색 — 워밍업 + 타이핑 + 오타 + 랜덤 클릭"""
        # 워밍업 (세션 생성)
        await self.warmup_session(page)

        await page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(random.randint(800, 2000))
        await self.random_mouse(page)

        si = await page.query_selector("#query, #search, input[name='query']")
        if not si:
            url = f"https://search.naver.com/search.naver?query={query}"
            if where:
                url += f"&where={where}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)
            return

        box = await si.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + box["width"] * random.uniform(0.2, 0.8),
                box["y"] + box["height"] * random.uniform(0.3, 0.7),
                steps=random.randint(8, 20))
            await page.wait_for_timeout(random.randint(100, 400))
        await si.click()
        await page.wait_for_timeout(random.randint(300, 800))

        # 타이핑 — 오타 + 멈춤 + 속도 변화
        typo_count = 0
        max_typos = random.randint(0, 2)
        for i, char in enumerate(query):
            # 타이핑 속도 변화 (단어 경계에서 느려짐)
            if char == " ":
                delay = random.randint(150, 400)
            else:
                delay = random.randint(50, 200)
            await si.type(char, delay=delay)

            # 오타 (5% 확률, 최대 2번)
            if typo_count < max_typos and random.random() < 0.05 and i > 2:
                typo_char = random.choice("ㅁㄴㅇㄹㅎㅗㅓㅏㅣㅂㅈㄷㅅ")
                await si.type(typo_char, delay=random.randint(30, 80))
                await page.wait_for_timeout(random.randint(200, 600))
                await si.press("Backspace")
                await page.wait_for_timeout(random.randint(100, 300))
                typo_count += 1

            # 타이핑 중 멈춤 (8% 확률 — 생각하는 듯)
            if random.random() < 0.08:
                await page.wait_for_timeout(random.randint(300, 1200))

        await page.wait_for_timeout(random.randint(500, 1500))

        # 자동완성 무시하고 Enter (가끔 잠깐 봄)
        if random.random() < 0.3:
            await page.wait_for_timeout(random.randint(500, 1500))
        await si.press("Enter")
        await page.wait_for_timeout(random.randint(1500, 3000))
        await self.random_mouse(page)

        # 탭 이동 (블로그/지식iN)
        if where:
            tab_map = {
                "kin": 'a[href*="where=kin"], a:has-text("지식iN"), a:has-text("지식IN")',
                "blog": 'a[href*="where=post"], a:has-text("블로그")',
                "post": 'a[href*="where=post"], a:has-text("블로그")',
            }
            selector = tab_map.get(where)
            if selector:
                clicked = False
                tabs = await page.query_selector_all(selector)
                for tab_el in tabs:
                    try:
                        if await tab_el.is_visible():
                            tb = await tab_el.bounding_box()
                            if tb:
                                await page.mouse.move(
                                    tb["x"] + tb["width"] * random.uniform(0.2, 0.8),
                                    tb["y"] + tb["height"] * random.uniform(0.3, 0.7),
                                    steps=random.randint(8, 15))
                                await page.wait_for_timeout(random.randint(100, 400))
                            await tab_el.click(timeout=5000)
                            clicked = True
                            await page.wait_for_timeout(random.randint(1000, 2000))
                            break
                    except Exception:
                        continue
                if not clicked:
                    await page.goto(
                        f"https://search.naver.com/search.naver?query={query}&where={where}",
                        wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(1000)

        # 결과 페이지에서 자연스럽게 스크롤
        await self.human_scroll(page, times=random.randint(3, 6))
        await page.wait_for_timeout(random.randint(300, 800))

        # 랜덤 결과 클릭 후 돌아오기 (자연스러움)
        await self.click_random_result(page)
