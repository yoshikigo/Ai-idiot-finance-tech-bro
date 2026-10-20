"""分析工具箱 — the same jobs as Longbridge's agent skills (technical / options / research / earnings / value /
market intel), built on the data this terminal already has (Yahoo, TWSE/TPEx research DB, SEC EDGAR, FRED).
No broker account, nothing leaves the host except the usual public data requests.

Every function returns {"title", "text" (Markdown, Traditional Chinese), "data" (numbers for the UI/tests)}.
They report facts and rule-based signals only — no buy/sell recommendation.
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from . import market

SPDR = {"XLK": "科技", "XLC": "通訊服務", "XLY": "非必需消費", "XLP": "必需消費", "XLE": "能源", "XLF": "金融",
        "XLV": "醫療保健", "XLI": "工業", "XLB": "原物料", "XLRE": "房地產", "XLU": "公用事業"}
US_BIG = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "TSM", "BRK-B", "JPM", "LLY", "V", "XOM",
          "UNH", "MA", "COST", "NFLX", "AMD", "ORCL", "PLTR", "MU", "INTC", "QCOM", "ASML", "ARM", "SMCI", "COIN"]
TW_BIG = ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "2303.TW", "3711.TW", "2881.TW", "2882.TW", "2891.TW",
          "2412.TW", "3231.TW", "6669.TW", "2603.TW", "3661.TW", "2345.TW", "3008.TW", "2379.TW", "2357.TW", "1301.TW",
          "2002.TW", "2886.TW", "5871.TW", "2395.TW", "3034.TW", "2408.TW", "2344.TW", "4938.TW", "0050.TW", "0056.TW"]
NOT_ADVICE = "\n\n> 以上為資料整理與規則化訊號，不構成投資建議。"


def _f(x, d=2, pct=False, sign=False):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    s = f"{x:+,.{d}f}" if sign else f"{x:,.{d}f}"
    return s + ("%" if pct else "")


def _hist(sym: str, period: str = "2y") -> pd.DataFrame:
    df = market.history(sym, period, "1d")
    if df is None or len(df) < 30:
        raise ValueError(f"{sym} 歷史資料不足")
    df = df.copy()
    df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
    return df[~df.index.duplicated(keep="last")]


def _tw_code(sym: str) -> str | None:
    return sym.split(".")[0] if sym.endswith((".TW", ".TWO")) else None


# ------------------------------------------------------------------ 技術面
def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, 1e-12))).fillna(50)


def _kd(df: pd.DataFrame, n: int = 9):
    lo, hi = df["Low"].rolling(n).min(), df["High"].rolling(n).max()
    rsv = ((df["Close"] - lo) / (hi - lo).replace(0, np.nan) * 100).fillna(50)
    k, d = [50.0], [50.0]
    for v in rsv.values[1:]:
        k.append(k[-1] * 2 / 3 + v / 3)
        d.append(d[-1] * 2 / 3 + k[-1] / 3)
    return pd.Series(k, index=df.index), pd.Series(d, index=df.index)


def candle_patterns(df: pd.DataFrame) -> list[str]:
    """Classic patterns on the last 1–3 bars."""
    o, h, lo, c = (df[k].values[-3:] for k in ("Open", "High", "Low", "Close"))
    if len(c) < 3:
        return []
    out = []
    body = abs(c[-1] - o[-1])
    rng = max(h[-1] - lo[-1], 1e-12)
    upper, lower = h[-1] - max(c[-1], o[-1]), min(c[-1], o[-1]) - lo[-1]
    trend_dn = c[-1] < df["Close"].iloc[-6:-1].mean()
    if body / rng < 0.1:
        out.append("十字線（多空拉鋸）")
    elif lower > 2 * body and upper < body and trend_dn:
        out.append("錘子線（跌勢中長下影，可能止跌）")
    elif upper > 2 * body and lower < body and not trend_dn:
        out.append("射擊之星（漲勢中長上影，可能轉弱）")
    if c[-2] < o[-2] and c[-1] > o[-1] and c[-1] >= o[-2] and o[-1] <= c[-2]:
        out.append("多頭吞噬")
    if c[-2] > o[-2] and c[-1] < o[-1] and o[-1] >= c[-2] and c[-1] <= o[-2]:
        out.append("空頭吞噬")
    small_mid = abs(c[-2] - o[-2]) < 0.3 * abs(c[-3] - o[-3])
    if c[-3] < o[-3] and small_mid and c[-1] > o[-1] and c[-1] > (o[-3] + c[-3]) / 2:
        out.append("晨星（三根 K 棒反轉向上）")
    if c[-3] > o[-3] and small_mid and c[-1] < o[-1] and c[-1] < (o[-3] + c[-3]) / 2:
        out.append("夜星（三根 K 棒反轉向下）")
    return out


def technical(sym: str) -> dict:
    df = _hist(sym, "2y")
    c, v = df["Close"], df["Volume"].fillna(0)
    last = float(c.iloc[-1])
    ma = {n: float(c.rolling(n).mean().iloc[-1]) for n in (5, 20, 60, 120, 240) if len(c) >= n}
    rsi = float(_rsi(c).iloc[-1])
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = dif - dea
    k, d = _kd(df)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std()
    bb_up, bb_lo = float((mid + 2 * sd).iloc[-1]), float((mid - 2 * sd).iloc[-1])
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    # Ichimoku (9/26/52)
    tenkan = (df["High"].rolling(9).max() + df["Low"].rolling(9).min()) / 2
    kijun = (df["High"].rolling(26).max() + df["Low"].rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((df["High"].rolling(52).max() + df["Low"].rolling(52).min()) / 2).shift(26)
    cloud_top, cloud_bot = max(span_a.iloc[-1], span_b.iloc[-1]), min(span_a.iloc[-1], span_b.iloc[-1])
    cloud = "雲上（偏多）" if last > cloud_top else "雲下（偏空）" if last < cloud_bot else "雲中（盤整）"
    vol_ratio = float(v.iloc[-1] / v.iloc[-21:-1].mean()) if v.iloc[-21:-1].mean() else None
    hi52, lo52 = float(c.iloc[-252:].max()), float(c.iloc[-252:].min())
    swings_hi = sorted({round(float(x), 2) for x in df["High"].iloc[-60:].nlargest(3)}, reverse=True)
    swings_lo = sorted({round(float(x), 2) for x in df["Low"].iloc[-60:].nsmallest(3)})
    sig = []
    if len(dif) > 2 and dif.iloc[-2] <= dea.iloc[-2] and dif.iloc[-1] > dea.iloc[-1]:
        sig.append("MACD 黃金交叉")
    if len(dif) > 2 and dif.iloc[-2] >= dea.iloc[-2] and dif.iloc[-1] < dea.iloc[-1]:
        sig.append("MACD 死亡交叉")
    if k.iloc[-2] <= d.iloc[-2] and k.iloc[-1] > d.iloc[-1]:
        sig.append(f"KD 黃金交叉（K {k.iloc[-1]:.0f}）")
    if k.iloc[-2] >= d.iloc[-2] and k.iloc[-1] < d.iloc[-1]:
        sig.append(f"KD 死亡交叉（K {k.iloc[-1]:.0f}）")
    if rsi >= 70:
        sig.append("RSI 超買（≥70）")
    elif rsi <= 30:
        sig.append("RSI 超賣（≤30）")
    if 20 in ma and 60 in ma:
        p20, p60 = c.rolling(20).mean(), c.rolling(60).mean()
        if p20.iloc[-2] <= p60.iloc[-2] and p20.iloc[-1] > p60.iloc[-1]:
            sig.append("月線上穿季線")
        if p20.iloc[-2] >= p60.iloc[-2] and p20.iloc[-1] < p60.iloc[-1]:
            sig.append("月線下穿季線")
    if last >= hi52 * 0.999:
        sig.append("創 52 週新高")
    if last <= lo52 * 1.001:
        sig.append("創 52 週新低")
    if last > bb_up:
        sig.append("突破布林上軌")
    elif last < bb_lo:
        sig.append("跌破布林下軌")
    if vol_ratio and vol_ratio >= 2:
        sig.append(f"爆量（{vol_ratio:.1f} 倍 20 日均量）")
    above = [n for n, x in ma.items() if last > x]
    align = "多頭排列" if all(ma[a] > ma[b] for a, b in zip(sorted(ma), sorted(ma)[1:])) else \
            "空頭排列" if all(ma[a] < ma[b] for a, b in zip(sorted(ma), sorted(ma)[1:])) else "均線糾結"
    score = sum([last > ma.get(20, last), last > ma.get(60, last), last > ma.get(240, last), hist.iloc[-1] > 0,
                 rsi > 50, last > cloud_top]) - 3
    trend = "偏多" if score >= 2 else "偏空" if score <= -2 else "中性"
    pats = candle_patterns(df)
    lines = [f"### {sym} 技術面（{df.index[-1]:%Y-%m-%d} 收 {_f(last)}）",
             f"- **趨勢**：{trend}（{align}；站上 {'、'.join(f'{n}日' for n in above) or '無'} 均線）",
             "- **均線**：" + "、".join(f"{n}日 {_f(x)}" for n, x in ma.items()),
             f"- **RSI(14)** {rsi:.1f}・**KD(9)** K {k.iloc[-1]:.1f}／D {d.iloc[-1]:.1f}・**MACD** DIF {dif.iloc[-1]:.3f}／DEA {dea.iloc[-1]:.3f}／柱 {hist.iloc[-1]:+.3f}",
             f"- **布林(20,2)** {_f(bb_lo)}～{_f(bb_up)}・**ATR(14)** {_f(atr)}（{atr / last * 100:.2f}%）・**量比** {_f(vol_ratio)}",
             f"- **一目均衡表**：轉換線 {_f(float(tenkan.iloc[-1]))}、基準線 {_f(float(kijun.iloc[-1]))}、雲帶 {_f(float(cloud_bot))}～{_f(float(cloud_top))} → {cloud}",
             f"- **52 週** {_f(lo52)}～{_f(hi52)}（距高點 {_f((last / hi52 - 1) * 100, 1, True, True)}）・近 60 日壓力 {', '.join(_f(x) for x in swings_hi)}・支撐 {', '.join(_f(x) for x in swings_lo)}",
             f"- **訊號**：{'、'.join(sig) or '無特別訊號'}",
             f"- **K 線型態**：{'、'.join(pats) or '無明顯型態'}"]
    return {"title": f"{sym} 技術面", "text": "\n".join(lines) + NOT_ADVICE,
            "data": {"last": last, "ma": ma, "rsi": rsi, "k": float(k.iloc[-1]), "d": float(d.iloc[-1]), "macd_hist": float(hist.iloc[-1]),
                     "bb": [bb_lo, bb_up], "atr": atr, "cloud": cloud, "signals": sig, "patterns": pats, "trend": trend,
                     "hi52": hi52, "lo52": lo52}}


# ------------------------------------------------------------------ 選擇權
def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def greeks(S, K, T, r, iv, call=True):
    if not (S and K and T > 0 and iv and iv > 0):
        return {}
    d1 = (math.log(S / K) + (r + iv * iv / 2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)
    pdf = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    delta = _ncdf(d1) if call else _ncdf(d1) - 1
    gamma = pdf / (S * iv * math.sqrt(T))
    vega = S * pdf * math.sqrt(T) / 100
    disc = r * K * math.exp(-r * T)
    theta = ((-S * pdf * iv / (2 * math.sqrt(T)) - disc * _ncdf(d2)) if call
             else (-S * pdf * iv / (2 * math.sqrt(T)) + disc * _ncdf(-d2))) / 365
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def _risk_free() -> float:
    try:
        from . import fred
        return float(fred.raw("DGS3MO").iloc[-1]) / 100
    except Exception:  # noqa: BLE001
        return 0.04


def max_pain(calls: pd.DataFrame, puts: pd.DataFrame) -> float | None:
    strikes = sorted(set(calls["strike"]).union(puts["strike"]))
    if not strikes:
        return None
    co, po = calls.set_index("strike")["openInterest"].fillna(0), puts.set_index("strike")["openInterest"].fillna(0)

    def pain(p):
        return float(sum(max(0, p - k) * oi for k, oi in co.items()) + sum(max(0, k - p) * oi for k, oi in po.items()))
    return min(strikes, key=pain)


def options(sym: str, expiry: str = "") -> dict:
    if sym.endswith((".TW", ".TWO")):
        raise ValueError("台股個股選擇權沒有免費的公開鏈資料；這裡只支援美股（含 ETF）")
    if sym.startswith(("^", "FUND:")):
        raise ValueError("這裡只支援美股與美國 ETF 的選擇權")
    if market.FAKE:
        raise ValueError("示範資料模式沒有選擇權鏈")
    t = market._yf().Ticker(sym)
    exps = list(t.options or [])
    if not exps:
        raise ValueError(f"{sym} 沒有掛牌選擇權")
    exp = expiry if expiry in exps else next((e for e in exps if (dt.date.fromisoformat(e) - dt.date.today()).days >= 7), exps[0])
    ch = t.option_chain(exp)
    calls, puts = ch.calls.copy(), ch.puts.copy()
    S = float(_hist(sym, "3mo")["Close"].iloc[-1])
    T = max((dt.date.fromisoformat(exp) - dt.date.today()).days, 1) / 365
    r = _risk_free()
    strikes = sorted(set(calls["strike"]))
    atm = min(strikes, key=lambda k: abs(k - S))
    i = strikes.index(atm)
    near = strikes[max(0, i - 5): i + 6]
    rows = []
    for k in near:
        cr = calls[calls["strike"] == k].iloc[0] if (calls["strike"] == k).any() else None
        pr = puts[puts["strike"] == k].iloc[0] if (puts["strike"] == k).any() else None
        row = {"strike": k}
        for side, x, is_call in (("c", cr, True), ("p", pr, False)):
            if x is None:
                continue
            iv = float(x.get("impliedVolatility") or 0)
            g = greeks(S, k, T, r, iv, is_call)
            row.update({f"{side}_last": float(x.get("lastPrice") or 0), f"{side}_iv": iv * 100,
                        f"{side}_oi": int(x.get("openInterest") or 0), f"{side}_vol": int(x.get("volume") or 0) if not pd.isna(x.get("volume")) else 0,
                        f"{side}_delta": g.get("delta"), f"{side}_theta": g.get("theta")})
        rows.append(row)
    c_oi, p_oi = float(calls["openInterest"].fillna(0).sum()), float(puts["openInterest"].fillna(0).sum())
    c_v, p_v = float(calls["volume"].fillna(0).sum()), float(puts["volume"].fillna(0).sum())
    mp = max_pain(calls, puts)
    atm_iv = next((r_.get("c_iv") for r_ in rows if r_["strike"] == atm), None)
    move = S * (atm_iv or 0) / 100 * math.sqrt(T) if atm_iv else None
    tbl = ["| 履約價 | Call 價 | IV | Δ | OI | Put 價 | IV | Δ | OI |", "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r_ in rows:
        mark = " **ATM**" if r_["strike"] == atm else ""
        tbl.append(f"| {_f(r_['strike'])}{mark} | {_f(r_.get('c_last'))} | {_f(r_.get('c_iv'), 1)} | {_f(r_.get('c_delta'))} | {r_.get('c_oi', '—')} "
                   f"| {_f(r_.get('p_last'))} | {_f(r_.get('p_iv'), 1)} | {_f(r_.get('p_delta'))} | {r_.get('p_oi', '—')} |")
    text = "\n".join([f"### {sym} 選擇權（到期 {exp}，剩 {round(T * 365)} 天；現價 {_f(S)}）",
                      f"- **Put/Call 比**：未平倉 {p_oi / c_oi:.2f}・成交量 {(p_v / c_v) if c_v else float('nan'):.2f}" if c_oi else "- 沒有未平倉資料",
                      f"- **Max pain**：{_f(mp)}・**ATM IV** {_f(atm_iv, 1)}% → 到期前市場隱含波動約 ±{_f(move)}（±{_f(move / S * 100 if move else None, 1)}%）",
                      f"- 其他到期日：{'、'.join(exps[:8])}{'…' if len(exps) > 8 else ''}",
                      f"- Greeks 用 Black-Scholes，無風險利率 {r * 100:.2f}%（美債 3 個月）", "", *tbl]) + NOT_ADVICE
    return {"title": f"{sym} 選擇權", "text": text,
            "data": {"expiry": exp, "expiries": exps, "spot": S, "atm": atm, "atm_iv": atm_iv, "max_pain": mp,
                     "pcr_oi": p_oi / c_oi if c_oi else None, "rows": rows}}


# ------------------------------------------------------------------ 研究：評等、目標價、內部人、法人
def research(sym: str) -> dict:
    lines = [f"### {sym} 研究與籌碼"]
    data: dict = {}
    code = _tw_code(sym)
    if code:
        try:
            from .research import store
            with store.conn() as c:
                ins = c.execute("SELECT date, f_net, t_net, d_net, all_net FROM insti WHERE code=? ORDER BY date DESC LIMIT 20", (code,)).fetchall()
                mg = c.execute("SELECT date, margin, short FROM margin WHERE code=? ORDER BY date DESC LIMIT 20", (code,)).fetchall()
                va = c.execute("SELECT date, pe, dy, pb FROM valuation WHERE code=? ORDER BY date DESC LIMIT 1", (code,)).fetchone()
            if ins:
                f5, t5, d5 = (sum((r[i] or 0) for r in ins[:5]) for i in (1, 2, 3))
                f20 = sum((r[1] or 0) for r in ins)
                lines.append(f"- **三大法人（近 5 日，張）**：外資 {_f(f5 / 1000, 0, sign=True)}、投信 {_f(t5 / 1000, 0, sign=True)}、自營 {_f(d5 / 1000, 0, sign=True)}；外資近 20 日 {_f(f20 / 1000, 0, sign=True)}（{ins[0][0]}）")
                streak = 0
                for r in ins:
                    if (r[1] or 0) > 0 and streak >= 0:
                        streak += 1
                    elif (r[1] or 0) < 0 and streak <= 0:
                        streak -= 1
                    else:
                        break
                if abs(streak) >= 3:
                    lines.append(f"- 外資連續 {abs(streak)} 日{'買超' if streak > 0 else '賣超'}")
                data["insti"] = {"f5": f5, "t5": t5, "d5": d5, "f20": f20, "streak": streak}
            if len(mg) >= 2:
                lines.append(f"- **融資** {_f(mg[0][1], 0)} 張（20 日 {_f((mg[0][1] or 0) - (mg[-1][1] or 0), 0, sign=True)}）・**融券** {_f(mg[0][2], 0)} 張・券資比 {_f((mg[0][2] or 0) / (mg[0][1] or 1) * 100, 1)}%")
            if va:
                lines.append(f"- **估值（證交所）** 本益比 {_f(va[1])}・殖利率 {_f(va[2])}%・股價淨值比 {_f(va[3])}（{va[0]}）")
                data["valuation"] = {"pe": va[1], "dy": va[2], "pb": va[3]}
            if not (ins or mg or va):
                lines.append("- 策略研究資料庫還沒有這檔的法人／融資資料（背景回補中）")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- 讀取台股籌碼資料失敗：{e}")
    if market.FAKE:
        lines.append("- 示範資料模式：沒有分析師評等")
        return {"title": f"{sym} 研究", "text": "\n".join(lines) + NOT_ADVICE, "data": data}
    try:
        t = market._yf().Ticker(sym)
        info = market.info(sym)
        tgt = {k: info.get(k) for k in ("targetMeanPrice", "targetHighPrice", "targetLowPrice", "numberOfAnalystOpinions", "recommendationKey", "currentPrice")}
        if tgt.get("targetMeanPrice"):
            up = (tgt["targetMeanPrice"] / tgt["currentPrice"] - 1) * 100 if tgt.get("currentPrice") else None
            lines.append(f"- **分析師目標價**：平均 {_f(tgt['targetMeanPrice'])}（{_f(up, 1, True, True)}）、區間 {_f(tgt['targetLowPrice'])}～{_f(tgt['targetHighPrice'])}，{tgt.get('numberOfAnalystOpinions') or '?'} 位；共識 {tgt.get('recommendationKey') or '—'}")
        data["targets"] = tgt
        try:
            rs = t.recommendations
            if rs is not None and len(rs):
                r0 = rs.iloc[0]
                lines.append(f"- **評等分布（本月）**：強力買進 {int(r0.get('strongBuy', 0))}、買進 {int(r0.get('buy', 0))}、持有 {int(r0.get('hold', 0))}、賣出 {int(r0.get('sell', 0))}、強力賣出 {int(r0.get('strongSell', 0))}")
        except Exception:  # noqa: BLE001
            pass
        try:
            ud = t.upgrades_downgrades
            if ud is not None and len(ud):
                lines.append("- **最近評等調整**：")
                for idx, r in ud.head(6).iterrows():
                    lines.append(f"  - {pd.Timestamp(idx):%Y-%m-%d} {r.get('Firm', '')}：{r.get('FromGrade') or '—'} → {r.get('ToGrade') or '—'}（{r.get('Action', '')}）")
        except Exception:  # noqa: BLE001
            pass
        if not code:
            try:
                it = t.insider_transactions
                if it is not None and len(it):
                    lines.append("- **內部人交易（近期）**：")
                    for _, r in it.head(6).iterrows():
                        d = r.get("Start Date") or r.get("Date")
                        lines.append(f"  - {pd.Timestamp(d):%Y-%m-%d} {r.get('Insider', '')}（{r.get('Position', '')}）{r.get('Text') or r.get('Transaction') or ''} {_f(r.get('Shares'), 0)} 股")
            except Exception:  # noqa: BLE001
                pass
            try:
                ih = t.institutional_holders
                if ih is not None and len(ih):
                    lines.append("- **前五大機構持有**：" + "、".join(f"{r['Holder']} {_f(r.get('pctHeld', 0) * 100, 2)}%" for _, r in ih.head(5).iterrows()))
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        lines.append(f"- Yahoo 研究資料讀取失敗：{type(e).__name__}")
    return {"title": f"{sym} 研究", "text": "\n".join(lines) + NOT_ADVICE, "data": data}


# ------------------------------------------------------------------ 財報季
def earnings(sym: str) -> dict:
    lines = [f"### {sym} 財報與市場反應"]
    data: dict = {}
    code = _tw_code(sym)
    if code:
        try:
            from .research import store
            with store.conn() as c:
                rv = c.execute("SELECT period, revenue, mom, yoy, cum_yoy FROM revenue WHERE code=? ORDER BY period DESC LIMIT 6", (code,)).fetchall()
                inc = c.execute("SELECT period, revenue, gross, op_income, net_parent, eps FROM income_ytd WHERE code=? ORDER BY period DESC LIMIT 8", (code,)).fetchall()
            if rv:
                lines.append("- **月營收**：" + "；".join(f"{p} {_f((r or 0) / 1e5, 0)} 億（年增 {_f(y, 1, True, True)}）" for p, r, m, y, cy in rv))
                data["revenue"] = [{"period": p, "revenue": r, "mom": m, "yoy": y} for p, r, m, y, cy in rv]
            if inc:
                q = []
                for p, rev, gross, op, net, eps in inc[:4]:
                    gm = gross / rev * 100 if rev and gross is not None else None
                    om = op / rev * 100 if rev and op is not None else None
                    q.append(f"{p}（累計）EPS {_f(eps)}、毛利率 {_f(gm, 1)}%、營益率 {_f(om, 1)}%")
                lines.append("- **財報（年初至今累計）**：" + "；".join(q))
            if not (rv or inc):
                lines.append("- 策略研究資料庫還沒有這檔的月營收／財報（背景回補中）")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- 讀取台股財報資料失敗：{e}")
    if market.FAKE:
        lines.append("- 示範資料模式：沒有 EPS 預估與財報日")
        return {"title": f"{sym} 財報", "text": "\n".join(lines) + NOT_ADVICE, "data": data}
    try:
        t = market._yf().Ticker(sym)
        ed = t.get_earnings_dates(limit=12)
        if ed is not None and len(ed):
            ed = ed.copy()
            ed.index = pd.DatetimeIndex(ed.index).tz_localize(None).normalize()
            px = _hist(sym, "5y")["Close"]
            fut = ed[ed.index > pd.Timestamp(dt.date.today())]
            if len(fut):
                nx = fut.index.min()
                est = fut.loc[nx].get("EPS Estimate")
                lines.append(f"- **下次財報**：{nx:%Y-%m-%d}（EPS 預估 {_f(est)}）")
                data["next"] = nx.strftime("%Y-%m-%d")
            past = ed[ed.index <= pd.Timestamp(dt.date.today())].dropna(subset=["Reported EPS"]).head(8)
            if len(past):
                lines += ["", "| 財報日 | EPS 預估 | 實際 | 驚喜 | 隔日 | 5 日 |", "|---|---:|---:|---:|---:|---:|"]
                beats = 0
                reacts = []
                for d0, r in past.iterrows():
                    k = px.index.searchsorted(d0)
                    # report usually after close → reaction = next session vs report-day close
                    r1 = (px.iloc[k + 1] / px.iloc[k] - 1) * 100 if k + 1 < len(px) else None
                    r5 = (px.iloc[min(k + 5, len(px) - 1)] / px.iloc[k] - 1) * 100 if k + 1 < len(px) else None
                    sp = r.get("Surprise(%)")
                    beats += 1 if (sp or 0) > 0 else 0
                    reacts.append(r1)
                    lines.append(f"| {d0:%Y-%m-%d} | {_f(r.get('EPS Estimate'))} | {_f(r.get('Reported EPS'))} | {_f(sp, 1, True, True)} | {_f(r1, 1, True, True)} | {_f(r5, 1, True, True)} |")
                rr = [x for x in reacts if x is not None]
                lines.insert(1, f"- **近 {len(past)} 季**：{beats} 季優於預期；財報後隔日平均 {_f(np.mean(rr) if rr else None, 1, True, True)}、平均絕對波動 {_f(np.mean(np.abs(rr)) if rr else None, 1, True)}")
                data["beats"] = beats
                data["quarters"] = len(past)
    except Exception as e:  # noqa: BLE001
        lines.append(f"- Yahoo 財報日資料讀取失敗：{type(e).__name__}")
    return {"title": f"{sym} 財報", "text": "\n".join(lines) + NOT_ADVICE, "data": data}


# ------------------------------------------------------------------ 價值投資：Graham／F-score／護城河
def _row(df: pd.DataFrame, *names):
    for n in names:
        if df is not None and n in df.index:
            s = df.loc[n]
            return s.astype(float)
    return None


def piotroski(inc: pd.DataFrame, bal: pd.DataFrame, cf: pd.DataFrame) -> tuple[int | None, list[str]]:
    """9-point F-score from the two most recent annual statements (Yahoo layout, newest column first)."""
    try:
        ni, ta, cfo = _row(inc, "Net Income"), _row(bal, "Total Assets"), _row(cf, "Operating Cash Flow")
        ltd = _row(bal, "Long Term Debt", "Long Term Debt And Capital Lease Obligation")
        ca, cl = _row(bal, "Current Assets"), _row(bal, "Current Liabilities")
        sh = _row(bal, "Ordinary Shares Number", "Share Issued")
        gp, rev = _row(inc, "Gross Profit"), _row(inc, "Total Revenue")
        if ni is None or ta is None or len(ta) < 2:
            return None, []
        roa0, roa1 = ni.iloc[0] / ta.iloc[0], ni.iloc[1] / ta.iloc[1]
        pts = []
        pts.append(("ROA 為正", roa0 > 0))
        pts.append(("營業現金流為正", cfo is not None and cfo.iloc[0] > 0))
        pts.append(("ROA 改善", roa0 > roa1))
        pts.append(("現金流 > 淨利（盈餘品質）", cfo is not None and cfo.iloc[0] > ni.iloc[0]))
        pts.append(("長期負債比下降", ltd is not None and len(ltd) > 1 and ltd.iloc[0] / ta.iloc[0] <= ltd.iloc[1] / ta.iloc[1]))
        pts.append(("流動比率上升", ca is not None and cl is not None and len(ca) > 1 and ca.iloc[0] / cl.iloc[0] > ca.iloc[1] / cl.iloc[1]))
        pts.append(("沒有增發股票", sh is not None and len(sh) > 1 and sh.iloc[0] <= sh.iloc[1] * 1.001))
        pts.append(("毛利率上升", gp is not None and rev is not None and len(gp) > 1 and gp.iloc[0] / rev.iloc[0] > gp.iloc[1] / rev.iloc[1]))
        pts.append(("資產周轉率上升", rev is not None and len(rev) > 1 and rev.iloc[0] / ta.iloc[0] > rev.iloc[1] / ta.iloc[1]))
        return sum(1 for _, ok in pts if ok), [f"{'✓' if ok else '✗'} {n}" for n, ok in pts]
    except Exception:  # noqa: BLE001
        return None, []


def value(sym: str) -> dict:
    lines = [f"### {sym} 價值投資檢查（Graham／Piotroski／護城河指標）"]
    data: dict = {}
    if sym.startswith(("^", "FUND:")) or sym.endswith("-USD"):
        raise ValueError("價值檢查只適用個股")
    if market.FAKE:
        raise ValueError("示範資料模式沒有財報")
    info = market.info(sym)
    price = info.get("currentPrice") or info.get("regularMarketPrice") or float(_hist(sym, "3mo")["Close"].iloc[-1])
    eps, bvps = info.get("trailingEps"), info.get("bookValue")
    shares = info.get("sharesOutstanding")
    t = market._yf().Ticker(sym)
    inc, bal, cf = t.financials, t.balance_sheet, t.cashflow
    graham = math.sqrt(22.5 * eps * bvps) if eps and bvps and eps > 0 and bvps > 0 else None
    ca, tl = _row(bal, "Current Assets"), _row(bal, "Total Liabilities Net Minority Interest", "Total Liabilities")
    ncav = (ca.iloc[0] - tl.iloc[0]) / shares if ca is not None and tl is not None and shares else None
    fs, items = piotroski(inc, bal, cf)
    ni, eq = _row(inc, "Net Income"), _row(bal, "Stockholders Equity", "Common Stock Equity")
    gp, rev = _row(inc, "Gross Profit"), _row(inc, "Total Revenue")
    roes = [ni.iloc[i] / eq.iloc[i] * 100 for i in range(min(len(ni), len(eq) if eq is not None else 0, 4))] if ni is not None and eq is not None else []
    gms = [gp.iloc[i] / rev.iloc[i] * 100 for i in range(min(len(gp), len(rev), 4))] if gp is not None and rev is not None else []
    fcf = _row(cf, "Free Cash Flow")
    mcap = info.get("marketCap")
    fcf_y = fcf.iloc[0] / mcap * 100 if fcf is not None and mcap else None
    moat = []
    if roes and all(x > 15 for x in roes):
        moat.append(f"ROE 連續 {len(roes)} 年 > 15%")
    if len(gms) >= 3 and np.std(gms) < 3 and np.mean(gms) > 30:
        moat.append(f"毛利率高且穩（平均 {np.mean(gms):.1f}%、波動 {np.std(gms):.1f}）")
    de = info.get("debtToEquity")
    if de is not None and de < 50:
        moat.append(f"負債權益比低（{de:.0f}%）")
    if fcf is not None and len(fcf) >= 3 and all(x > 0 for x in fcf.iloc[:3]):
        moat.append("自由現金流連續 3 年為正")
    lines += [f"- 價格 {_f(price)}・本益比 {_f(info.get('trailingPE'))}・預估本益比 {_f(info.get('forwardPE'))}・股價淨值比 {_f(info.get('priceToBook'))}・殖利率 {_f((info.get('dividendYield') or 0) * (100 if (info.get('dividendYield') or 0) < 1 else 1), 2)}%",
              f"- **Graham 數**（√(22.5×EPS×每股淨值)）：{_f(graham)} → 價格是它的 {_f(price / graham * 100 if graham else None, 0)}%（< 100% 才在 Graham 的「合理價」內）",
              f"- **NCAV／股**（流動資產 − 總負債）：{_f(ncav)} → 價格／NCAV {_f(price / ncav if ncav and ncav > 0 else None)}（< 0.67 是 Graham 的 net-net）",
              f"- **Piotroski F-score**：{fs if fs is not None else '—'}/9（≥7 財務體質轉強、≤3 轉弱）" + (("：" + "；".join(items)) if items else ""),
              f"- **ROE（近年）**：{'、'.join(f'{x:.1f}%' for x in roes) or '—'}・**毛利率**：{'、'.join(f'{x:.1f}%' for x in gms) or '—'}・**自由現金流殖利率** {_f(fcf_y, 2)}%",
              f"- **護城河跡象**：{'；'.join(moat) or '未見明顯量化跡象（不代表沒有護城河，品牌／網路效應無法只靠財報判斷）'}"]
    data.update(graham=graham, ncav=ncav, fscore=fs, roe=roes, gross_margin=gms, moat=moat, price=price)
    return {"title": f"{sym} 價值檢查", "text": "\n".join(lines) + NOT_ADVICE, "data": data}


# ------------------------------------------------------------------ 市場情報：類股輪動、強弱、異常
def _rets(syms: list[str]) -> list[dict]:
    out = []
    data = market.download(syms, (dt.date.today() - dt.timedelta(days=400)).isoformat())
    for s in syms:
        df = data.get(s)
        if df is None or len(df) < 30:
            continue
        c = df["Close"].astype(float)
        c.index = pd.DatetimeIndex(c.index).tz_localize(None)

        def r(days):
            prior = c[c.index <= c.index[-1] - pd.Timedelta(days=days)]
            return (c.iloc[-1] / prior.iloc[-1] - 1) * 100 if len(prior) else None
        v = df["Volume"].astype(float)
        out.append({"symbol": s, "last": float(c.iloc[-1]), "d1": (c.iloc[-1] / c.iloc[-2] - 1) * 100, "w1": r(7), "m1": r(30),
                    "m3": r(91), "y1": r(365), "vol_ratio": float(v.iloc[-1] / v.iloc[-21:-1].mean()) if v.iloc[-21:-1].mean() else None,
                    "above200": bool(c.iloc[-1] > c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None})
    return out


def intel(market_: str = "all") -> dict:
    lines = ["### 市場情報"]
    data: dict = {}
    if market_ in ("all", "us"):
        sec = _rets(list(SPDR))
        sec.sort(key=lambda x: -(x["m1"] or -999))
        lines += ["", "**美股類股輪動（SPDR 類股 ETF，依 1 個月排序）**", "", "| 類股 | 1 週 | 1 月 | 3 月 | 1 年 |", "|---|---:|---:|---:|---:|"]
        for x in sec:
            lines.append(f"| {SPDR[x['symbol']]} {x['symbol']} | {_f(x['w1'], 1, True, True)} | {_f(x['m1'], 1, True, True)} | {_f(x['m3'], 1, True, True)} | {_f(x['y1'], 1, True, True)} |")
        if sec:
            lead = [SPDR[x["symbol"]] for x in sec[:3]]
            lag = [SPDR[x["symbol"]] for x in sec[-3:]]
            defensive = {"必需消費", "公用事業", "醫療保健"}
            tone = "防禦型領先（偏避險）" if len(defensive & set(lead)) >= 2 else "景氣／成長型領先（偏風險偏好）"
            lines.append(f"\n- 領先：{'、'.join(lead)}；落後：{'、'.join(lag)} → {tone}")
        data["sectors"] = sec
    for name, uni in (("台股權值", TW_BIG), ("美股大型", US_BIG)):
        if market_ not in ("all", "tw" if name == "台股權值" else "us"):
            continue
        rs = _rets(uni)
        if not rs:
            continue
        breadth = sum(1 for x in rs if x["above200"]) / max(1, sum(1 for x in rs if x["above200"] is not None)) * 100
        up = sorted(rs, key=lambda x: -x["d1"])[:5]
        dn = sorted(rs, key=lambda x: x["d1"])[:5]
        m1 = sorted([x for x in rs if x["m1"] is not None], key=lambda x: -x["m1"])[:5]
        hot = [x for x in rs if (x["vol_ratio"] or 0) >= 2]
        nm = lambda s: s.replace(".TW", "")  # noqa: E731
        lines += ["", f"**{name}（{len(rs)} 檔）**",
                  f"- 站上 200 日線比例 {breadth:.0f}%（市場廣度）",
                  "- 今日最強：" + "、".join(f"{nm(x['symbol'])} {x['d1']:+.1f}%" for x in up),
                  "- 今日最弱：" + "、".join(f"{nm(x['symbol'])} {x['d1']:+.1f}%" for x in dn),
                  "- 1 個月動能前五：" + "、".join(f"{nm(x['symbol'])} {x['m1']:+.1f}%" for x in m1),
                  "- 爆量（≥2 倍 20 日均量）：" + ("、".join(f"{nm(x['symbol'])} {x['vol_ratio']:.1f}×" for x in hot) or "無")]
        data[name] = {"breadth": breadth, "rows": rs}
    return {"title": "市場情報", "text": "\n".join(lines) + NOT_ADVICE, "data": data}


KINDS = {"tech": ("技術面", technical), "options": ("選擇權", options), "research": ("研究與籌碼", research),
         "earnings": ("財報季", earnings), "value": ("價值檢查", value)}


def run(kind: str, sym: str = "", **kw) -> dict:
    if kind == "intel":
        return intel(kw.get("market") or "all")
    if kind not in KINDS:
        raise KeyError(kind)
    if not sym:
        raise ValueError("請指定標的")
    return KINDS[kind][1](sym, **({"expiry": kw["expiry"]} if kind == "options" and kw.get("expiry") else {}))
