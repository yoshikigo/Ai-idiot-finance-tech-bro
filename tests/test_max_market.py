"""MAX（MaiCoin）台幣交易對（USDT-TWD）：Yahoo 沒有，改抓 MAX 公開 K 線。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from ft_common import dca, market  # noqa: E402

ROWS = [[1794355200, 33.006, 33.006, 32.885, 32.96, 4435957.83],
        [1794441600, 32.961, 33.057, 32.902, 33.01, 7256805.1],
        [1794528000, 33.011, 33.05, 32.916, 33.024, 7842639.68]]


class _R:
    def __init__(self, data):
        self._d = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._d


def test_max_history_and_quote(monkeypatch):
    import httpx
    calls = []

    def get(url, params=None, **kw):
        calls.append(params)
        return _R([r for r in ROWS if r[0] >= params["timestamp"]][: params["limit"]])

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(market, "FAKE", False)
    market._cache.clear()
    df = market._max_history("USDT-TWD", "5d", "1d", "2026-11-10", "2026-11-14")
    assert calls[0]["market"] == "usdttwd" and calls[0]["period"] == 1440
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(df) == 3 and df["Close"].iloc[-1] == 33.024
    assert str(df.index.tz) == "Asia/Taipei"
    market._cache.clear()
    monkeypatch.setattr(market, "history", lambda *a, **k: df)
    q = market.quotes(["USDT-TWD"])[0]
    assert q["last"] == 33.024 and round(q["chg"], 3) == 0.014 and q["currency"] == "TWD"
    assert dca.currency_of("USDT-TWD") == "TWD"


BITO = {"data": [{"timestamp": r[0] * 1000, "open": str(r[1]), "high": str(r[2]), "low": str(r[3]), "close": str(r[4]),
                  "volume": str(r[5])} for r in ROWS] + [{"timestamp": 1794614400000, "open": "33.036", "high": "33.036",
                                                         "low": "33.036", "close": "33.036", "volume": "0"}]}


def test_bitopro_history(monkeypatch):
    import httpx
    calls = []

    def get(url, params=None, **kw):
        calls.append((url, params))
        return _R(BITO if params["from"] <= ROWS[0][0] else {"data": []})

    monkeypatch.setattr(httpx, "get", get)
    df = market._twx_history("USDT-TWD.BITO", "5d", "1d", "2026-11-10", "2026-11-15")
    assert calls[0][0].endswith("/usdt_twd") and calls[0][1]["resolution"] == "1d"
    assert len(df) == 4 and df["Close"].iloc[-1] == 33.036  # last zero-volume bar kept (current period)
    assert dca.currency_of("USDT-TWD.BITO") == "TWD"
