-- ============================================================
-- Phase 1: CrawlStation 워커 인프라
-- 실행: Supabase SQL Editor에서 실행
-- ============================================================

-- 1. workers 테이블 (워커 자동/수동 등록)
CREATE TABLE IF NOT EXISTS workers (
  id TEXT PRIMARY KEY,                        -- "worker-a1b2c3d4" (UUID 기반)
  name TEXT,                                  -- "서울사무실-PC" (hostname 기반, 변경 가능)
  os TEXT,                                    -- "Windows 11" / "macOS 15.3"
  hostname TEXT,                              -- 머신 hostname
  python_version TEXT,                        -- "3.12.3"
  status TEXT DEFAULT 'offline'               -- online/idle/crawling/blocked/offline
    CHECK (status IN ('online', 'idle', 'crawling', 'blocked', 'offline')),
  last_seen TIMESTAMPTZ,
  ip_address TEXT,
  current_task_id UUID,
  current_keyword TEXT,
  current_type TEXT,                          -- 현재 실행 중인 크롤링 타입
  total_processed INTEGER DEFAULT 0,
  error_count INTEGER DEFAULT 0,
  blocked_until TIMESTAMPTZ,
  registered_at TIMESTAMPTZ DEFAULT now(),
  registered_by TEXT DEFAULT 'auto'           -- "auto" / "manual"
    CHECK (registered_by IN ('auto', 'manual')),
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 2. worker_config 테이블 (Evasion Agent가 관리하는 설정)
CREATE TABLE IF NOT EXISTS worker_config (
  id TEXT PRIMARY KEY,                        -- "global" 또는 워커별 ID
  ua_pool TEXT[] DEFAULT ARRAY[
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0'
  ],
  typing_speed_min INTEGER DEFAULT 60,        -- ms
  typing_speed_max INTEGER DEFAULT 180,
  scroll_min INTEGER DEFAULT 200,             -- px
  scroll_max INTEGER DEFAULT 600,
  batch_size INTEGER DEFAULT 30,
  batch_rest_seconds INTEGER DEFAULT 180,     -- 배치 간 휴식 (초)
  keyword_delay_min INTEGER DEFAULT 15,       -- 키워드 간 딜레이 (초)
  keyword_delay_max INTEGER DEFAULT 30,
  typo_probability FLOAT DEFAULT 0.05,        -- 오타 확률
  scroll_back_probability FLOAT DEFAULT 0.4,  -- 역스크롤 확률
  updated_at TIMESTAMPTZ DEFAULT now(),
  updated_by TEXT DEFAULT 'manual'            -- "evasion_agent" / "manual"
);

-- 글로벌 기본 config 삽입
INSERT INTO worker_config (id) VALUES ('global')
ON CONFLICT (id) DO NOTHING;

-- 3. crawl_requests 확장 (기존 테이블에 컬럼 추가)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
    WHERE table_name = 'crawl_requests' AND column_name = 'assigned_worker') THEN
    ALTER TABLE crawl_requests ADD COLUMN assigned_worker TEXT;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
    WHERE table_name = 'crawl_requests' AND column_name = 'priority') THEN
    ALTER TABLE crawl_requests ADD COLUMN priority INTEGER DEFAULT 0;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
    WHERE table_name = 'crawl_requests' AND column_name = 'schedule_id') THEN
    ALTER TABLE crawl_requests ADD COLUMN schedule_id UUID;
  END IF;
END $$;

-- 4. 인덱스
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
CREATE INDEX IF NOT EXISTS idx_workers_last_seen ON workers(last_seen);
CREATE INDEX IF NOT EXISTS idx_crawl_requests_assigned ON crawl_requests(assigned_worker, status);
CREATE INDEX IF NOT EXISTS idx_crawl_requests_priority ON crawl_requests(priority DESC, created_at ASC);

-- 5. RLS (Row Level Security) — 서비스 키 사용 시 불필요하지만 안전장치
ALTER TABLE workers ENABLE ROW LEVEL SECURITY;
ALTER TABLE worker_config ENABLE ROW LEVEL SECURITY;

-- 서비스 키로 전체 접근 허용
CREATE POLICY IF NOT EXISTS "workers_service_all" ON workers FOR ALL USING (true);
CREATE POLICY IF NOT EXISTS "worker_config_service_all" ON worker_config FOR ALL USING (true);
