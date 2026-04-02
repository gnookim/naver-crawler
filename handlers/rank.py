"""일일 순위 체크 핸들러 — 등록된 URL이 검색 결과에서 몇 위에 있는지 확인"""
import asyncio
import random
from .base import BaseCrawler


class DailyRankHandler(BaseCrawler):

    async def handle(self, keyword, options, log_cb=None):
        from playwright.async_api import async_playwright

        target_url = options.get("target_url", "")
        max_pages = options.get("max_pages", 3)  # 검색 결과 최대 페이지
        check_tabs = options.get("check_tabs", ["integrated", "blog_tab"])

        if not target_url:
            return [{"error": "target_url이 필요합니다", "keyword": keyword}]

        # URL 정규화 (프로토콜/www/슬래시 제거하여 비교)
        normalized_target = self._normalize_url(target_url)

        async with async_playwright() as pw:
            browser, ctx = await self.create_browser(pw)
            page = await ctx.new_page()
            results = []

            for tab_name in check_tabs:
                where = None
                tab_label = "통합검색"
                if tab_name == "blog_tab":
                    where = "post"
                    tab_label = "블로그탭"
                elif tab_name == "kin_tab":
                    where = "kin"
                    tab_label = "지식iN탭"
                elif tab_name == "cafe_tab":
                    where = "cafe"
                    tab_label = "카페탭"

                if log_cb:
                    log_cb(f"  🔍 [{tab_label}] 순위 체크: {keyword}")

                await self.human_search(page, keyword, where=where)
                rank_info = await self._find_rank(page, normalized_target, max_pages, log_cb)

                results.append({
                    "keyword": keyword,
                    "target_url": target_url,
                    "tab": tab_label,
                    "rank": rank_info["rank"],
                    "found_url": rank_info.get("found_url", ""),
                    "total_checked": rank_info["total_checked"],
                })

                if log_cb:
                    if rank_info["rank"] > 0:
                        log_cb(f"     {tab_label}: {rank_info['rank']}위")
                    else:
                        log_cb(f"     {tab_label}: 미발견 ({rank_info['total_checked']}개 확인)")

                # 탭 전환 간 자연스러운 간격
                if len(check_tabs) > 1:
                    await asyncio.sleep(random.uniform(2.0, 4.0))

            await browser.close()

        return results

    def _normalize_url(self, url):
        """URL 정규화 — 비교용"""
        url = url.lower().strip()
        for prefix in ["https://", "http://", "www."]:
            if url.startswith(prefix):
                url = url[len(prefix):]
        return url.rstrip("/")

    async def _find_rank(self, page, normalized_target, max_pages, log_cb=None):
        """현재 검색 결과 페이지에서 target URL 순위 찾기"""
        total_checked = 0

        for page_num in range(1, max_pages + 1):
            if page_num > 1:
                # 다음 페이지 이동
                next_clicked = await self._go_next_page(page, page_num)
                if not next_clicked:
                    break
                await asyncio.sleep(random.uniform(1.5, 3.0))
                await self.human_scroll(page, times=random.randint(2, 4))

            # 현재 페이지의 모든 링크 수집
            links = await page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href;
                    if (!href || !href.startsWith('http')) return;
                    // 네이버 내부 네비게이션 링크 제외
                    if (href.includes('search.naver.com') && !href.includes('blog.naver') && !href.includes('kin.naver') && !href.includes('cafe.naver')) return;
                    if (href.includes('naver.com/search')) return;
                    const title = (a.textContent || '').trim();
                    if (title.length < 2) return;
                    results.push({ url: href, title: title.slice(0, 150) });
                });
                return results;
            }""")

            # 결과에서 target URL 검색
            seen = set()
            rank_in_page = 0
            for link in links:
                normalized = self._normalize_url(link["url"])
                if normalized in seen:
                    continue
                seen.add(normalized)
                rank_in_page += 1
                total_checked += 1

                if normalized_target in normalized or normalized in normalized_target:
                    return {
                        "rank": total_checked,
                        "found_url": link["url"],
                        "total_checked": total_checked,
                    }

        return {"rank": 0, "total_checked": total_checked}

    async def _go_next_page(self, page, target_page):
        """검색 결과 다음 페이지 이동"""
        try:
            # 페이지 번호 클릭
            next_btn = await page.query_selector(f'a.btn_next, a[href*="start={((target_page-1)*10)+1}"]')
            if next_btn:
                await self.random_mouse(page)
                await next_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                return True

            # 숫자 페이지네이션
            pager = await page.query_selector(f'a:text("{target_page}")')
            if pager:
                await pager.click()
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
                return True
        except Exception:
            pass
        return False
