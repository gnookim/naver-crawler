"""블로그 SERP 핸들러 — 검색결과 순위만 수집 (본문 크롤링 없음)"""
from .base import BaseCrawler


class BlogSerpHandler(BaseCrawler):

    async def handle(self, keyword, options, log_cb=None):
        from playwright.async_api import async_playwright
        max_items = options.get("max_items", 20)
        source = options.get("source", "integrated")

        async with async_playwright() as pw:
            browser, ctx = await self.create_browser(pw)
            page = await ctx.new_page()

            where = "post" if source == "blog_tab" else None
            if log_cb: log_cb(f"  🔍 {'블로그탭' if where else '통합검색'} 순위 수집 중...")
            await self.human_search(page, keyword, where=where)

            results = await page.evaluate("""(maxItems) => {
                const results = [];
                const seen = new Set();
                const allLinks = document.querySelectorAll('a[href*="blog.naver.com"]');
                // 1차: URL별로 가장 긴 텍스트를 가진 링크 수집
                const urlMap = new Map();
                allLinks.forEach(a => {
                    const href = a.href;
                    if (!href.match(/blog\\.naver\\.com\\/[^/]+\\/\\d+/)) return;
                    // URL 정규화 (쿼리 파라미터 제거)
                    const cleanUrl = href.split('?')[0];
                    const text = (a.innerText || '').trim();
                    const existing = urlMap.get(cleanUrl);
                    if (!existing || text.length > (existing.title || '').length) {
                        urlMap.set(cleanUrl, { title: text, url: cleanUrl });
                    }
                });
                // 2차: 제목이 있는 것만 결과로
                for (const [url, item] of urlMap) {
                    if (item.title.length < 3) continue;
                    if (item.title.length > 200) item.title = item.title.slice(0, 150);
                    if (item.title === '블로그') continue;
                    results.push({ title: item.title.slice(0, 150), url: item.url });
                    if (results.length >= maxItems) break;
                }
                return results;
            }""", max_items)

            if log_cb: log_cb(f"     {len(results)}개 수집")
            for i, r in enumerate(results):
                r["rank"] = i + 1

            await browser.close()

        return results
