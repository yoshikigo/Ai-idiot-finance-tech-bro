"""我的持股 — per-user accounts and holdings.

* passwords: scrypt (n=2^14, r=8, p=1) with a per-user salt; never stored or logged in clear
* sessions: random 256-bit token in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored;
  30-day sliding expiry; logout / password change revoke them
* every holdings query is scoped by the session's user id — there is no endpoint that takes a user id
* state-changing calls need the X-FT header (a cross-site form or <img> cannot send it) on top of SameSite
* repeated wrong passwords lock that username for 5 minutes
* optional TOTP (RFC 6238: SHA-1, 6 digits, 30 s, ±1 step, a code is accepted once) with 8 single-use recovery
  codes (stored hashed); FT_PUBLIC_TOTP=required makes it mandatory for logins over the public URL
* the database file (users.sqlite under FT_DATA) is created 0600

Admin from the host (forgotten password, close sign-up, list users):
  podman exec -it fin-terminal python /app/terminal/auth.py list
  podman exec -it fin-terminal python /app/terminal/auth.py reset-password <username>
  podman exec -it fin-terminal python /app/terminal/auth.py add <username>
  podman exec -it fin-terminal python /app/terminal/auth.py delete <username>
  podman exec -it fin-terminal python /app/terminal/auth.py totp <username>       set up TOTP (QR in the terminal)
  podman exec -it fin-terminal python /app/terminal/auth.py totp-off <username>   lost the phone and the codes
"""
from __future__ import annotations

import base64
import datetime as dt
import struct
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

DATA = Path(os.environ.get("FT_DATA", "/data"))
DB = DATA / "users.sqlite"
COOKIE = "ft_session"
SESSION_DAYS = 30
SIGNUP = os.environ.get("FT_SIGNUP", "open").strip().lower()  # open | off
# Public exposure (Cloudflare Tunnel etc.): requests for these hostnames — or any request carrying Cloudflare's
# CF-Connecting-IP header — must log in for everything, and cannot sign up unless FT_PUBLIC_SIGNUP=open.
PUBLIC_HOSTS = {h.strip().lower() for h in os.environ.get("FT_PUBLIC_HOSTS", "").split(",") if h.strip()}
PUBLIC_SIGNUP = os.environ.get("FT_PUBLIC_SIGNUP", "off").strip().lower() == "open"
PUBLIC_TOTP = os.environ.get("FT_PUBLIC_TOTP", "optional").strip().lower()  # optional | required
TOTP_ISSUER = os.environ.get("FT_TOTP_ISSUER", "FT 金融終端")


def is_public(req: Request) -> bool:
    if req.headers.get("cf-connecting-ip"):  # only ever makes a request *more* restricted, so spoofing it is harmless
        return True
    host = (req.headers.get("host") or "").split(":")[0].strip().lower()
    return host in PUBLIC_HOSTS


def signup_open(req: Request) -> bool:
    return SIGNUP != "off" and (PUBLIC_SIGNUP or not is_public(req))
_lock = threading.Lock()
_fails: dict[str, list[float]] = {}

router = APIRouter()
quotes_fn = None       # set by app: list[str] -> list[dict]
fund_fn = None         # set by app: fund id -> summary dict | None
chains_fn = None       # set by app: symbol -> [node names]
name_fn = None         # set by app: symbol -> display name


# ------------------------------------------------------------------ storage
def _conn() -> sqlite3.Connection:
    new = not DB.exists()
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=30)
    if new:
        try:
            os.chmod(DB, 0o600)
        except OSError:
            pass
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
        pw TEXT NOT NULL, created TEXT NOT NULL, last_login TEXT);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created TEXT NOT NULL, expires REAL NOT NULL, agent TEXT);
    CREATE TABLE IF NOT EXISTS holdings(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        symbol TEXT NOT NULL, shares REAL NOT NULL, cost REAL NOT NULL, opened TEXT, note TEXT,
        created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS holdings_user ON holdings(user_id);
    CREATE TABLE IF NOT EXISTS dca_plans(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        symbol TEXT NOT NULL, amount REAL NOT NULL, freq TEXT NOT NULL, days TEXT NOT NULL, start TEXT NOT NULL, end_date TEXT,
        fee_pct REAL NOT NULL DEFAULT 0.1425, min_fee REAL NOT NULL DEFAULT 1, note TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS dca_user ON dca_plans(user_id);
    CREATE TABLE IF NOT EXISTS totp(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        secret TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, last_step INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS totp_recovery(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        code_hash TEXT NOT NULL, used TEXT);
    """)
    return c


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    d = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$14$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(d).decode()


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, want = stored.split("$")
        d = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=2 ** int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(d, base64.b64decode(want))
    except Exception:  # noqa: BLE001
        return False


_DUMMY = hash_pw(secrets.token_hex(8))  # equal work for unknown usernames (no user enumeration by timing)
USER_RE = re.compile(r"^[\w.\-]{3,32}$")


def _validate(username: str, password: str):
    if not USER_RE.match(username or ""):
        raise HTTPException(400, "帳號 3–32 字，可用中英文、數字、底線、點、減號")
    if len(password or "") < 8:
        raise HTTPException(400, "密碼至少 8 個字")
    if len(password) > 256:
        raise HTTPException(400, "密碼太長")


def create_user(username: str, password: str) -> int:
    _validate(username, password)
    with _lock, _conn() as c:
        try:
            cur = c.execute("INSERT INTO users(username,pw,created) VALUES(?,?,?)", (username, hash_pw(password), _now()))
        except sqlite3.IntegrityError as e:
            raise HTTPException(409, "這個帳號已經有人使用") from e
        return cur.lastrowid


def _tok_hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _new_session(user_id: int, agent: str) -> str:
    tok = secrets.token_urlsafe(32)
    with _lock, _conn() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO sessions(token,user_id,created,expires,agent) VALUES(?,?,?,?,?)",
                  (_tok_hash(tok), user_id, _now(), time.time() + SESSION_DAYS * 86400, (agent or "")[:160]))
        c.execute("UPDATE users SET last_login=? WHERE id=?", (_now(), user_id))
    return tok


def current_user(req: Request) -> dict | None:
    tok = req.cookies.get(COOKIE)
    if not tok:
        return None
    th = _tok_hash(tok)
    with _conn() as c:
        row = c.execute("SELECT u.id, u.username, s.expires FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                        (th,)).fetchone()
        if not row or row[2] < time.time():
            return None
        if row[2] - time.time() < (SESSION_DAYS - 1) * 86400:  # sliding expiry, at most one write a day
            c.execute("UPDATE sessions SET expires=? WHERE token=?", (time.time() + SESSION_DAYS * 86400, th))
    return {"id": row[0], "username": row[1]}


def require_user(req: Request) -> dict:
    u = current_user(req)
    if not u:
        raise HTTPException(401, "請先登入")
    return u


def _csrf(req: Request):
    # a custom header forces a CORS preflight, which this server never grants → other sites cannot post here
    if req.headers.get("x-ft") != "1":
        raise HTTPException(403, "缺少 X-FT 標頭")


def _secure(req: Request) -> bool:
    return req.url.scheme == "https" or req.headers.get("x-forwarded-proto", "").lower() == "https"


def _set_cookie(resp: Response, req: Request, tok: str):
    resp.set_cookie(COOKIE, tok, max_age=SESSION_DAYS * 86400, httponly=True, samesite="strict", secure=_secure(req), path="/")


def _locked(username: str) -> bool:
    now = time.time()
    if len(_fails) > 5000:
        _fails.clear()
    xs = [t for t in _fails.get(username.lower(), []) if now - t < 300]
    _fails[username.lower()] = xs
    return len(xs) >= 5


def _no_store(resp: Response):
    resp.headers["Cache-Control"] = "no-store"


# ------------------------------------------------------------------ TOTP
def totp_code(secret: str, step: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    d = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = d[-1] & 15
    return f"{(struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def _totp_row(c, uid: int):
    return c.execute("SELECT secret, enabled, last_step FROM totp WHERE user_id=?", (uid,)).fetchone()


def totp_enabled(uid: int) -> bool:
    with _conn() as c:
        r = _totp_row(c, uid)
    return bool(r and r[1])


def _rc_hash(code: str) -> str:
    return hashlib.sha256(re.sub(r"[^0-9a-z]", "", code.lower()).encode()).hexdigest()


def check_second_factor(uid: int, code: str, pending: bool = False) -> bool:
    """6-digit TOTP (each time step accepted once, ±30 s drift) or an unused recovery code."""
    code = (code or "").strip().replace(" ", "")
    now_step = int(time.time()) // 30
    with _lock, _conn() as c:
        r = _totp_row(c, uid)
        if not r or (not r[1] and not pending):
            return False
        secret, _, last = r
        if re.fullmatch(r"\d{6}", code):
            for st in (now_step - 1, now_step, now_step + 1):
                if st > last and hmac.compare_digest(totp_code(secret, st), code):
                    c.execute("UPDATE totp SET last_step=? WHERE user_id=?", (st, uid))
                    return True
            return False
        if pending:
            return False
        h = _rc_hash(code)
        row = c.execute("SELECT id FROM totp_recovery WHERE user_id=? AND code_hash=? AND used IS NULL", (uid, h)).fetchone()
        if row:
            c.execute("UPDATE totp_recovery SET used=? WHERE id=?", (_now(), row[0]))
            return True
    return False


def totp_begin(uid: int, username: str) -> dict:
    """New (not yet active) secret; the old one, if any, keeps working until the new one is confirmed."""
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    with _lock, _conn() as c:
        r = _totp_row(c, uid)
        if r and r[1]:
            c.execute("CREATE TABLE IF NOT EXISTS totp_pending(user_id INTEGER PRIMARY KEY, secret TEXT NOT NULL)")
            c.execute("INSERT OR REPLACE INTO totp_pending VALUES(?,?)", (uid, secret))
        else:
            c.execute("INSERT OR REPLACE INTO totp(user_id, secret, enabled, last_step, created) VALUES(?,?,0,0,?)",
                      (uid, secret, _now()))
    from urllib.parse import quote
    uri = (f"otpauth://totp/{quote(TOTP_ISSUER)}:{quote(username)}?secret={secret}&issuer={quote(TOTP_ISSUER)}"
           "&algorithm=SHA1&digits=6&period=30")
    return {"secret": secret, "uri": uri, "qr_svg": _qr_svg(uri)}


def _qr_svg(text: str) -> str | None:
    try:
        import io

        import segno
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#000", light="#fff", xmldecl=False)
        return buf.getvalue().decode()
    except Exception:  # noqa: BLE001
        return None


def totp_confirm(uid: int, code: str) -> list[str] | None:
    """Activate the pending secret if `code` matches it; returns fresh recovery codes (shown once)."""
    with _lock, _conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS totp_pending(user_id INTEGER PRIMARY KEY, secret TEXT NOT NULL)")
        pend = c.execute("SELECT secret FROM totp_pending WHERE user_id=?", (uid,)).fetchone()
        r = _totp_row(c, uid)
        secret = pend[0] if pend else (r[0] if r and not r[1] else None)
    if not secret:
        return None
    now_step = int(time.time()) // 30
    st = next((x for x in (now_step - 1, now_step, now_step + 1)
               if hmac.compare_digest(totp_code(secret, x), (code or "").strip().replace(" ", ""))), None)
    if st is None:
        return None
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(8)]
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO totp(user_id, secret, enabled, last_step, created) VALUES(?,?,1,?,?)",
                  (uid, secret, st, _now()))
        c.execute("DELETE FROM totp_pending WHERE user_id=?", (uid,))
        c.execute("DELETE FROM totp_recovery WHERE user_id=?", (uid,))
        c.executemany("INSERT INTO totp_recovery(user_id, code_hash) VALUES(?,?)", [(uid, _rc_hash(x)) for x in codes])
    return codes


def totp_disable(uid: int):
    with _lock, _conn() as c:
        c.execute("DELETE FROM totp WHERE user_id=?", (uid,))
        c.execute("DELETE FROM totp_recovery WHERE user_id=?", (uid,))
        c.execute("CREATE TABLE IF NOT EXISTS totp_pending(user_id INTEGER PRIMARY KEY, secret TEXT NOT NULL)")
        c.execute("DELETE FROM totp_pending WHERE user_id=?", (uid,))


def _recovery_left(uid: int) -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM totp_recovery WHERE user_id=? AND used IS NULL", (uid,)).fetchone()[0]


# ------------------------------------------------------------------ auth endpoints
@router.get("/api/auth/me")
def me(req: Request, resp: Response):
    _no_store(resp)
    u = current_user(req)
    return {"user": u and {"username": u["username"], "totp": totp_enabled(u["id"]),
                           "recovery_left": _recovery_left(u["id"]) if totp_enabled(u["id"]) else None},
            "signup": signup_open(req), "public": is_public(req),
            "totp_required": is_public(req) and PUBLIC_TOTP == "required"}


@router.post("/api/auth/register")
async def register(req: Request):
    _csrf(req)
    if not signup_open(req):
        raise HTTPException(403, "已關閉註冊；請管理者用 auth.py add 建立帳號" if SIGNUP == "off" else
                            "從公開網址不能註冊；請在 tailnet（或本機）註冊，或管理者用 auth.py add 建立帳號")
    b = await req.json()
    uid = create_user((b.get("username") or "").strip(), b.get("password") or "")
    resp = JSONResponse({"user": {"username": (b.get("username") or "").strip()}})
    _set_cookie(resp, req, _new_session(uid, req.headers.get("user-agent", "")))
    _no_store(resp)
    return resp


@router.post("/api/auth/login")
async def login(req: Request):
    _csrf(req)
    b = await req.json()
    username, pw = (b.get("username") or "").strip(), b.get("password") or ""
    if _locked(username):
        raise HTTPException(429, "密碼錯誤太多次，請 5 分鐘後再試")
    with _conn() as c:
        row = c.execute("SELECT id, username, pw FROM users WHERE username=?", (username,)).fetchone()
    ok = check_pw(pw, row[2] if row else _DUMMY) and row is not None
    if not ok:
        _fails.setdefault(username.lower(), []).append(time.time())
        raise HTTPException(401, "帳號或密碼錯誤")
    if totp_enabled(row[0]):
        code = str(b.get("code") or "").strip()
        if not code:  # password was right: ask for the second factor (no failure counted)
            return JSONResponse({"detail": "請輸入驗證器 App 的 6 位數驗證碼", "need_totp": True}, status_code=401)
        if not check_second_factor(row[0], code):
            _fails.setdefault(username.lower(), []).append(time.time())
            return JSONResponse({"detail": "驗證碼錯誤或已用過", "need_totp": True}, status_code=401)
    elif is_public(req) and PUBLIC_TOTP == "required":
        raise HTTPException(403, "從公開網址登入需要兩步驟驗證：請先在本機或 tailnet 登入，到「持股 → 帳號 → 兩步驟驗證」設定（或主機上 auth.py totp <帳號>）")
    _fails.pop(username.lower(), None)
    resp = JSONResponse({"user": {"username": row[1]}})
    _set_cookie(resp, req, _new_session(row[0], req.headers.get("user-agent", "")))
    _no_store(resp)
    return resp


@router.post("/api/auth/logout")
def logout(req: Request):
    _csrf(req)
    tok = req.cookies.get(COOKIE)
    if tok:
        with _lock, _conn() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (_tok_hash(tok),))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


@router.post("/api/auth/totp/setup")
async def totp_setup(req: Request):
    _csrf(req)
    u = require_user(req)
    b = await req.json()
    with _conn() as c:
        row = c.execute("SELECT pw FROM users WHERE id=?", (u["id"],)).fetchone()
    if not check_pw(b.get("password") or "", row[0]):
        raise HTTPException(401, "密碼錯誤")
    resp = JSONResponse(totp_begin(u["id"], u["username"]))
    _no_store(resp)
    return resp


@router.post("/api/auth/totp/enable")
async def totp_enable(req: Request):
    _csrf(req)
    u = require_user(req)
    b = await req.json()
    codes = totp_confirm(u["id"], str(b.get("code") or ""))
    if not codes:
        raise HTTPException(400, "驗證碼不對（確認手機時間正確，用剛掃的那組）")
    resp = JSONResponse({"ok": True, "recovery_codes": codes})
    _no_store(resp)
    return resp


@router.post("/api/auth/totp/disable")
async def totp_off(req: Request):
    _csrf(req)
    u = require_user(req)
    if is_public(req) and PUBLIC_TOTP == "required":
        raise HTTPException(403, "公開網址要求兩步驟驗證，請在本機或 tailnet 關閉")
    b = await req.json()
    with _conn() as c:
        row = c.execute("SELECT pw FROM users WHERE id=?", (u["id"],)).fetchone()
    if not check_pw(b.get("password") or "", row[0]) or not check_second_factor(u["id"], str(b.get("code") or "")):
        raise HTTPException(401, "密碼或驗證碼錯誤")
    totp_disable(u["id"])
    return {"ok": True}


@router.post("/api/auth/password")
async def change_password(req: Request):
    _csrf(req)
    u = require_user(req)
    b = await req.json()
    with _conn() as c:
        stored = c.execute("SELECT pw FROM users WHERE id=?", (u["id"],)).fetchone()[0]
    if not check_pw(b.get("old") or "", stored):
        raise HTTPException(401, "舊密碼錯誤")
    _validate(u["username"], b.get("new") or "")
    with _lock, _conn() as c:
        c.execute("UPDATE users SET pw=? WHERE id=?", (hash_pw(b["new"]), u["id"]))
        c.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))  # sign out everywhere else
    resp = JSONResponse({"ok": True})
    _set_cookie(resp, req, _new_session(u["id"], req.headers.get("user-agent", "")))
    return resp


@router.post("/api/auth/delete")
async def delete_account(req: Request):
    _csrf(req)
    u = require_user(req)
    b = await req.json()
    with _conn() as c:
        stored = c.execute("SELECT pw FROM users WHERE id=?", (u["id"],)).fetchone()[0]
    if not check_pw(b.get("password") or "", stored):
        raise HTTPException(401, "密碼錯誤")
    with _lock, _conn() as c:
        c.execute("DELETE FROM users WHERE id=?", (u["id"],))  # holdings + sessions cascade
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


# ------------------------------------------------------------------ holdings
FX_SYM = {"USD": "TWD=X", "JPY": "JPYTWD=X", "EUR": "EURTWD=X"}


def _currency(sym: str, q: dict) -> str:
    if q.get("currency"):
        return str(q["currency"]).upper()
    if sym.endswith((".TW", ".TWO")) or sym in ("^TWII",):
        return "TWD"
    if sym.endswith(".T"):
        return "JPY"
    if sym.endswith((".KS", ".KQ")):
        return "KRW"
    if sym.endswith(".HK"):
        return "HKD"
    return "USD"


def _fx(cur: str, quotes: dict) -> float | None:
    if cur == "TWD":
        return 1.0
    if cur in FX_SYM:
        q = quotes.get(FX_SYM[cur]) or {}
        return q.get("last")
    usd = (quotes.get("TWD=X") or {}).get("last")
    pair = {"KRW": "KRW=X", "HKD": "HKD=X", "CNY": "CNY=X"}.get(cur)
    if usd and pair and (quotes.get(pair) or {}).get("last"):
        return usd / quotes[pair]["last"]
    return None


def _clean(b: dict) -> tuple:
    sym = (b.get("symbol") or "").strip()
    if not sym or len(sym) > 40:
        raise HTTPException(400, "請輸入標的")
    try:
        shares = float(b.get("shares"))
        cost = float(b.get("cost"))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "股數與成本要是數字") from e
    if shares <= 0 or cost < 0 or shares > 1e12 or cost > 1e9:
        raise HTTPException(400, "股數要大於 0，成本不能是負數")
    opened = (b.get("opened") or "").strip()[:10] or None
    if opened and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", opened):
        raise HTTPException(400, "日期格式 YYYY-MM-DD")
    return sym, shares, cost, opened, (b.get("note") or "").strip()[:200]


def _rows(user_id: int) -> list[tuple]:
    with _conn() as c:
        return c.execute("SELECT id, symbol, shares, cost, opened, note FROM holdings WHERE user_id=? ORDER BY id",
                         (user_id,)).fetchall()


def portfolio(user_id: int) -> dict:
    rows = _rows(user_id)
    syms = [r[1] for r in rows if not r[1].startswith("FUND:")]
    qs = {q["symbol"]: q for q in (quotes_fn(list(dict.fromkeys(syms + list(FX_SYM.values()) + ["KRW=X", "HKD=X"])))
                                   if rows and quotes_fn else [])}
    out, tot_v, tot_c, tot_day, missing_fx = [], 0.0, 0.0, 0.0, set()
    for hid, sym, shares, cost, opened, note in rows:
        if sym.startswith("FUND:") and fund_fn:
            f = fund_fn(sym[5:]) or {}
            q = {"last": f.get("nav"), "pct": f.get("pct"), "currency": f.get("currency"), "name": f.get("name")}
        else:
            q = qs.get(sym, {})
        cur = _currency(sym, q)
        fx = _fx(cur, qs)
        last = q.get("last")
        value = last * shares if last is not None else None
        basis = cost * shares
        pnl = value - basis if value is not None else None
        prev = last / (1 + q["pct"] / 100) if last is not None and q.get("pct") is not None else None
        day = (last - prev) * shares if prev is not None else None
        row = {"id": hid, "symbol": sym, "name": q.get("name") or (name_fn(sym) if name_fn else None),
               "shares": shares, "cost": cost, "opened": opened, "note": note, "currency": cur, "last": last,
               "pct": q.get("pct"), "value": value, "basis": basis, "pnl": pnl,
               "ret": (pnl / basis * 100) if pnl is not None and basis else None, "day": day, "fx": fx,
               "chains": chains_fn(sym) if chains_fn else []}
        if value is not None and fx:
            row["value_twd"] = value * fx
            tot_v += value * fx
            tot_c += basis * fx
            tot_day += (day or 0) * fx
        elif value is not None:
            missing_fx.add(cur)
        out.append(row)
    for r in out:
        r["weight"] = r["value_twd"] / tot_v * 100 if r.get("value_twd") and tot_v else None
    expo: dict[str, float] = {}
    for r in out:
        for ch in r["chains"]:
            if r.get("weight"):
                expo[ch] = expo.get(ch, 0) + r["weight"] / len(r["chains"])
    return {"rows": out, "total": {"value": tot_v, "basis": tot_c, "pnl": tot_v - tot_c,
                                   "ret": (tot_v / tot_c - 1) * 100 if tot_c else None, "day": tot_day,
                                   "day_pct": tot_day / (tot_v - tot_day) * 100 if tot_v - tot_day else None},
            "exposure": sorted(({"chain": k, "weight": v} for k, v in expo.items()), key=lambda x: -x["weight"]),
            "missing_fx": sorted(missing_fx), "at": _now()}


@router.get("/api/me/holdings")
def list_holdings(req: Request, resp: Response):
    _no_store(resp)
    u = require_user(req)
    return {"user": u["username"], **portfolio(u["id"])}


@router.post("/api/me/holdings")
async def add_holding(req: Request):
    _csrf(req)
    u = require_user(req)
    sym, shares, cost, opened, note = _clean(await req.json())
    with _lock, _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM holdings WHERE user_id=?", (u["id"],)).fetchone()[0]
        if n >= 500:
            raise HTTPException(400, "最多 500 筆")
        cur = c.execute("INSERT INTO holdings(user_id,symbol,shares,cost,opened,note,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                        (u["id"], sym, shares, cost, opened, note, _now(), _now()))
    return {"id": cur.lastrowid}


@router.put("/api/me/holdings/{hid}")
async def edit_holding(hid: int, req: Request):
    _csrf(req)
    u = require_user(req)
    sym, shares, cost, opened, note = _clean(await req.json())
    with _lock, _conn() as c:
        cur = c.execute("UPDATE holdings SET symbol=?, shares=?, cost=?, opened=?, note=?, updated=? WHERE id=? AND user_id=?",
                        (sym, shares, cost, opened, note, _now(), hid, u["id"]))
    if not cur.rowcount:
        raise HTTPException(404, "找不到這筆持股")
    return {"ok": True}


@router.delete("/api/me/holdings/{hid}")
def delete_holding(hid: int, req: Request):
    _csrf(req)
    u = require_user(req)
    with _lock, _conn() as c:
        cur = c.execute("DELETE FROM holdings WHERE id=? AND user_id=?", (hid, u["id"]))
    if not cur.rowcount:
        raise HTTPException(404, "找不到這筆持股")
    return {"ok": True}


@router.get("/api/me/holdings.csv")
def export_csv(req: Request):
    u = require_user(req)
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["symbol", "shares", "cost", "opened", "note"])
    for _, sym, shares, cost, opened, note in _rows(u["id"]):
        w.writerow([sym, shares, cost, opened or "", note or ""])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=holdings.csv", "Cache-Control": "no-store"})


# ------------------------------------------------------------------ 定期定額 plans (computed from prices on the fly)
def _dca_clean(b: dict) -> tuple:
    from ft_common import dca
    sym = (b.get("symbol") or "").strip()
    if not sym or len(sym) > 40:
        raise HTTPException(400, "請輸入標的")
    try:
        amount = float(b.get("amount"))
        fee_pct = float(b.get("fee_pct", 0.1425) or 0)
        min_fee = float(b.get("min_fee", 1) or 0)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "金額與手續費要是數字") from e
    if not 1 <= amount <= 1e8 or not 0 <= fee_pct <= 5 or not 0 <= min_fee <= 1000:
        raise HTTPException(400, "每期金額 1 元～1 億、手續費 0–5%")
    freq = b.get("freq") or "monthly"
    if freq not in dca.FREQS:
        raise HTTPException(400, "頻率只能是每月／每週／每日")
    try:
        days = ",".join(str(d) for d in dca.parse_days(freq, b.get("days") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    start = (b.get("start") or "").strip()[:10]
    end = (b.get("end") or "").strip()[:10] or None
    for d in (start, end):
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            raise HTTPException(400, "日期格式 YYYY-MM-DD")
    if not start:
        raise HTTPException(400, "請輸入開始扣款日期")
    return sym, amount, freq, days, start, end, fee_pct, min_fee, (b.get("note") or "").strip()[:200]


def dca_portfolio(user_id: int) -> dict:
    from ft_common import dca
    with _conn() as c:
        rows = c.execute("SELECT id, symbol, amount, freq, days, start, end_date, fee_pct, min_fee, note FROM dca_plans "
                         "WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
    out, tot = [], {"invested": 0.0, "value": 0.0}
    for pid, sym, amount, freq, days, start, end, fee_pct, min_fee, note in rows:
        base = {"id": pid, "symbol": sym, "amount": amount, "freq": freq, "days": days, "start": start, "end": end,
                "fee_pct": fee_pct, "min_fee": min_fee, "note": note, "name": name_fn(sym) if name_fn else None,
                "plan": dca.describe(freq, dca.parse_days(freq, days), amount)}
        try:
            if start > dt.date.today().isoformat():
                raise ValueError(f"{start} 開始扣款")
            r = dca.simulate(sym, amount, freq, days, start, end, fee_pct, min_fee, lots_out=6)
            r.pop("curve", None)
            base.update({k: r[k] for k in ("n", "invested", "fees", "units", "avg_cost", "last_price", "value", "pnl", "ret",
                                           "xirr", "mdd", "next", "lots", "currency", "price_note")})
            tot["invested"] += r["invested"]
            tot["value"] += r["value"]
        except Exception as e:  # noqa: BLE001
            base["error"] = str(e)[:160]
            if start > dt.date.today().isoformat():
                base["next"] = start
        out.append(base)
    tot["pnl"] = tot["value"] - tot["invested"]
    tot["ret"] = (tot["value"] / tot["invested"] - 1) * 100 if tot["invested"] else None
    tot["monthly"] = sum(p["amount"] * (len(p["days"].split(",")) if p["freq"] == "monthly" else
                                        4.33 * len(p["days"].split(",")) if p["freq"] == "weekly" else 21)
                         for p in out if not p.get("end") or p["end"] >= dt.date.today().isoformat())
    return {"plans": out, "total": tot, "at": _now()}


@router.get("/api/me/dca")
def list_dca(req: Request, resp: Response):
    _no_store(resp)
    u = require_user(req)
    return dca_portfolio(u["id"])


@router.post("/api/me/dca")
async def add_dca(req: Request):
    _csrf(req)
    u = require_user(req)
    v = _dca_clean(await req.json())
    with _lock, _conn() as c:
        if c.execute("SELECT COUNT(*) FROM dca_plans WHERE user_id=?", (u["id"],)).fetchone()[0] >= 100:
            raise HTTPException(400, "最多 100 個定期定額")
        cur = c.execute("INSERT INTO dca_plans(user_id,symbol,amount,freq,days,start,end_date,fee_pct,min_fee,note,created,updated) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (u["id"], *v, _now(), _now()))
    return {"id": cur.lastrowid}


@router.put("/api/me/dca/{pid}")
async def edit_dca(pid: int, req: Request):
    _csrf(req)
    u = require_user(req)
    v = _dca_clean(await req.json())
    with _lock, _conn() as c:
        cur = c.execute("UPDATE dca_plans SET symbol=?, amount=?, freq=?, days=?, start=?, end_date=?, fee_pct=?, min_fee=?, note=?, "
                        "updated=? WHERE id=? AND user_id=?", (*v, _now(), pid, u["id"]))
    if not cur.rowcount:
        raise HTTPException(404, "找不到這個定期定額")
    return {"ok": True}


@router.delete("/api/me/dca/{pid}")
def delete_dca(pid: int, req: Request):
    _csrf(req)
    u = require_user(req)
    with _lock, _conn() as c:
        cur = c.execute("DELETE FROM dca_plans WHERE id=? AND user_id=?", (pid, u["id"]))
    if not cur.rowcount:
        raise HTTPException(404, "找不到這個定期定額")
    return {"ok": True}


# ------------------------------------------------------------------ admin CLI
def _cli(argv: list[str]) -> int:
    import getpass
    if not argv or argv[0] not in ("list", "add", "reset-password", "delete", "totp", "totp-off"):
        print(__doc__)
        return 1
    cmd = argv[0]
    with _conn() as c:
        if cmd == "list":
            for uid, name, created, last in c.execute("SELECT id, username, created, last_login FROM users ORDER BY id"):
                n = c.execute("SELECT COUNT(*) FROM holdings WHERE user_id=?", (uid,)).fetchone()[0]
                t = c.execute("SELECT enabled FROM totp WHERE user_id=?", (uid,)).fetchone()
                print(f"{name}\t建立 {created}\t最近登入 {last or '—'}\t持股 {n} 筆\t兩步驟 {'開' if t and t[0] else '關'}")
            return 0
    if len(argv) < 2:
        print("需要帳號名稱")
        return 1
    name = argv[1]
    if cmd in ("totp", "totp-off"):
        with _conn() as c:
            row = c.execute("SELECT id, username FROM users WHERE username=?", (name,)).fetchone()
        if not row:
            print("找不到帳號")
            return 1
        if cmd == "totp-off":
            totp_disable(row[0])
            print("已關閉兩步驟驗證（可重新設定）")
            return 0
        t = totp_begin(row[0], row[1])
        try:
            import segno
            segno.make(t["uri"], error="m").terminal(compact=True)
        except Exception:  # noqa: BLE001
            pass
        print(f"用 Google Authenticator／1Password／Aegis 掃上面的 QR，或手動輸入：{t['secret']}")
        codes = totp_confirm(row[0], input("輸入 App 顯示的 6 位數驗證碼："))
        if not codes:
            print("驗證碼不對，沒有啟用")
            return 1
        print("已啟用。救援碼（每組只能用一次，請存好，不會再顯示）：")
        print("  " + "  ".join(codes))
        return 0
    if cmd == "delete":
        with _conn() as c:
            n = c.execute("DELETE FROM users WHERE username=?", (name,)).rowcount
        print("已刪除（含持股）" if n else "找不到帳號")
        return 0 if n else 1
    pw = getpass.getpass("新密碼：")
    if pw != getpass.getpass("再輸入一次："):
        print("兩次不一樣")
        return 1
    if cmd == "add":
        create_user(name, pw)
        print("已建立")
        return 0
    _validate(name, pw)
    with _conn() as c:
        row = c.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
        if not row:
            print("找不到帳號")
            return 1
        c.execute("UPDATE users SET pw=? WHERE id=?", (hash_pw(pw), row[0]))
        c.execute("DELETE FROM sessions WHERE user_id=?", (row[0],))
    print("已重設，並登出所有裝置")
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.exit(_cli(sys.argv[1:]))
    except HTTPException as e:
        print(e.detail)
        sys.exit(1)
