"""통합검색 영역 순위 분석 핸들러 — 검색 결과에 표시되는 영역(섹션)들의 존재 + 순서 파악"""
import asyncio
import random
from .base import BaseCrawler


class AreaAnalysisHandler(BaseCrawler):

    async def handle(self, keyword, options, log_cb=None):
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser, ctx = await self.create_browser(pw)
            page = await ctx.new_page()

            if log_cb:
                log_cb(f"  🔍 통합검색 영역 분석: {keyword}")
            await self.human_search(page, keyword)

            # 추가 스크롤 — 하단 영역까지 로드
            await self.human_scroll(page, times=random.randint(6, 10))

            # 영역(섹션) 추출
            areas = await page.evaluate("""() => {
                const results = [];
                const seen = new Set();

                // 네이버 통합검색 영역 매핑
                const AREA_MAP = {
                    'lst_total_pwr': '파워링크',
                    'pw_ad': '파워링크',
                    'sp_nplace': '플레이스',
                    'place-main': '플레이스',
                    'lst_total_place': '플레이스',
                    'sp_blog': '블로그',
                    'blog': '블로그',
                    'lst_total_blog': '블로그',
                    'sp_view': 'VIEW',
                    'view': 'VIEW',
                    'sp_kin': '지식iN',
                    'kin': '지식iN',
                    'lst_total_kin': '지식iN',
                    'sp_cafe': '카페',
                    'cafe': '카페',
                    'lst_total_cafe': '카페',
                    'web': '웹문서',
                    'sp_website': '웹사이트',
                    'lst_total_web': '웹문서',
                    'sp_nws': '뉴스',
                    'news': '뉴스',
                    'lst_total_news': '뉴스',
                    'sp_image': '이미지',
                    'image': '이미지',
                    'sp_video': '동영상',
                    'video': '동영상',
                    'clip': '클립',
                    'sp_clip': '클립',
                    'sp_shop': '쇼핑',
                    'shopping': '쇼핑',
                    'lst_total_shop': '쇼핑',
                    'sp_book': '도서',
                    'sp_movie': '영화',
                    'sp_music': '음악',
                    'sp_realestate': '부동산',
                    'sp_local': '지역정보',
                    'sp_influencer': '인플루언서',
                    'sp_faq': 'FAQ',
                };

                // 1차: id 기반 영역 탐색
                for (const [id, name] of Object.entries(AREA_MAP)) {
                    const el = document.getElementById(id);
                    if (el && el.offsetHeight > 0 && !seen.has(name)) {
                        const rect = el.getBoundingClientRect();
                        results.push({ name, y: rect.top + window.scrollY, source: 'id:' + id });
                        seen.add(name);
                    }
                }

                // 2차: data-area 속성 기반
                document.querySelectorAll('[data-area]').forEach(el => {
                    const area = el.getAttribute('data-area');
                    const name = AREA_MAP[area] || area;
                    if (!seen.has(name) && el.offsetHeight > 0) {
                        const rect = el.getBoundingClientRect();
                        results.push({ name, y: rect.top + window.scrollY, source: 'data-area:' + area });
                        seen.add(name);
                    }
                });

                // 3차: section 헤더 텍스트 기반 (fallback)
                const HEADER_MAP = {
                    '파워링크': '파워링크', '플레이스': '플레이스',
                    '블로그': '블로그', 'VIEW': 'VIEW',
                    '지식iN': '지식iN', '지식인': '지식iN',
                    '카페': '카페', '인기글': '카페',
                    '웹문서': '웹문서', '뉴스': '뉴스',
                    '이미지': '이미지', '동영상': '동영상',
                    '쇼핑': '쇼핑', '클립': '클립',
                    '인플루언서': '인플루언서',
                };
                document.querySelectorAll('h2, .api_title, .tit_area').forEach(el => {
                    const text = (el.textContent || '').trim();
                    for (const [keyword, name] of Object.entries(HEADER_MAP)) {
                        if (text.includes(keyword) && !seen.has(name) && el.offsetHeight > 0) {
                            const rect = el.getBoundingClientRect();
                            results.push({ name, y: rect.top + window.scrollY, source: 'header:' + text.slice(0, 30) });
                            seen.add(name);
                            break;
                        }
                    }
                });

                // y좌표로 정렬 → 순위 부여
                results.sort((a, b) => a.y - b.y);
                return results.map((r, i) => ({
                    rank: i + 1,
                    area: r.name,
                    y_position: Math.round(r.y),
                    source: r.source,
                }));
            }""")

            if log_cb:
                log_cb(f"     {len(areas)}개 영역 감지")
                for a in areas:
                    log_cb(f"     {a['rank']}위: {a['area']}")

            await browser.close()

        return areas
