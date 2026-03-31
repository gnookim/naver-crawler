"""블로그 크롤링 핸들러 — 상위 블로그 본문/소제목/이미지 수집"""
import asyncio
import random
from .base import BaseCrawler


class BlogCrawlHandler(BaseCrawler):

    async def handle(self, keyword, options, log_cb=None):
        from playwright.async_api import async_playwright
        max_items = options.get("max_items", 10)
        source = options.get("source", "blog_tab")
        need_body = options.get("need_body", True)
        need_headings = options.get("need_headings", True)

        async with async_playwright() as pw:
            browser, ctx = await self.create_browser(pw)
            page = await ctx.new_page()

            where = "post" if source == "blog_tab" else None
            if log_cb: log_cb(f"  🔍 {'블로그탭' if where else '통합검색'} 수집 중...")
            await self.human_search(page, keyword, where=where)

            links = await self._collect_blog_links(page, max_items)
            if log_cb: log_cb(f"     {len(links)}개 블로그 링크 수집")

            results = []
            detail_page = await ctx.new_page()
            for i, link in enumerate(links):
                if log_cb: log_cb(f"  [{i+1}/{len(links)}] {link['title'][:30]}...")
                data = await self._crawl_blog_post(detail_page, link, need_body, need_headings)
                data["rank"] = i + 1
                results.append(data)
                if i < len(links) - 1:
                    await asyncio.sleep(random.uniform(1.5, 3.0))

            await browser.close()

        return results

    async def _collect_blog_links(self, page, max_items):
        return await page.evaluate("""(maxItems) => {
            const results = [];
            const seen = new Set();
            const allLinks = document.querySelectorAll('a[href*="blog.naver.com"], a[href*="m.blog.naver.com"]');
            allLinks.forEach(a => {
                const href = a.href;
                if (!href.match(/blog\\.naver\\.com\\/[^/]+\\/\\d+/) && !href.match(/m\\.blog\\.naver\\.com\\/[^/]+\\/\\d+/)) return;
                if (seen.has(href)) return;
                const text = a.innerText.trim();
                if (text.length < 5 || text.length > 200) return;
                if (text.includes('블로그') && text.length < 10) return;
                seen.add(href);
                results.push({ title: text.slice(0, 150), url: href });
            });
            return results.slice(0, maxItems);
        }""", max_items)

    async def _crawl_blog_post(self, page, link, need_body, need_headings):
        url = link["url"]
        result = {
            "title": link["title"], "url": url, "blogger": "",
            "body": "", "headings": [], "word_count": 0,
            "image_count": 0, "image_alts": [], "has_video": False,
            "published_at": "", "link_count": {"internal": 0, "external": 0},
        }

        try:
            # 모바일 URL 변환 (이미 모바일이면 그대로, 데스크톱이면 변환)
            if "m.blog.naver.com" in url:
                mobile_url = url
            elif "blog.naver.com" in url:
                mobile_url = url.replace("://blog.naver.com", "://m.blog.naver.com")
            else:
                mobile_url = url

            await page.goto(mobile_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(random.randint(1000, 2000))
            await self.random_mouse(page)
            await self.human_scroll(page, times=random.randint(2, 3))

            post_data = await page.evaluate("""() => {
                const result = {
                    title: '', blogger: '', body: '', headings: [],
                    imageCount: 0, imageAlts: [], hasVideo: false, publishedAt: '',
                    internalLinks: 0, externalLinks: 0,
                };
                const titleEl = document.querySelector('.se-title-text, .pcol1, h3.se_textarea, .tit_h3');
                if (titleEl) result.title = titleEl.innerText.trim();
                const bloggerEl = document.querySelector('.nick, .blog_author, .writer_info .name, [class*="nickname"]');
                if (bloggerEl) result.blogger = bloggerEl.innerText.trim();
                const bodySelectors = ['.se-main-container', '.post_ct', '.postViewArea', '#postViewArea', '.se_component_wrap', 'article', '.blog_post'];
                let bodyEl = null;
                for (const sel of bodySelectors) { bodyEl = document.querySelector(sel); if (bodyEl) break; }
                if (bodyEl) {
                    result.body = bodyEl.innerText.trim();
                    const headingEls = bodyEl.querySelectorAll(
                        'h2, h3, h4, .se-section-title, .se-text-paragraph strong, .se-text-paragraph b, ' +
                        '.se-sticker-title, span[style*="font-size:2"], span[style*="font-size: 2"], ' +
                        'p > strong:only-child, p > b:only-child'
                    );
                    const seenH = new Set();
                    headingEls.forEach(h => {
                        const txt = h.innerText.trim();
                        if (txt.length > 2 && txt.length < 100 && !seenH.has(txt)) { seenH.add(txt); result.headings.push(txt); }
                    });
                    const imgs = bodyEl.querySelectorAll('img');
                    result.imageCount = imgs.length;
                    imgs.forEach(img => {
                        const alt = (img.alt || '').trim();
                        if (alt && alt !== 'image' && alt !== '이미지') result.imageAlts.push(alt);
                    });
                    result.hasVideo = bodyEl.querySelector('video, iframe[src*="youtube"], iframe[src*="tv.naver"], .se-video') !== null;
                    bodyEl.querySelectorAll('a[href]').forEach(a => {
                        const href = a.href || '';
                        if (href.includes('naver.com')) result.internalLinks++;
                        else if (href.startsWith('http')) result.externalLinks++;
                    });
                } else {
                    result.body = document.body.innerText.trim().slice(0, 5000);
                }
                const dateEl = document.querySelector('.se_publishDate, .blog_date, [class*="date"], time');
                if (dateEl) result.publishedAt = dateEl.innerText.trim();
                return result;
            }""")

            result["title"] = post_data.get("title") or link["title"]
            result["blogger"] = post_data.get("blogger", "")
            if need_body: result["body"] = post_data.get("body", "")
            result["word_count"] = len(post_data.get("body", ""))
            if need_headings: result["headings"] = post_data.get("headings", [])
            result["image_count"] = post_data.get("imageCount", 0)
            result["image_alts"] = post_data.get("imageAlts", [])
            result["has_video"] = post_data.get("hasVideo", False)
            result["published_at"] = post_data.get("publishedAt", "")
            result["link_count"] = {
                "internal": post_data.get("internalLinks", 0),
                "external": post_data.get("externalLinks", 0),
            }
        except Exception as e:
            result["error"] = str(e)[:200]

        return result
