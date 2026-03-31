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
                allLinks.forEach(a => {
                    const href = a.href;
                    if (!href.match(/blog\\.naver\\.com\\/[^/]+\\/\\d+/)) return;
                    if (seen.has(href)) return;
                    const text = a.innerText.trim();
                    if (text.length < 5 || text.length > 200) return;
                    if (text.includes('블로그') && text.length < 10) return;
                    seen.add(href);
                    results.push({ title: text.slice(0, 150), url: href });
                });
                return results.slice(0, maxItems);
            }""", max_items)

            if log_cb: log_cb(f"     {len(results)}개 수집")
            for i, r in enumerate(results):
                r["rank"] = i + 1

            await browser.close()

        return results
