"""
기본 크롤러 — 사람 흉내 + 봇 탐지 우회
모든 핸들러가 이 클래스를 상속받는다.
"""
import random


class BaseCrawler:
    UA_LIST = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    ]

    def __init__(self, headless=True):
        self.headless = headless

    async def create_browser(self, pw):
        ua = random.choice(self.UA_LIST)
        vw = random.choice([1280, 1366, 1440, 1536, 1920])
        vh = random.choice([720, 768, 800, 900, 1080])
        browser = await pw.chromium.launch(
            headless=self.headless,
            slow_mo=200 if not self.headless else 0,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=ua, locale="ko-KR",
            extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9"},
            viewport={"width": vw, "height": vh},
        )
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)
        return browser, ctx

    async def random_mouse(self, page):
        for _ in range(random.randint(2, 4)):
            await page.mouse.move(
                random.randint(100, 900), random.randint(100, 600),
                steps=random.randint(5, 15))
            await page.wait_for_timeout(random.randint(50, 200))

    async def human_scroll(self, page, times=4):
        for i in range(times):
            amount = random.randint(200, 600)
            await page.evaluate(f"window.scrollBy({{top: {amount}, behavior: 'smooth'}})")
            await page.wait_for_timeout(random.randint(400, 900))
            if random.random() < 0.4:
                await self.random_mouse(page)
            if random.random() < 0.2 and i > 0:
                await page.evaluate(f"window.scrollBy({{top: -{random.randint(50, 150)}, behavior: 'smooth'}})")
                await page.wait_for_timeout(random.randint(200, 400))

    async def human_search(self, page, query, where=None):
        """사람처럼 네이버 검색"""
        await page.goto("https://www.naver.com", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(random.randint(800, 1400))
        await self.random_mouse(page)

        si = await page.query_selector("#query, #search, input[name='query']")
        if not si:
            url = f"https://search.naver.com/search.naver?query={query}"
            if where: url += f"&where={where}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1000)
            return

        box = await si.bounding_box()
        if box:
            await page.mouse.move(
                box["x"] + box["width"] * random.uniform(0.2, 0.8),
                box["y"] + box["height"] * random.uniform(0.3, 0.7),
                steps=random.randint(8, 20))
            await page.wait_for_timeout(random.randint(100, 300))
        await si.click()
        await page.wait_for_timeout(random.randint(300, 600))

        typo_done = False
        for i, char in enumerate(query):
            await si.type(char, delay=random.randint(60, 180))
            if not typo_done and random.random() < 0.05 and i > 2:
                await si.type(random.choice("ㅁㄴㅇㄹㅎㅗㅓㅏㅣ"), delay=random.randint(40, 100))
                await page.wait_for_timeout(random.randint(200, 500))
                await si.press("Backspace")
                await page.wait_for_timeout(random.randint(100, 300))
                typo_done = True
            if random.random() < 0.08:
                await page.wait_for_timeout(random.randint(300, 800))

        await page.wait_for_timeout(random.randint(500, 1200))
        await si.press("Enter")
        await page.wait_for_timeout(random.randint(1500, 2500))
        await self.random_mouse(page)

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
                                await page.wait_for_timeout(random.randint(100, 300))
                            await tab_el.click(timeout=5000)
                            clicked = True
                            await page.wait_for_timeout(random.randint(1000, 1800))
                            break
                    except Exception:
                        continue
                if not clicked:
                    await page.goto(
                        f"https://search.naver.com/search.naver?query={query}&where={where}",
                        wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(1000)

        await self.human_scroll(page, times=random.randint(3, 5))
        await page.wait_for_timeout(random.randint(300, 700))
