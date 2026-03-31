"""
CrawlStation 크롤링 워커 — 데스크톱 앱
GUI로 워커 상태 확인, 시작/정지, 업데이트, 로그 실시간 확인

실행: python3.12 app.py
"""
import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import asyncio
import os
import sys
import queue
import time
from datetime import datetime, timezone

# ── 워커 모듈 임포트 ─────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from worker import (
    VERSION, load_env, ensure_worker_id, collect_machine_info,
    register_worker, load_config, heartbeat, check_update, apply_update,
    process_request, WORKER_ID,
)
import worker as worker_module

load_env()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


class CrawlWorkerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"CrawlStation Worker v{VERSION}")
        self.root.geometry("700x520")
        self.root.resizable(True, True)
        self.root.configure(bg="#1a1a2e")

        self.sb = None
        self.config = {}
        self.running = False
        self.worker_thread = None
        self.log_queue = queue.Queue()
        self.task_count = 0
        self.error_count = 0

        self._build_ui()
        self._connect()
        self._poll_logs()

    # ── UI 구성 ────────────────────────────────
    def _build_ui(self):
        # 상단: 워커 정보
        header = tk.Frame(self.root, bg="#16213e", pady=8, padx=12)
        header.pack(fill=tk.X)

        tk.Label(header, text="CrawlStation Worker",
                 font=("Helvetica", 16, "bold"), fg="#e94560", bg="#16213e").pack(anchor=tk.W)

        self.info_label = tk.Label(header, text="연결 중...",
                                   font=("Helvetica", 10), fg="#a0a0b0", bg="#16213e")
        self.info_label.pack(anchor=tk.W)

        # 상태 바
        status_frame = tk.Frame(self.root, bg="#0f3460", pady=6, padx=12)
        status_frame.pack(fill=tk.X)

        self.status_dot = tk.Label(status_frame, text="●", font=("Helvetica", 14),
                                    fg="#666", bg="#0f3460")
        self.status_dot.pack(side=tk.LEFT)

        self.status_label = tk.Label(status_frame, text="정지됨",
                                      font=("Helvetica", 12, "bold"), fg="#ccc", bg="#0f3460")
        self.status_label.pack(side=tk.LEFT, padx=(6, 20))

        self.version_label = tk.Label(status_frame, text=f"v{VERSION}",
                                       font=("Helvetica", 10), fg="#a0a0b0", bg="#0f3460")
        self.version_label.pack(side=tk.LEFT)

        self.stats_label = tk.Label(status_frame, text="처리: 0  에러: 0",
                                     font=("Helvetica", 10), fg="#a0a0b0", bg="#0f3460")
        self.stats_label.pack(side=tk.RIGHT)

        # 버튼 바
        btn_frame = tk.Frame(self.root, bg="#1a1a2e", pady=8, padx=12)
        btn_frame.pack(fill=tk.X)

        self.start_btn = tk.Button(btn_frame, text="▶ 시작", font=("Helvetica", 11, "bold"),
                                    bg="#27ae60", fg="white", relief=tk.FLAT, padx=16, pady=4,
                                    command=self._start_worker, cursor="hand2")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.stop_btn = tk.Button(btn_frame, text="■ 정지", font=("Helvetica", 11, "bold"),
                                   bg="#c0392b", fg="white", relief=tk.FLAT, padx=16, pady=4,
                                   command=self._stop_worker, state=tk.DISABLED, cursor="hand2")
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.update_btn = tk.Button(btn_frame, text="↑ 업데이트 확인", font=("Helvetica", 10),
                                     bg="#2980b9", fg="white", relief=tk.FLAT, padx=12, pady=4,
                                     command=self._check_update, cursor="hand2")
        self.update_btn.pack(side=tk.RIGHT)

        # 로그 영역
        log_frame = tk.Frame(self.root, bg="#1a1a2e", padx=12, pady=6)
        log_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(log_frame, text="로그", font=("Helvetica", 10, "bold"),
                 fg="#a0a0b0", bg="#1a1a2e").pack(anchor=tk.W, pady=(0, 4))

        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, font=("Menlo", 10),
            bg="#0a0a1a", fg="#00ff88", insertbackground="#00ff88",
            relief=tk.FLAT, padx=8, pady=6, state=tk.DISABLED,
            height=15
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 로그 색상 태그
        self.log_text.tag_config("error", foreground="#ff6b6b")
        self.log_text.tag_config("success", foreground="#51cf66")
        self.log_text.tag_config("info", foreground="#74c0fc")
        self.log_text.tag_config("dim", foreground="#666")

    # ── 연결 ───────────────────────────────────
    def _connect(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            self._log("❌ .env에 SUPABASE_URL, SUPABASE_KEY 설정 필요", "error")
            self._set_status("설정 필요", "#e74c3c")
            return

        try:
            from supabase import create_client
            self.sb = create_client(SUPABASE_URL, SUPABASE_KEY)

            ensure_worker_id()
            info = collect_machine_info()
            wid = worker_module.WORKER_ID

            register_worker(self.sb)
            self.config = load_config(self.sb)

            self.info_label.config(
                text=f"ID: {wid}  |  {info['os']}  |  {info['hostname']}"
            )
            self._log(f"CrawlStation 연결 완료 — {wid}", "success")
            self._log(f"Config: 배치 {self.config.get('batch_size', 30)}개, "
                       f"딜레이 {self.config.get('keyword_delay_min', 15)}~"
                       f"{self.config.get('keyword_delay_max', 30)}초", "dim")
            self._set_status("대기", "#f39c12")
        except Exception as e:
            self._log(f"❌ 연결 실패: {e}", "error")
            self._set_status("연결 실패", "#e74c3c")

    # ── 시작/정지 ──────────────────────────────
    def _start_worker(self):
        if not self.sb:
            self._log("❌ Supabase 연결이 필요합니다", "error")
            return

        self.running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self._set_status("실행 중", "#27ae60")
        self._log("▶ 워커 시작", "info")

        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def _stop_worker(self):
        self.running = False
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self._set_status("정지됨", "#e74c3c")
        self._log("■ 워커 정지", "info")
        if self.sb:
            heartbeat(self.sb, "offline")

    def _worker_loop(self):
        """백그라운드 워커 루프"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        batch_count = 0

        while self.running:
            try:
                heartbeat(self.sb, "idle")

                # 1) 내게 할당된 작업
                res = self.sb.table("crawl_requests").select("*") \
                    .eq("assigned_worker", worker_module.WORKER_ID) \
                    .eq("status", "assigned") \
                    .order("priority", desc=True) \
                    .order("created_at") \
                    .limit(1).execute()

                task = None
                if res.data:
                    task = res.data[0]
                else:
                    # 2) 미할당 pending
                    res2 = self.sb.table("crawl_requests").select("*") \
                        .is_("assigned_worker", "null") \
                        .eq("status", "pending") \
                        .order("priority", desc=True) \
                        .order("created_at") \
                        .limit(1).execute()
                    if res2.data:
                        task = res2.data[0]
                        self.sb.table("crawl_requests").update({
                            "assigned_worker": worker_module.WORKER_ID,
                            "status": "assigned",
                        }).eq("id", task["id"]).execute()

                if task:
                    keyword = task["keyword"]
                    req_type = task["type"]
                    self._set_status(f"크롤링: {keyword[:20]}", "#3498db")
                    self._log(f"━━ [{req_type}] {keyword}", "info")

                    loop.run_until_complete(
                        process_request(self.sb, task, self.config, log_cb=self._log)
                    )

                    self.task_count += 1
                    self._update_stats()
                    self._set_status("실행 중", "#27ae60")
                    batch_count += 1

                    if batch_count >= self.config.get("batch_size", 30):
                        rest = self.config.get("batch_rest_seconds", 180)
                        self._log(f"😴 배치 완료 — {rest}초 휴식", "dim")
                        time.sleep(rest)
                        batch_count = 0
                        self.config = load_config(self.sb)
                    else:
                        delay_min = self.config.get("keyword_delay_min", 15)
                        delay_max = self.config.get("keyword_delay_max", 30)
                        import random
                        time.sleep(random.randint(delay_min, delay_max))
                else:
                    time.sleep(5)

            except Exception as e:
                self._log(f"⚠️ {e}", "error")
                self.error_count += 1
                self._update_stats()
                time.sleep(10)

        loop.close()

    # ── 업데이트 ───────────────────────────────
    def _check_update(self):
        if not self.sb:
            self._log("❌ Supabase 연결이 필요합니다", "error")
            return

        self._log("🔍 업데이트 확인 중...", "info")
        update = check_update(self.sb)

        if update:
            new_ver = update["version"]
            changelog = update.get("changelog", "")
            self._log(f"📦 새 버전: v{new_ver} — {changelog}", "info")

            if messagebox.askyesno("업데이트",
                                    f"새 버전 v{new_ver}이 있습니다.\n{changelog}\n\n업데이트 하시겠습니까?"):
                was_running = self.running
                if was_running:
                    self._stop_worker()

                self._log("🔄 업데이트 적용 중...", "info")
                if apply_update(self.sb, update):
                    self._log(f"✅ v{new_ver} 업데이트 완료!", "success")
                    self.version_label.config(text=f"v{new_ver}")
                    messagebox.showinfo("업데이트 완료",
                                        f"v{new_ver}으로 업데이트되었습니다.\n앱을 재시작해주세요.")
                else:
                    self._log("⚠️ 업데이트 실패", "error")

                if was_running:
                    self._start_worker()
        else:
            self._log("✅ 최신 버전입니다", "success")

    # ── UI 헬퍼 ────────────────────────────────
    def _set_status(self, text, color):
        self.root.after(0, lambda: self.status_label.config(text=text))
        self.root.after(0, lambda: self.status_dot.config(fg=color))

    def _update_stats(self):
        self.root.after(0, lambda: self.stats_label.config(
            text=f"처리: {self.task_count}  에러: {self.error_count}"
        ))

    def _log(self, msg, tag=None):
        """스레드 안전 로그 추가"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put((f"[{timestamp}] {msg}\n", tag))

    def _poll_logs(self):
        """메인 스레드에서 로그 큐 처리"""
        while not self.log_queue.empty():
            msg, tag = self.log_queue.get_nowait()
            self.log_text.config(state=tk.NORMAL)
            if tag:
                self.log_text.insert(tk.END, msg, tag)
            else:
                self.log_text.insert(tk.END, msg)
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)

        self.root.after(100, self._poll_logs)

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False
        if self.sb:
            try:
                heartbeat(self.sb, "offline")
            except Exception:
                pass
        self.root.destroy()


if __name__ == "__main__":
    app = CrawlWorkerApp()
    app.run()
