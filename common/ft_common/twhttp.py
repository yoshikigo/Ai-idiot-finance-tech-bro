"""HTTP helpers for Taiwanese public data sources (TWSE / TPEx / SITCA / TDCC / MIS / Google News).

- One shared httpx client, polite User-Agent, small in-memory + on-disk cache.
- TWSE-family certificates are known to fail Python's strict X.509 checks
  (VERIFY_X509_STRICT, default since Python 3.13). We keep verification on but
  clear that flag; only if the handshake still fails for one of the government
  hosts below do we retry once without verification (logged once).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import ssl
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger("ft.twhttp")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36 fin-terminal"
GOV_HOSTS = ("twse.com.tw", "tpex.org.tw", "sitca.org.tw", "tdcc.com.tw", "gov.tw")  # gov.tw: some 主計總處 hosts omit the intermediate cert
CACHE_DIR = Path(os.environ.get("FT_DATA", "/data")) / "cache" / "tw"

_ctx = ssl.create_default_context()
_ctx.verify_flags &= ~getattr(ssl, "VERIFY_X509_STRICT", 0)
_mem: dict[str, tuple[float, bytes]] = {}
_lock = threading.Lock()
_insecure_warned: set[str] = set()


def _key(method, url, params, data) -> str:
    raw = json.dumps([method, url, params or {}, data or {}], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()


def fetch(url: str, *, params: dict | None = None, data: dict | None = None, method: str = "GET",
          headers: dict | None = None, timeout: float = 30, ttl: float = 0, disk: bool = False) -> bytes:
    """GET/POST → bytes. `ttl` seconds of caching (memory; also disk when disk=True)."""
    k = _key(method, url, params, data)
    now = time.time()
    if ttl:
        with _lock:
            hit = _mem.get(k)
        if hit and now - hit[0] < ttl:
            return hit[1]
        if disk:
            f = CACHE_DIR / k
            if f.exists() and now - f.stat().st_mtime < ttl:
                body = f.read_bytes()
                with _lock:
                    _mem[k] = (f.stat().st_mtime, body)
                return body
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6", **(headers or {})}
    host = urlparse(url).hostname or ""

    def go(verify):
        with httpx.Client(verify=verify, timeout=timeout, follow_redirects=True, headers=h) as c:
            r = c.request(method, url, params=params, data=data)
            r.raise_for_status()
            return r.content

    try:
        body = go(_ctx)
    except httpx.ConnectError as e:
        if "CERTIFICATE" not in str(e).upper() or not host.endswith(GOV_HOSTS):
            raise
        if host not in _insecure_warned:
            _insecure_warned.add(host)
            log.warning("TLS verify failed for %s (%s); retrying without verification", host, e)
        body = go(False)
    if ttl:
        with _lock:
            _mem[k] = (now, body)
            if len(_mem) > 500:
                for old in sorted(_mem, key=lambda x: _mem[x][0])[:100]:
                    _mem.pop(old, None)
        if disk:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_DIR / (k + ".tmp")
            tmp.write_bytes(body)
            tmp.replace(CACHE_DIR / k)
    return body


def fetch_json(url: str, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8-sig"))


def decode_tw(body: bytes) -> str:
    """Taiwanese pages/CSVs come as UTF-8 (with/without BOM), Big5 or CP950."""
    for enc in ("utf-8-sig", "cp950", "big5hkscs"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")
