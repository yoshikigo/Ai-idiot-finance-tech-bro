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
