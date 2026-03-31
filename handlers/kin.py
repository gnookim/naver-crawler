"""지식인 경로 분석 핸들러 — 일반 지식인 vs 닥톡(FAQ) 판별"""
import re
from .base import BaseCrawler


class KinHandler(BaseCrawler):

    async def handle(self, keyword, options, log_cb=None):
        from playwright.async_api import async_playwright
        search_mode = options.get("search_mode", "both")
        max_items = options.get("max_items", 20)
        results = []

        async with async_playwright() as pw:
            browser, ctx = await self.create_browser(pw)
            page = await ctx.new_page()

            for where, tab_name in [
                (None, "통합검색") if search_mode in ("integrated", "both") else (None, None),
                ("kin", "지식인탭") if search_mode in ("kin_tab", "both") else (None, None),
            ]:
                if tab_name is None:
                    continue
                if log_cb: log_cb(f"  🔍 {tab_name} 수집 중...")
                await self.human_search(page, keyword, where=where)
                items = await self._collect_kin_links(page, max_items, tab_name)
                if log_cb: log_cb(f"     {len(items)}개 수집")
                results.extend(items)

            await browser.close()

        seen = set()
        unique = []
        for item in results:
            if item["url"] not in seen:
                seen.add(item["url"])
                item["rank"] = len(unique) + 1
                unique.append(item)

        for item in unique[:max_items]:
            title = item["title"].strip()
            item["source_type"] = "daktok" if re.search(r'\([^()]{2,}\)\s*$', title) else "jisikin"

        return unique[:max_items]

    async def _collect_kin_links(self, page, max_items, tab):
        grouped = await page.evaluate("""() => {
            const links = document.querySelectorAll('a[href*="kin.naver.com/qna/detail"]');
            const groups = new Map(); const order = [];
            links.forEach(a => {
                const href = a.href;
                if (href.includes('answerNo=') || href.includes('#answer')) return;
                const m = href.match(/docId=(\\d+)/); if (!m) return;
                const docId = m[1]; const text = a.innerText.trim();
                if (!groups.has(docId)) {
                    let card = a;
                    for (let i = 0; i < 15 && card; i++) {
                        if (card.classList && card.classList.contains('api_subject_bx')) break;
                        card = card.parentElement;
                    }
                    let cardTitle = '';
                    if (card && card.classList && card.classList.contains('api_subject_bx')) {
                        const lines = card.innerText.split('\\n').map(l => l.trim())
                            .filter(l => l.length > 5 && !l.includes('FAQ') && !l.includes('지식iN')
                                && !l.includes('더보기') && !l.match(/^[가-힣]{2,3}$/)
                                && !l.match(/^(의사|약사|한의사|간호사|변호사|세무사)/));
                        if (lines.length > 0) cardTitle = lines[0].slice(0, 150);
                    }
                    groups.set(docId, { links: [], cardTitle, firstHref: href }); order.push(docId);
                }
                groups.get(docId).links.push({ href, text: text.slice(0, 200) });
            });
            return order.map(docId => ({
                docId, links: groups.get(docId).links,
                cardTitle: groups.get(docId).cardTitle, firstHref: groups.get(docId).firstHref,
            }));
        }""")

        items = []
        for group in grouped:
            if len(items) >= max_items: break
            title_text, title_href = "", ""
            for lnk in group["links"]:
                txt = lnk["text"]
                if len(txt) > 4 and "지식iN" not in txt and "지식IN" not in txt and len(txt) < 200:
                    title_text, title_href = txt, lnk["href"]; break
            if not title_text:
                title_text = group.get("cardTitle", "")
                title_href = group.get("firstHref", "")
            if not title_text or not title_href: continue

            items.append({
                "rank": len(items) + 1,
                "title": title_text.splitlines()[0].strip()[:150],
                "url": title_href,
                "doc_id": group["docId"],
                "search_tab": tab,
            })
        return items
