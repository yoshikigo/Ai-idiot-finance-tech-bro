"""台幣匯率：新台幣對世界主要貨幣（Yahoo 交叉匯率＋台灣銀行牌告）。

Yahoo "XXX=X" is "units of XXX per 1 USD", so TWD per 1 XXX = (TWD=X) / (XXX=X) — one method for every
currency, with full daily history. Bank of Taiwan board rates (現金／即期 買入／賣出) come from its public CSV.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math

import pandas as pd

from . import market

# code: (中文名, 地區, 旗)
CCY = {
    "USD": ("美元", "主要", "🇺🇸"), "EUR": ("歐元", "主要", "🇪🇺"), "JPY": ("日圓", "主要", "🇯🇵"),
    "CNY": ("人民幣", "主要", "🇨🇳"), "GBP": ("英鎊", "主要", "🇬🇧"), "HKD": ("港幣", "主要", "🇭🇰"),
    "KRW": ("韓元", "亞洲", "🇰🇷"), "SGD": ("新加坡幣", "亞洲", "🇸🇬"), "THB": ("泰銖", "亞洲", "🇹🇭"),
    "MYR": ("馬來幣", "亞洲", "🇲🇾"), "PHP": ("菲律賓披索", "亞洲", "🇵🇭"), "IDR": ("印尼盾", "亞洲", "🇮🇩"),
    "VND": ("越南盾", "亞洲", "🇻🇳"), "INR": ("印度盧比", "亞洲", "🇮🇳"), "MOP": ("澳門幣", "亞洲", "🇲🇴"),
    "CHF": ("瑞士法郎", "歐洲", "🇨🇭"), "SEK": ("瑞典克朗", "歐洲", "🇸🇪"), "NOK": ("挪威克朗", "歐洲", "🇳🇴"),
    "DKK": ("丹麥克朗", "歐洲", "🇩🇰"), "PLN": ("波蘭茲羅提", "歐洲", "🇵🇱"), "CZK": ("捷克克朗", "歐洲", "🇨🇿"),
    "HUF": ("匈牙利福林", "歐洲", "🇭🇺"), "TRY": ("土耳其里拉", "歐洲", "🇹🇷"),
    "AUD": ("澳幣", "美洲／大洋洲", "🇦🇺"), "NZD": ("紐西蘭幣", "美洲／大洋洲", "🇳🇿"), "CAD": ("加拿大幣", "美洲／大洋洲", "🇨🇦"),
    "MXN": ("墨西哥披索", "美洲／大洋洲", "🇲🇽"), "BRL": ("巴西里拉", "美洲／大洋洲", "🇧🇷"),
    "ZAR": ("南非幣", "中東／非洲", "🇿🇦"), "AED": ("阿聯迪拉姆", "中東／非洲", "🇦🇪"), "SAR": ("沙烏地里亞爾", "中東／非洲", "🇸🇦"),
    "ILS": ("以色列謝克爾", "中東／非洲", "🇮🇱"),
}
REGIONS = ["主要", "亞洲", "歐洲", "美洲／大洋洲", "中東／非洲"]
# show these per 100 / 1000 units so the number is readable
UNIT = {"JPY": 100, "KRW": 1000, "IDR": 10000, "VND": 10000, "HUF": 100}
BOT_CSV = "https://rate.bot.com.tw/xrt/flcsv/0/day"
_FAKE_TWD = {"USD": 32.4, "EUR": 35.2, "JPY": 0.215, "CNY": 4.5, "GBP": 41.8, "HKD": 4.16, "KRW": 0.0235, "SGD": 24.6,
             "THB": 0.96, "MYR": 7.4, "PHP": 0.56, "IDR": 0.002, "VND": 0.00128, "INR": 0.385, "MOP": 4.04, "CHF": 38.5,
             "SEK": 3.1, "NOK": 3.0, "DKK": 4.7, "PLN": 8.1, "CZK": 1.4, "HUF": 0.088, "TRY": 0.92, "AUD": 21.3,
             "NZD": 19.2, "CAD": 23.4, "MXN": 1.75, "BRL": 5.9, "ZAR": 1.8, "AED": 8.82, "SAR": 8.64, "ILS": 8.7}


def _usd_sym(code: str) -> str:
    return "TWD=X" if code == "TWD" else f"{code}=X"


def _fake_series(code: str, days: int) -> pd.Series:
    import numpy as np
    rng = np.random.default_rng(abs(hash(code)) % 2 ** 32)
    idx = pd.bdate_range(end=dt.date.today(), periods=days)
    walk = np.cumprod(1 + rng.normal(0, 0.004, len(idx)))
    return pd.Series(_FAKE_TWD.get(code, 1.0) * walk / walk[-1], index=idx)


def _closes(sym: str, period: str) -> pd.Series:
    df = market.history(sym, period, "1d")
    if df is None or not len(df):
        return pd.Series(dtype=float)
    s = df["Close"].astype(float).dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    return s[~s.index.duplicated(keep="last")]


def series(code: str, period: str = "1y") -> pd.Series:
    """TWD per 1 unit of `code`, daily."""
    code = code.upper()
    if code not in CCY:
        raise KeyError(code)
    if market.FAKE:
        return _fake_series(code, {"1mo": 22, "3mo": 66, "6mo": 130, "1y": 260, "2y": 520, "5y": 1300, "10y": 2600}.get(period, 260))

    def load():
        twd = _closes("TWD=X", period)
        if code == "USD":
            return twd
        other = _closes(_usd_sym(code), period)
        both = pd.concat([twd, other], axis=1, join="inner").dropna()
        both = both[(both.iloc[:, 1] > 0)]
        return (both.iloc[:, 0] / both.iloc[:, 1]).rename(code)
    return market.cached(f"twfx:{code}:{period}", 900, load)


def _pct(s: pd.Series, days: int):
    if len(s) < 2:
        return None
    t = s.index[-1] - pd.Timedelta(days=days)
    prior = s[s.index <= t]
    if not len(prior) or not prior.iloc[-1]:
        return None
    return (float(s.iloc[-1]) / float(prior.iloc[-1]) - 1) * 100


def snapshot(codes: list[str] | None = None) -> list[dict]:
    rows = []
    for code in codes or list(CCY):
        name, region, flag = CCY.get(code, (code, "", ""))
        try:
            s = series(code, "2y")  # 2 years so the 1-year change always has a base; table shows the last year
        except Exception as e:  # noqa: BLE001
            rows.append({"code": code, "name": name, "region": region, "flag": flag, "error": str(e)[:120]})
            continue
        s = s[s.apply(lambda v: isinstance(v, float) and math.isfinite(v) and v > 0)]
        if not len(s):
            rows.append({"code": code, "name": name, "region": region, "flag": flag, "error": "沒有資料"})
            continue
        last = float(s.iloc[-1])
        prev = float(s.iloc[-2]) if len(s) > 1 else None
        yr = s[s.index > s.index[-1] - pd.Timedelta(days=365)]
        step = max(1, len(yr) // 60)
        rows.append({"code": code, "name": name, "region": region, "flag": flag, "unit": UNIT.get(code, 1),
                     "twd": last, "per_twd": 1 / last, "date": s.index[-1].strftime("%Y-%m-%d"),
                     "d1": None if not prev else (last / prev - 1) * 100, "m1": _pct(s, 30), "m3": _pct(s, 91),
                     "y1": _pct(s, 365), "hi52": float(yr.max()), "lo52": float(yr.min()),
                     "spark": [round(float(x), 6) for x in yr.iloc[::step]]})
    return rows


def history(code: str, period: str = "1y") -> list[dict]:
    s = series(code, period)
    return [{"time": t.strftime("%Y-%m-%d"), "value": round(float(v), 6)} for t, v in s.items() if math.isfinite(v)]


# ------------------------------------------------------------------ Bank of Taiwan board rates
def parse_bot(text: str) -> dict[str, dict]:
    """rate.bot.com.tw flcsv: CODE,本行買入,現金,即期,遠期…,本行賣出,現金,即期,遠期…"""
    out: dict[str, dict] = {}
    for row in csv.reader(io.StringIO(text.lstrip("﻿"))):
        if len(row) < 4 or not row[0].strip().isalpha() or len(row[0].strip()) != 3:
            continue
        cells = [c.strip() for c in row]

        def num(i):
            try:
                v = float(cells[i])
                return v if v > 0 else None
            except (IndexError, ValueError):
                return None
        rec = {}
        for label, side in (("本行買入", "buy"), ("本行賣出", "sell")):
            if label in cells:
                k = cells.index(label)
                rec[f"cash_{side}"], rec[f"spot_{side}"] = num(k + 1), num(k + 2)
        if any(v for v in rec.values()):
            out[cells[0].upper()] = rec
    return out


def bot_rates() -> dict:
    if market.FAKE:
        return {"rates": {c: {"cash_buy": v * 0.985, "cash_sell": v * 1.015, "spot_buy": v * 0.995, "spot_sell": v * 1.005}
                          for c, v in _FAKE_TWD.items() if c in ("USD", "EUR", "JPY", "CNY", "GBP", "HKD", "AUD", "SGD", "KRW")},
                "source": "台灣銀行（示範）"}

    def load():
        from .twhttp import decode_tw, fetch
        return {"rates": parse_bot(decode_tw(fetch(BOT_CSV, timeout=20))), "source": "台灣銀行牌告",
                "fetched": dt.datetime.now().isoformat(timespec="minutes")}
    try:
        return market.cached("twfx:bot", 600, load)
    except Exception as e:  # noqa: BLE001
        return {"rates": {}, "source": "台灣銀行牌告", "error": str(e)[:160]}


def text(codes: list[str] | None = None) -> str:
    rows = snapshot(codes or ["USD", "JPY", "EUR", "CNY", "HKD", "GBP", "KRW", "AUD", "SGD"])
    lines = ["【新台幣匯率】（1 外幣 = 台幣；漲＝外幣升值／台幣貶值）"]
    for r in rows:
        if "twd" not in r:
            continue
        u = r["unit"]
        f = lambda x: "—" if x is None else f"{x:+.2f}%"  # noqa: E731
        lines.append(f"- {r['name']} {r['code']}：{u if u > 1 else 1} {r['code']} = {r['twd'] * u:,.4f} 台幣"
                     f"（日 {f(r['d1'])}、1 月 {f(r['m1'])}、1 年 {f(r['y1'])}；{r['date']}）")
    return "\n".join(lines)
