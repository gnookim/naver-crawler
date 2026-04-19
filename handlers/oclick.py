"""Oclick 재고 동기화 + 매출 수집 핸들러
admin.oclick.co.kr에 로그인 후 XML 데이터 수집
"""
import re
import os
import urllib.request
import json
from .base import BaseCrawler

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
_SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

PAGE_SIZE = 500
MAX_PAGES = 20  # 최대 10,000개
SALES_PAGE_SIZE = 500
SALES_MAX_PAGES = 40  # 최대 20,000건


def _sb_headers():
    return {
        "apikey": _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _parse_oclick_xml_cells(row_xml):
    """dhtmlxgrid XML <cell> 파싱"""
    cells = []
    for m in re.finditer(r'<cell[^>]*>([\s\S]*?)</cell>', row_xml):
        val = m.group(1)
        val = re.sub(r'<!\[CDATA\[|\]\]>', '', val)
        val = re.sub(r'<[^>]+>', '', val)
        val = val.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ')
        cells.append(val.strip())
    return cells


def _load_credentials(options):
    """options 또는 station_settings에서 자격증명 로드"""
    company_code = options.get('company_code', '')
    user_id = options.get('user_id', '') or options.get('company_name', '')
    password = options.get('password', '')

    if company_code and user_id and password:
        return company_code, user_id, password

    # station_settings에서 로드
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return company_code, user_id, password

    try:
        keys = ['oclick_company_code', 'oclick_user_id', 'oclick_password']
        url = f"{_SUPABASE_URL}/rest/v1/station_settings?key=in.({','.join(keys)})&select=key,value"
        req = urllib.request.Request(url, headers=_sb_headers())
        with urllib.request.urlopen(req, timeout=5) as resp:
            rows = json.loads(resp.read())
        cfg = {r['key']: r['value'] for r in rows}
        company_code = company_code or cfg.get('oclick_company_code', '')
        user_id = user_id or cfg.get('oclick_user_id', '')
        password = password or cfg.get('oclick_password', '')
    except Exception:
        pass

    return company_code, user_id, password


class OclickSyncHandler(BaseCrawler):

    async def handle(self, keyword, options, log_cb=None):
        def log(msg):
            if log_cb:
                log_cb(f"  [Oclick] {msg}")

        company_code, user_id, password = _load_credentials(options)
        if not company_code or not user_id or not password:
            raise ValueError("Oclick 자격증명 없음 — station_settings에 oclick_* 등록 필요")

        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser, ctx = await self.create_browser(pw)
            page = await ctx.new_page()
            items = []

            try:
                # 1. 로그인
                log("로그인 중...")
                await page.goto('https://www.oclick.co.kr', wait_until='networkidle', timeout=20000)
                await page.fill('#in_usercu', company_code)
                await page.fill('#in_userid', user_id)
                await page.fill('#in_passwd', password)
                await page.click('button.bt_m_button01')
                await page.wait_for_load_state('networkidle', timeout=15000)

                after_url = page.url
                if 'admin.oclick.co.kr' not in after_url:
                    err = await page.evaluate('document.body.innerText')
                    raise ValueError(f"로그인 실패: {err[:200]}")
                log(f"로그인 성공")

                # 2. 상품마스터 → urlKey 추출
                await page.goto('https://admin.oclick.co.kr/sell/ProdMstSelect1.jsp',
                                wait_until='networkidle', timeout=15000)
                url_key = await page.evaluate("""() => {
                    const scripts = Array.from(document.querySelectorAll('script:not([src])'))
                        .map(s => s.textContent).join('\\n')
                    const m = scripts.match(/urlKey=(\\[S\\d+\\]\\[ProdMstSelect1\\]\\[\\d+\\]\\[all\\])/)
                    return m ? m[1] : null
                }""")
                if not url_key:
                    raise ValueError("urlKey를 찾을 수 없음 (세션 만료 또는 권한 없음)")
                log(f"urlKey 확인")

                # 3. 전체 상품 수 조회 (op=info)
                info_params = self._build_params(url_key, 1, 'info')
                info_url = f"https://admin.oclick.co.kr/sell/ProdMstSelect1_xml.jsp?{info_params}"
                info_xml = await page.evaluate("async (url) => (await fetch(url, {credentials:'same-origin'})).text()", info_url)

                total_match = re.search(r'total_count="(\d+)"|rows="(\d+)"|<total[^>]*>(\d+)</total>', info_xml)
                total_count = int(next(g for g in total_match.groups() if g) ) if total_match else 0
                total_pages = (total_count // PAGE_SIZE + 1) if total_count > 0 else MAX_PAGES
                log(f"전체 상품: {total_count or '확인 중'}개")

                # 4. 페이지 순회
                for page_num in range(1, min(total_pages, MAX_PAGES) + 1):
                    data_params = self._build_params(url_key, page_num, 'select')
                    data_url = f"https://admin.oclick.co.kr/sell/ProdMstSelect1_xml.jsp?{data_params}"
                    xml_text = await page.evaluate("async (url) => (await fetch(url, {credentials:'same-origin'})).text()", data_url)

                    row_count = 0
                    for row_m in re.finditer(r'<row[^>]*>([\s\S]*?)</row>', xml_text):
                        cells = _parse_oclick_xml_cells(row_m.group(0))
                        if len(cells) < 27:
                            continue
                        sku = cells[2]
                        name = cells[9]
                        if not sku or not name:
                            continue
                        raw_price = cells[25] or ''
                        raw_qty = cells[26] or ''
                        items.append({
                            'sku': sku,
                            'brand': cells[3],
                            'name': name,
                            'stock_status': cells[18],
                            'stock_qty': int(raw_qty.replace(',', '').replace('.', '') or 0) if raw_qty else 0,
                            'price': int(raw_price.replace(',', '').replace('.', '') or 0) or None if raw_price else None,
                        })
                        row_count += 1

                    log(f"{page_num}페이지: {row_count}개 (누적 {len(items)}개)")
                    if row_count < PAGE_SIZE:
                        break  # 마지막 페이지

                log(f"완료 — 총 {len(items)}개 상품")
                return items

            finally:
                await page.close()
                await browser.close()

    def _build_params(self, url_key, page_num, op):
        from urllib.parse import urlencode
        return urlencode({
            'in_PAGE_PG': str(page_num), 'urlKey': url_key,
            'in_PAGE_CNT': str(PAGE_SIZE),
            'in_SORT1': 'T.PRODID', 'in_SORT2': 'T.PRODID', 'in_STATUS': 'XXX',
            'in_PRODID': '', 'in_PRODNM': '', 'in_MPRODID': '', 'in_BRANDNO': '',
            'in_CLASSNO': '', 'in_SEXNO': '', 'in_SEX1NO': '', 'in_MCUSTID': '',
            'in_YYCD': '', 'in_SSCD': '', 'in_DATESTR': '', 'in_SDATE': '', 'in_EDATE': '',
            'in_BCODE': '', 'in_MBCODE': '', 'in_WAREID': '', 'in_GROUPNO': '', 'in_GROUPNOB': '',
            'in_PRTY': '', 'in_CNTPERBOX1': '', 'in_CNTPERBOX2': '', 'in_JAEGO': '', 'in_JAEGO1': '',
            'in_MEMO': '', 'in_VALSRC1': '', 'in_VALSRC2': '', 'in_PDETAIL': '', 'in_MADEIN': '',
            'in_IMGVIEW': 'N', 'op': op,
        })


class OclickSalesHandler(BaseCrawler):
    """Oclick 매출 수집 핸들러 — /repo/OrdeMstSelect3.jsp XML API"""

    async def handle(self, keyword, options, log_cb=None):
        def log(msg):
            if log_cb: log_cb(f"  [OclickSales] {msg}")

        company_code, user_id, password = _load_credentials(options)
        if not company_code or not user_id or not password:
            raise ValueError("Oclick 자격증명 없음 — station_settings에 oclick_* 등록 필요")

        # 날짜 정규화 (YYYY-MM-DD → YYYYMMDD)
        start_date = (options.get('start_date') or '').replace('-', '')
        end_date   = (options.get('end_date')   or '').replace('-', '')
        if not start_date or not end_date:
            from datetime import date, timedelta
            today = date.today()
            end_date   = today.strftime('%Y%m%d')
            start_date = (today - timedelta(days=30)).strftime('%Y%m%d')

        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser, ctx = await self.create_browser(pw)
            page = await ctx.new_page()
            orders = []

            try:
                # 1. 로그인
                log("로그인 중...")
                await page.goto('https://www.oclick.co.kr', wait_until='networkidle', timeout=20000)
                await page.fill('#in_usercu', company_code)
                await page.fill('#in_userid', user_id)
                await page.fill('#in_passwd', password)
                await page.click('button.bt_m_button01')
                await page.wait_for_load_state('networkidle', timeout=15000)
                if 'admin.oclick.co.kr' not in page.url:
                    raise ValueError(f"로그인 실패: {page.url}")
                log("로그인 성공")

                # 2. 매출 페이지 → urlKey 추출
                sales_url = (
                    f"https://admin.oclick.co.kr/repo/OrdeMstSelect3.jsp"
                    f"?in_SDATE={start_date}&in_EDATE={end_date}"
                    f"&in_KORDE=Y&in_KCANC=N&in_KREFN=N&in_KCHAN=N&in_KLOST=N"
                )
                await page.goto(sales_url, wait_until='networkidle', timeout=20000)
                content = await page.content()
                m = re.search(r'urlKey=(\[[^\]]+\]\[[^\]]+\]\[\d+\]\[[^\]]+\])', content)
                if not m:
                    raise ValueError("urlKey를 찾을 수 없음 (세션 만료 또는 권한 없음)")
                url_key = m.group(1)
                log(f"urlKey 확인 ({start_date}~{end_date})")

                # 3. 전체 건수 조회
                info_params = self._build_sales_params(url_key, start_date, end_date, 1, 'info')
                info_xml = await page.evaluate(
                    "async (url) => (await fetch(url, {credentials:'same-origin'})).text()",
                    f"https://admin.oclick.co.kr/repo/OrdeMstSelect3_xml.jsp?{info_params}"
                )
                total_match = re.search(r'total_count="(\d+)"|rows="(\d+)"', info_xml)
                total_count = int(next((g for g in total_match.groups() if g), 0)) if total_match else 0
                total_pages = min((total_count // SALES_PAGE_SIZE + 1) if total_count else SALES_MAX_PAGES, SALES_MAX_PAGES)
                log(f"전체 건수: {total_count or '확인 중'}건")

                # 4. 페이지 순회
                for page_num in range(1, total_pages + 1):
                    params = self._build_sales_params(url_key, start_date, end_date, page_num, 'select')
                    xml_text = await page.evaluate(
                        "async (url) => (await fetch(url, {credentials:'same-origin'})).text()",
                        f"https://admin.oclick.co.kr/repo/OrdeMstSelect3_xml.jsp?{params}"
                    )
                    row_count = 0
                    for row_m in re.finditer(r'<row[^>]*>([\s\S]*?)</row>', xml_text):
                        cells = _parse_oclick_xml_cells(row_m.group(0))
                        if len(cells) < 20:
                            continue
                        order_no = cells[3].strip()
                        if not order_no:
                            continue
                        raw_qty    = cells[18].replace(',', '').replace('.', '') or '0'
                        raw_amount = cells[19].replace(',', '').replace('.', '') or '0'
                        order_date_raw = cells[7].strip()  # "20260417 14:20:53"
                        orders.append({
                            'order_no':     order_no,
                            'order_date':   order_date_raw[:8] if order_date_raw else '',
                            'channel':      cells[2].strip(),
                            'order_status': cells[4].strip(),
                            'sku':          cells[12].strip() or None,
                            'product_name': cells[13].strip(),
                            'qty':          int(raw_qty)    if raw_qty.isdigit()    else 0,
                            'amount':       int(raw_amount) if raw_amount.isdigit() else 0,
                        })
                        row_count += 1

                    log(f"{page_num}페이지: {row_count}건 (누적 {len(orders)}건)")
                    if row_count < SALES_PAGE_SIZE:
                        break

                log(f"완료 — 총 {len(orders)}건")
                return orders

            finally:
                await page.close()
                await browser.close()

    def _build_sales_params(self, url_key, start_date, end_date, page_num, op):
        from urllib.parse import urlencode
        return urlencode({
            'in_SDATE': start_date, 'in_EDATE': end_date,
            'in_KORDE': 'Y', 'in_KCANC': 'N', 'in_KREFN': 'N', 'in_KCHAN': 'N', 'in_KLOST': 'N',
            'urlKey': url_key, 'in_PAGE_PG': str(page_num),
            'in_PAGE_CNT': str(SALES_PAGE_SIZE), 'op': op,
        })
