"""
닥톡(doctalk) 자동 송출 핸들러
posting_queue 테이블에서 pending 항목을 폴링하여 닥톡에 자동으로 질문/답변 등록

동작 방식:
  1. jk_accounts에서 assigned_worker = WORKER_ID 인 계정 조회
  2. posting_queue에서 해당 account_id + status='pending' 항목 1건 취득
  3. 닥톡(doctalk.co.kr) 로그인 → 질문 등록 → 답변 등록
  4. 완료: status='posted', result_url 기록
  5. 실패: status='failed', error_msg 기록 (retry_count 증가)

.env 설정:
  WORKER_ID=worker-xxxxxxxx       (워커 고유 ID)
  KIN_ACCOUNT_ID=ac_xxxxx         (선택: 특정 계정 ID 고정, 없으면 assigned_worker 자동 매칭)
  KIN_POST_HEADLESS=true          (false로 하면 브라우저 창 보임)
  KIN_POST_MAX_RETRY=3            (최대 재시도 횟수)
  KIN_POST_DELAY_MIN=30           (게시글 간 최소 딜레이 초)
  KIN_POST_DELAY_MAX=90           (게시글 간 최대 딜레이 초)
"""

import os
import re
import sys
import json
import random
import asyncio
import time
from datetime import datetime, timezone

from .base import BaseCrawler

DOCTALK_BASE = "https://doctalk.co.kr"
DOCTALK_LOGIN_URL = f"{DOCTALK_BASE}/user/login"
DOCTALK_QNA_URL = f"{DOCTALK_BASE}/qna"
DOCTALK_WRITE_URL = f"{DOCTALK_BASE}/qna/write"


class KinPostHandler(BaseCrawler):
    """닥톡 Q&A 자동 등록 핸들러"""

    def __init__(self, headless=None, config=None):
        _headless = os.environ.get("KIN_POST_HEADLESS", "true").lower() != "false"
        super().__init__(headless=headless if headless is not None else _headless, config=config)
        self.max_retry = int(os.environ.get("KIN_POST_MAX_RETRY", "3"))
        self.delay_min = int(os.environ.get("KIN_POST_DELAY_MIN", "30"))
        self.delay_max = int(os.environ.get("KIN_POST_DELAY_MAX", "90"))

    # ── 외부 진입점 (worker.py의 process_request 호환) ─────────────
    async def handle(self, keyword, options, log_cb=None):
        """
        worker.py의 crawl_requests 기반 handle() 호환 인터페이스.
        options에 account_data, posting_queue_id 등이 담겨 옴.
        직접 호출보다는 poll_and_post()로 사용하는 것이 권장.
        """
        acct = options.get("account_data", {})
        item = options.get("queue_item", {})
        if not acct or not item:
            return {"error": "account_data 또는 queue_item 누락"}
        result = await self._post_one(acct, item, log_cb=log_cb)
        return result

    # ── 메인 루프: polling → 취득 → 등록 ──────────────────────────
    async def poll_and_post(self, sb, worker_id: str, log_cb=None):
        """
        Supabase에서 이 워커에 배정된 계정의 pending 항목을 1건 처리.
        worker.py 루프에서 주기적으로 호출됨.
        """
        if log_cb: log_cb("  📮 KinPost: 송출 대기열 확인 중...")

        # 1. 배정된 계정 찾기 (data JSONB에서 assignedWorker 필드 확인)
        account_id_override = os.environ.get("KIN_ACCOUNT_ID", "")
        acct = None
        if account_id_override:
            res = sb.table("jk_accounts").select("id,data").eq("id", account_id_override).limit(1).execute()
            if res.data:
                acct = res.data[0].get("data") or {}
                acct["_row_id"] = res.data[0]["id"]
        else:
            # assignedWorker 필드가 data JSONB에 저장됨
            res = sb.table("jk_accounts").select("id,data").execute()
            for row in res.data or []:
                d = row.get("data") or {}
                if d.get("assignedWorker") == worker_id and not d.get("posting_paused"):
                    acct = d
                    acct["_row_id"] = row["id"]
                    break

        if not acct:
            if log_cb: log_cb("  ⚠️ KinPost: 배정된 계정 없음")
            return None
        if not acct.get("kinLoginId") or not acct.get("kinLoginPw"):
            if log_cb: log_cb(f"  ⚠️ KinPost: 계정 {acct.get('name','?')}에 로그인 정보 없음")
            return None

        # 2. 일일 한도 체크
        daily_limit = int(acct.get("dailyPostLimit") or 5)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        posted_today_res = sb.table("posting_queue")\
            .select("id", count="exact")\
            .eq("account_id", acct["_row_id"])\
            .eq("status", "posted")\
            .gte("posted_at", today_start)\
            .execute()
        posted_today = posted_today_res.count or 0
        if posted_today >= daily_limit:
            if log_cb: log_cb(f"  ℹ️ KinPost: {acct.get('name','?')} 오늘 한도 도달 ({posted_today}/{daily_limit})")
            return None

        # 3. pending 항목 1건 취득 (retry 제한 미초과)
        max_retry = self.max_retry
        res = sb.table("posting_queue")\
            .select("*")\
            .eq("account_id", acct["_row_id"])\
            .eq("status", "pending")\
            .lt("retry_count", max_retry)\
            .order("priority", desc=False)\
            .order("created_at")\
            .limit(1)\
            .execute()
        items = res.data or []
        if not items:
            if log_cb: log_cb(f"  ℹ️ KinPost: {acct.get('name','?')} 대기 항목 없음")
            return None

        item = items[0]
        queue_id = item["id"]

        # 4. assigned로 선점
        sb.table("posting_queue").update({
            "status": "assigned",
            "worker_id": worker_id,
        }).eq("id", queue_id).eq("status", "pending").execute()

        if log_cb: log_cb(f"  🚀 KinPost: [{acct.get('name','?')}] {item.get('title','')[:40]}... 송출 시작")

        # 5. 실제 등록
        result = await self._post_one(acct, item, log_cb=log_cb)

        # 6. 결과 기록
        if result.get("success"):
            sb.table("posting_queue").update({
                "status": "posted",
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "result_url": result.get("result_url") or "",
            }).eq("id", queue_id).execute()
            if log_cb: log_cb(f"  ✅ KinPost: 송출 완료 → {result.get('result_url','')}")
        else:
            retry_count = int(item.get("retry_count") or 0) + 1
            new_status = "failed" if retry_count >= max_retry else "pending"
            sb.table("posting_queue").update({
                "status": new_status,
                "error_msg": result.get("error", "알 수 없는 오류")[:500],
                "retry_count": retry_count,
                "worker_id": None,
            }).eq("id", queue_id).execute()
            if log_cb: log_cb(f"  ❌ KinPost: 실패 ({retry_count}/{max_retry}) → {result.get('error','')}")

        return result

    # ── 실제 브라우저 송출 ─────────────────────────────────────────
    async def _post_one(self, acct: dict, item: dict, log_cb=None) -> dict:
        """닥톡에 질문 + 답변 1건 등록. 성공 시 result_url 반환."""
        from playwright.async_api import async_playwright

        login_id = acct.get("kinLoginId", "")
        login_pw = acct.get("kinLoginPw", "")
        title = item.get("title", "")
        question_text = item.get("question_text", "")
        answer_text = item.get("answer_text", "")
        keyword = item.get("keyword", "")

        if not login_id or not login_pw:
            return {"success": False, "error": "로그인 정보 없음"}
        if not title or not question_text or not answer_text:
            return {"success": False, "error": "콘텐츠 내용 없음"}

        try:
            async with async_playwright() as pw:
                browser, ctx = await self.create_browser(pw)
                page = await ctx.new_page()

                # ── 로그인 ──────────────────────────────
                if log_cb: log_cb("     🔐 닥톡 로그인 중...")
                try:
                    await self._login(page, login_id, login_pw)
                    if log_cb: log_cb("     ✅ 로그인 완료")
                except Exception as e:
                    await browser.close()
                    return {"success": False, "error": f"로그인 실패: {e}"}

                # ── 자연스러운 워밍업 (랜덤 메인 페이지 탐색) ──
                await self._warmup_doctalk(page, log_cb)

                # ── 질문 등록 ────────────────────────────
                if log_cb: log_cb("     ✍️ 질문 등록 중...")
                try:
                    question_url = await self._write_question(page, title, question_text, keyword)
                    if log_cb: log_cb(f"     ✅ 질문 등록: {question_url}")
                except Exception as e:
                    await browser.close()
                    return {"success": False, "error": f"질문 등록 실패: {e}"}

                # ── 딜레이 (질문 인식 대기) ──────────────
                wait_sec = random.randint(15, 40)
                if log_cb: log_cb(f"     ⏳ {wait_sec}초 대기 (질문 인식)...")
                await asyncio.sleep(wait_sec)

                # ── 답변 등록 ────────────────────────────
                if log_cb: log_cb("     💬 답변 등록 중...")
                try:
                    result_url = await self._write_answer(page, question_url, answer_text)
                    if log_cb: log_cb(f"     ✅ 답변 등록: {result_url}")
                except Exception as e:
                    await browser.close()
                    # 질문은 올라갔으므로 질문 URL을 result_url로 기록
                    return {"success": False, "error": f"답변 등록 실패: {e}", "result_url": question_url}

                await browser.close()
                return {"success": True, "result_url": result_url or question_url}

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 닥톡 로그인 ───────────────────────────────────────────────
    async def _login(self, page, login_id: str, login_pw: str):
        await page.goto(DOCTALK_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(random.randint(800, 1800))

        # 네이버 로그인 버튼 클릭 (닥톡은 네이버 OAuth 사용)
        naver_btn = await page.query_selector('a[href*="naver"], button:has-text("네이버"), .btn-naver, [class*="naver"]')
        if naver_btn:
            await naver_btn.click()
            await page.wait_for_timeout(random.randint(1500, 3000))

        # 네이버 로그인 페이지 처리
        await page.wait_for_url(re.compile(r"naver\.com.*login|nid\.naver"), timeout=15000)
        await page.wait_for_timeout(random.randint(500, 1200))

        # 아이디 입력
        id_input = await page.wait_for_selector('#id, input[name="id"], #username', timeout=10000)
        await id_input.click()
        await id_input.fill("")
        for ch in login_id:
            await page.keyboard.type(ch, delay=random.randint(60, 150))
        await page.wait_for_timeout(random.randint(300, 700))

        # 비밀번호 입력
        pw_input = await page.wait_for_selector('#pw, input[name="pw"], #password', timeout=5000)
        await pw_input.click()
        for ch in login_pw:
            await page.keyboard.type(ch, delay=random.randint(50, 130))
        await page.wait_for_timeout(random.randint(400, 900))

        # 로그인 버튼
        login_btn = await page.query_selector(r'#log\.login, button[type="submit"], .btn_login')
        if login_btn:
            await login_btn.click()
        else:
            await pw_input.press("Enter")

        # 2차 인증 또는 닥톡 복귀 대기
        await page.wait_for_timeout(3000)
        try:
            await page.wait_for_url(re.compile(r"doctalk\.co\.kr"), timeout=20000)
        except Exception:
            # 2차 인증 팝업이 있는 경우 — 설정에서 비활성화 권장
            current = page.url
            if "naver" in current:
                raise Exception(f"네이버 2차인증 필요. 현재 URL: {current}")

    # ── 닥톡 워밍업 ──────────────────────────────────────────────
    async def _warmup_doctalk(self, page, log_cb=None):
        """메인 페이지 → Q&A 목록 탐색 (자연스러운 행동)"""
        if random.random() < 0.6:
            try:
                await page.goto(DOCTALK_QNA_URL, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(random.randint(1500, 4000))
                await self.human_scroll(page, times=random.randint(2, 4))
                # 랜덤 Q&A 클릭 후 읽기
                if random.random() < 0.4:
                    links = await page.query_selector_all('a[href*="/qna/"]')
                    if links:
                        target = random.choice(links[:10])
                        try:
                            await target.click(timeout=5000)
                            await page.wait_for_timeout(random.randint(3000, 7000))
                            await self.human_scroll(page, times=random.randint(1, 3))
                            await page.go_back(timeout=10000)
                            await page.wait_for_timeout(random.randint(500, 1500))
                        except Exception:
                            pass
            except Exception:
                pass

    # ── 질문 작성 ─────────────────────────────────────────────────
    async def _write_question(self, page, title: str, body: str, keyword: str = "") -> str:
        """질문 글 등록 → 등록된 URL 반환"""
        await page.goto(DOCTALK_WRITE_URL, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(random.randint(1000, 2500))

        # 제목 입력
        title_el = await page.wait_for_selector(
            'input[name="title"], input[placeholder*="제목"], #title, .title-input',
            timeout=10000
        )
        await title_el.click()
        await title_el.fill("")
        await page.wait_for_timeout(random.randint(200, 500))
        for ch in title:
            await page.keyboard.type(ch, delay=random.randint(40, 100))
        await page.wait_for_timeout(random.randint(300, 700))

        # 카테고리/질환 선택 (있으면)
        await self._select_category(page, keyword)

        # 본문 입력
        body_el = await page.wait_for_selector(
            'textarea[name="content"], textarea[placeholder*="질문"], #content, .content-input, textarea',
            timeout=10000
        )
        await body_el.click()
        await page.wait_for_timeout(random.randint(300, 600))

        # 긴 텍스트는 fill로 처리 (타이핑 시뮬레이션 대신)
        if len(body) > 300:
            await body_el.fill(body)
        else:
            for ch in body:
                await page.keyboard.type(ch, delay=random.randint(30, 80))

        await page.wait_for_timeout(random.randint(500, 1200))

        # 등록 버튼
        submit_btn = await page.query_selector(
            'button[type="submit"], button:has-text("등록"), button:has-text("작성"), .btn-submit, .submit-btn'
        )
        if not submit_btn:
            raise Exception("등록 버튼을 찾을 수 없음")

        await submit_btn.click()
        await page.wait_for_timeout(random.randint(2000, 4000))

        # 등록 완료 후 URL 확인
        current_url = page.url
        if "write" in current_url or "error" in current_url:
            # 에러 메시지 확인
            err_el = await page.query_selector('.error-msg, .alert, [class*="error"]')
            err_msg = await err_el.inner_text() if err_el else "등록 실패 (URL 변경 없음)"
            raise Exception(err_msg)

        return current_url

    # ── 카테고리 선택 ─────────────────────────────────────────────
    async def _select_category(self, page, keyword: str):
        """키워드 기반으로 카테고리/질환 분류 선택 시도 (선택적)"""
        try:
            # 카테고리 드롭다운이 있으면 선택 시도
            cat_sel = await page.query_selector('select[name="category"], select[name="disease_type"], #category')
            if not cat_sel:
                return

            # 키워드에서 질환명 추출하여 가장 유사한 옵션 선택
            options = await cat_sel.query_selector_all("option")
            if not options:
                return

            best_opt = None
            kw_lower = keyword.lower()
            for opt in options:
                val = await opt.get_attribute("value") or ""
                text = (await opt.inner_text()).strip()
                if val and kw_lower and (kw_lower in text.lower() or text.lower() in kw_lower):
                    best_opt = val
                    break

            if best_opt:
                await cat_sel.select_option(best_opt)
                await page.wait_for_timeout(random.randint(300, 700))
        except Exception:
            pass

    # ── 답변 작성 ─────────────────────────────────────────────────
    async def _write_answer(self, page, question_url: str, answer_text: str) -> str:
        """질문 페이지에서 전문가 답변 등록"""
        await page.goto(question_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(random.randint(1500, 3500))
        await self.human_scroll(page, times=random.randint(1, 3))

        # 답변 입력창 찾기
        answer_el = await page.query_selector(
            'textarea[name="answer"], textarea[placeholder*="답변"], #answer, .answer-input, '
            'textarea[name="comment"], .reply-input textarea'
        )
        if not answer_el:
            # 답변 버튼 클릭 후 textarea 대기
            ans_btn = await page.query_selector('button:has-text("답변"), .btn-answer, a:has-text("답변하기")')
            if ans_btn:
                await ans_btn.click()
                await page.wait_for_timeout(random.randint(800, 1500))
                answer_el = await page.wait_for_selector(
                    'textarea[name="answer"], textarea[placeholder*="답변"], .answer-textarea',
                    timeout=8000
                )

        if not answer_el:
            raise Exception("답변 입력창을 찾을 수 없음")

        await answer_el.click()
        await page.wait_for_timeout(random.randint(300, 700))

        if len(answer_text) > 300:
            await answer_el.fill(answer_text)
        else:
            for ch in answer_text:
                await page.keyboard.type(ch, delay=random.randint(30, 80))

        await page.wait_for_timeout(random.randint(600, 1400))

        # 답변 등록 버튼
        submit_btn = await page.query_selector(
            'button[type="submit"]:near(textarea), button:has-text("답변 등록"), '
            'button:has-text("등록"), .btn-submit, .answer-submit'
        )
        if not submit_btn:
            # 폼 submit 직접 시도
            await answer_el.press("Control+Enter")
        else:
            await submit_btn.click()

        await page.wait_for_timeout(random.randint(2000, 4000))
        return page.url
