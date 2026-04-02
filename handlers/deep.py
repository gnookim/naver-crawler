"""키워드 상위 컨텐츠 심화 분석 핸들러 — 통합검색 + 각 탭(블로그/지식인/카페)에서 상위 N개 세부 분석"""
import asyncio
import random
from .base import BaseCrawler


class DeepAnalysisHandler(BaseCrawler):

    async def handle(self, keyword, options, log_cb=None):
        from playwright.async_api import async_playwright

        max_items = options.get("max_items", 5)
        # 분석 대상 탭 (scope가 지정되면 해당 탭만, 없으면 전체)
        scope = options.get("scope", "all")  # "all", "integrated", "blog_tab", "kin_tab", "cafe_tab"

        async with async_playwright() as pw:
            browser, ctx = await self.create_browser(pw)
            page = await ctx.new_page()
            results = []

            tabs = self._get_tabs(scope)

            for tab_name, where_param in tabs:
                if log_cb:
                    log_cb(f"  🔍 [{tab_name}] 상위 {max_items}개 분석: {keyword}")

                await self.human_search(page, keyword, where=where_param)
                await self.human_scroll(page, times=random.randint(3, 5))

                # 상위 링크 수집
                links = await self._collect_top_links(page, tab_name, max_items)
                if log_cb:
                    log_cb(f"     {len(links)}개 링크 수집")

                # 각 링크 상세 분석
                detail_page = await ctx.new_page()
                for i, link in enumerate(links):
                    if log_cb:
                        log_cb(f"  [{i+1}/{len(links)}] {link['title'][:30]}...")
                    detail = await self._analyze_content(detail_page, link)
                    detail["tab"] = tab_name
                    detail["rank"] = i + 1
                    detail["keyword"] = keyword
                    results.append(detail)

                    if i < len(links) - 1:
                        await asyncio.sleep(random.uniform(2.0, 4.0))

                await detail_page.close()

                # 탭 전환 간 자연스러운 휴식
                if len(tabs) > 1:
                    await asyncio.sleep(random.uniform(3.0, 6.0))

            await browser.close()

        return results

    def _get_tabs(self, scope):
        """scope에 따라 분석할 탭 목록 반환"""
        all_tabs = [
            ("통합검색", None),
            ("블로그", "post"),
            ("지식iN", "kin"),
            ("카페", "cafe"),
        ]
        scope_map = {
            "integrated": [("통합검색", None)],
            "blog_tab": [("블로그", "post")],
            "kin_tab": [("지식iN", "kin")],
            "cafe_tab": [("카페", "cafe")],
        }
        return scope_map.get(scope, all_tabs)

    async def _collect_top_links(self, page, tab_name, max_items):
        """탭에서 상위 링크 수집"""
        return await page.evaluate("""(args) => {
            const { tabName, maxItems } = args;
            const results = [];
            const seen = new Set();

            // 블로그 링크
            if (tabName === '블로그' || tabName === '통합검색') {
                document.querySelectorAll('a[href*="blog.naver.com"]').forEach(a => {
                    const href = a.href;
                    if (seen.has(href) || results.length >= maxItems) return;
                    const title = (a.textContent || '').trim();
                    if (title.length < 3 || title.length > 200) return;
                    seen.add(href);
                    results.push({ title, url: href, type: 'blog' });
                });
            }

            // 지식인 링크
            if (tabName === '지식iN' || tabName === '통합검색') {
                document.querySelectorAll('a[href*="kin.naver.com/qna/detail"]').forEach(a => {
                    const href = a.href;
                    if (seen.has(href) || results.length >= maxItems) return;
                    if (href.includes('answerNo=') || href.includes('#answer')) return;
                    const title = (a.textContent || '').trim();
                    if (title.length < 3 || title.length > 200) return;
                    seen.add(href);
                    results.push({ title, url: href, type: 'kin' });
                });
            }

            // 카페 링크
            if (tabName === '카페' || tabName === '통합검색') {
                document.querySelectorAll('a[href*="cafe.naver.com"]').forEach(a => {
                    const href = a.href;
                    if (seen.has(href) || results.length >= maxItems) return;
                    const title = (a.textContent || '').trim();
                    if (title.length < 3 || title.length > 200) return;
                    seen.add(href);
                    results.push({ title, url: href, type: 'cafe' });
                });
            }

            // 일반 웹문서
            if (tabName === '통합검색' && results.length < maxItems) {
                document.querySelectorAll('.total_area a, .web_area a').forEach(a => {
                    const href = a.href;
                    if (seen.has(href) || results.length >= maxItems) return;
                    if (!href.startsWith('http')) return;
                    const title = (a.textContent || '').trim();
                    if (title.length < 3 || title.length > 200) return;
                    seen.add(href);
                    results.push({ title, url: href, type: 'web' });
                });
            }

            return results.slice(0, maxItems);
        }""", {"tabName": tab_name, "maxItems": max_items})

    async def _analyze_content(self, page, link):
        """개별 컨텐츠 상세 분석"""
        url = link["url"]
        content_type = link.get("type", "web")
        result = {
            "title": link["title"],
            "url": url,
            "content_type": content_type,
            "body": "",
            "headings": [],
            "word_count": 0,
            "image_count": 0,
            "has_video": False,
            "published_at": "",
            "error": None,
        }

        try:
            # 모바일 버전 변환 (블로그)
            nav_url = url
            if "blog.naver.com" in url and "m.blog" not in url:
                nav_url = url.replace("blog.naver.com", "m.blog.naver.com")

            await page.goto(nav_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(random.uniform(1.0, 2.0))
            await self.random_mouse(page)
            await self.human_scroll(page, times=random.randint(2, 4))

            data = await page.evaluate("""() => {
                // 제목
                const titleEl = document.querySelector(
                    '.se-title-text, .pcol1, h3.se_textarea, .tit_h3, h1, .title, article h2'
                );
                const title = titleEl ? titleEl.textContent.trim().slice(0, 200) : '';

                // 본문
                const bodyEl = document.querySelector(
                    '.se-main-container, .post_ct, .postViewArea, article, .content, .se_component_wrap'
                );
                const body = bodyEl ? bodyEl.innerText.trim().slice(0, 5000) : '';

                // 소제목
                const headings = [];
                document.querySelectorAll(
                    '.se-main-container h2, .se-main-container h3, .se-main-container h4, ' +
                    '.se-section-title, strong, b'
                ).forEach(el => {
                    const t = el.textContent.trim();
                    if (t.length >= 4 && t.length <= 100 && !headings.includes(t)) {
                        headings.push(t);
                    }
                });

                // 이미지 수
                const images = bodyEl ? bodyEl.querySelectorAll('img').length : 0;

                // 동영상
                const hasVideo = !!(
                    document.querySelector('video') ||
                    document.querySelector('iframe[src*="youtube"]') ||
                    document.querySelector('.se-video')
                );

                // 발행일
                const dateEl = document.querySelector(
                    '.se_publishDate, .blog_date, .date, time, .post_date, .pub_date'
                );
                const publishedAt = dateEl ? dateEl.textContent.trim() : '';

                // 링크 분석
                let internalLinks = 0, externalLinks = 0;
                if (bodyEl) {
                    bodyEl.querySelectorAll('a[href]').forEach(a => {
                        if (a.href.includes('naver.com')) internalLinks++;
                        else if (a.href.startsWith('http')) externalLinks++;
                    });
                }

                return {
                    title, body, headings: headings.slice(0, 20),
                    word_count: body.length,
                    image_count: images, has_video: hasVideo,
                    published_at: publishedAt,
                    internal_links: internalLinks,
                    external_links: externalLinks,
                };
            }""")

            result.update({
                "title": data.get("title") or link["title"],
                "body": data.get("body", ""),
                "headings": data.get("headings", []),
                "word_count": data.get("word_count", 0),
                "image_count": data.get("image_count", 0),
                "has_video": data.get("has_video", False),
                "published_at": data.get("published_at", ""),
                "internal_links": data.get("internal_links", 0),
                "external_links": data.get("external_links", 0),
            })
        except Exception as e:
            result["error"] = str(e)[:200]

        return result
