"""
Supabase REST API 클라이언트 — stdlib만 사용 (greenlet/httpx 불필요)
supabase Python SDK가 import 안 되는 환경에서 fallback으로 사용.
sb.table("X").select("*").eq("col", val).execute() 체이닝 패턴 호환.
"""
import json
import urllib.request
import urllib.error
import urllib.parse
import ctypes
import ctypes.util
import socket


def _refresh_dns():
    """macOS 장시간 실행 시 DNS 리졸버가 stale해지는 문제 해결.
    res_init()으로 /etc/resolv.conf를 재로드해 DNS 캐시를 초기화한다."""
    try:
        lib = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("resolv") or "libresolv.dylib"
        )
        lib.res_init()
    except Exception:
        pass


class SupabaseResponse:
    """supabase SDK의 execute() 반환값 호환"""
    def __init__(self, data=None, count=None, error=None):
        self.data = data or []
        self.count = count
        self.error = error


class QueryBuilder:
    """Supabase PostgREST 쿼리 빌더 — 체이닝 지원"""

    def __init__(self, client, table):
        self._client = client
        self._table = table
        self._method = "GET"
        self._body = None
        self._params = {}
        self._headers = {}
        self._select_cols = None
        self._filters = []
        self._order_cols = []
        self._limit_val = None
        self._single = False
        self._upsert = False
        self._count_only = False
        self._head = False

    def select(self, columns="*", count=None, head=False):
        self._method = "GET"
        self._select_cols = columns
        if count == "exact":
            self._headers["Prefer"] = "count=exact"
        if head:
            self._head = True
            self._count_only = True
        return self

    def insert(self, data):
        self._method = "POST"
        self._body = data if isinstance(data, list) else [data]
        self._headers["Prefer"] = "return=representation"
        return self

    def upsert(self, data, on_conflict=None):
        self._method = "POST"
        self._body = data if isinstance(data, list) else [data]
        prefer = "resolution=merge-duplicates,return=representation"
        if on_conflict:
            self._headers["Prefer"] = prefer
        else:
            self._headers["Prefer"] = prefer
        self._upsert = True
        return self

    def update(self, data):
        self._method = "PATCH"
        self._body = data
        self._headers["Prefer"] = "return=representation"
        return self

    def delete(self):
        self._method = "DELETE"
        self._headers["Prefer"] = "return=representation"
        return self

    def eq(self, col, val):
        self._filters.append(f"{col}=eq.{val}")
        return self

    def neq(self, col, val):
        self._filters.append(f"{col}=neq.{val}")
        return self

    def is_(self, col, val):
        self._filters.append(f"{col}=is.{val}")
        return self

    def in_(self, col, values):
        vals = ",".join(str(v) for v in values)
        self._filters.append(f"{col}=in.({vals})")
        return self

    def lt(self, col, val):
        self._filters.append(f"{col}=lt.{val}")
        return self

    def lte(self, col, val):
        self._filters.append(f"{col}=lte.{val}")
        return self

    def gt(self, col, val):
        self._filters.append(f"{col}=gt.{val}")
        return self

    def gte(self, col, val):
        self._filters.append(f"{col}=gte.{val}")
        return self

    def order(self, col, desc=False):
        direction = "desc" if desc else "asc"
        self._order_cols.append(f"{col}.{direction}")
        return self

    def limit(self, n):
        self._limit_val = n
        return self

    def single(self):
        self._single = True
        self._limit_val = 1
        return self

    def range(self, start, end):
        self._headers["Range"] = f"{start}-{end}"
        return self

    def execute(self):
        url = f"{self._client._url}/rest/v1/{self._table}"

        # 쿼리 파라미터 조립
        params = []
        if self._select_cols:
            params.append(f"select={self._select_cols}")
        for f in self._filters:
            params.append(f)
        if self._order_cols:
            params.append(f"order={','.join(self._order_cols)}")
        if self._limit_val is not None:
            params.append(f"limit={self._limit_val}")

        if params:
            url += "?" + "&".join(params)

        headers = {
            "apikey": self._client._key,
            "Authorization": f"Bearer {self._client._key}",
            "Content-Type": "application/json",
        }
        headers.update(self._headers)

        body = None
        if self._body is not None:
            body = json.dumps(self._body).encode("utf-8")

        for _attempt in range(2):
          try:
            req = urllib.request.Request(url, data=body, headers=headers, method=self._method)
            if self._head or self._count_only:
                req.method = "HEAD" if self._head else self._method

            with urllib.request.urlopen(req, timeout=30) as resp:
                content_range = resp.headers.get("Content-Range", "")
                if self._count_only and content_range:
                    # "0-9/100" → count=100
                    total = content_range.split("/")[-1] if "/" in content_range else "0"
                    return SupabaseResponse(data=[], count=int(total) if total != "*" else 0)

                raw = resp.read().decode("utf-8")
                if not raw or raw.strip() == "":
                    return SupabaseResponse(data=[])

                data = json.loads(raw)
                if self._single:
                    if isinstance(data, list) and data:
                        return SupabaseResponse(data=data[0])
                    return SupabaseResponse(data=None)
                return SupabaseResponse(data=data if isinstance(data, list) else [data])

          except socket.gaierror as e:
            # DNS 조회 실패 (macOS 장시간 실행 시 stale resolver) — res_init() 후 1회 재시도
            if _attempt == 0:
                _refresh_dns()
                continue
            return SupabaseResponse(data=[], error=str(e)[:200])
          except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8")[:200]
            except Exception:
                pass
            return SupabaseResponse(data=[], error=f"HTTP {e.code}: {body_text}")
          except Exception as e:
            return SupabaseResponse(data=[], error=str(e)[:200])
        return SupabaseResponse(data=[], error="DNS 재시도 실패")


class SupabaseREST:
    """supabase.create_client() 호환 REST 클라이언트"""

    def __init__(self, url, key):
        self._url = url.rstrip("/")
        self._key = key

    def table(self, name):
        return QueryBuilder(self, name)

    def rpc(self, func_name, params=None):
        """Supabase RPC 호출"""
        return RPCBuilder(self, func_name, params)


class RPCBuilder:
    def __init__(self, client, func_name, params):
        self._client = client
        self._func_name = func_name
        self._params = params or {}

    def execute(self):
        url = f"{self._client._url}/rest/v1/rpc/{self._func_name}"
        headers = {
            "apikey": self._client._key,
            "Authorization": f"Bearer {self._client._key}",
            "Content-Type": "application/json",
        }
        body = json.dumps(self._params).encode("utf-8")
        for _attempt in range(2):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                    return SupabaseResponse(data=json.loads(raw) if raw.strip() else [])
            except socket.gaierror:
                if _attempt == 0:
                    _refresh_dns()
                    continue
                return SupabaseResponse(data=[], error="DNS 재시도 실패")
            except Exception as e:
                return SupabaseResponse(data=[], error=str(e)[:200])
        return SupabaseResponse(data=[], error="DNS 재시도 실패")
