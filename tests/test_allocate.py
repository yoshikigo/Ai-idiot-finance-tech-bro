"""組合配置（skfolio）：持股體檢、定期定額金額分配。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
pytest.importorskip("skfolio")


@pytest.fixture
def A(monkeypatch):
    from ft_common import allocate, dca
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=900)
    rng = np.random.default_rng(1)
    common = rng.normal(0, 0.01, len(idx))
    vol = {"2330.TW": 0.025, "0050.TW": 0.012, "00878.TW": 0.007, "VOO": 0.011}
    px = {}
    for k, (s, v) in enumerate(vol.items()):
        r = 0.0003 + v * (0.7 * common / 0.01 + 0.3 * rng.normal(0, 1, len(idx))) * (0.75 if s != "2330.TW" else 1)
        if s == "0050.TW":
            r = 0.0003 + 0.9 * common + rng.normal(0, 0.004, len(idx))
        px[s] = pd.Series(100 * np.cumprod(1 + r), index=idx)
    monkeypatch.setattr(dca, "prices", lambda s, start: px[s][px[s].index >= pd.Timestamp(start)])
    monkeypatch.setattr(dca, "twd_rate", lambda cur, start: None if cur == "TWD" else pd.Series(30.0, index=idx))
    return allocate, px


def test_risk_contribution_and_methods(A):
    allocate, px = A
    R, skipped = allocate.returns_twd(list(px))
    assert not skipped and R.shape[1] == 4 and len(R) > 600
    w = pd.Series({"2330.TW": 0.25, "0050.TW": 0.25, "00878.TW": 0.25, "VOO": 0.25})
    rc = allocate.risk_contrib(R, w)
    assert rc.sum() == pytest.approx(100, abs=0.01) and rc.idxmax() == "2330.TW"
    for m in ("equal", "inverse_vol", "risk_parity", "hrp", "min_cvar"):
        x = allocate.fit_weights(R, m)
        assert x.sum() == pytest.approx(1, abs=1e-6) and (x >= -1e-9).all()
    rp = allocate.fit_weights(R, "risk_parity")
    assert allocate.risk_contrib(R, rp).std() < 1.0  # equal risk contributions
    assert allocate.fit_weights(R, "inverse_vol")["2330.TW"] < 0.25 < allocate.fit_weights(R, "inverse_vol")["00878.TW"]
    capped = allocate.fit_weights(R, "min_cvar", max_weight=0.3)
    assert capped.max() <= 0.3 + 1e-6


def test_health_report(A):
    allocate, px = A
    rows = [{"symbol": "2330.TW", "value_twd": 600_000, "name": "台積電"}, {"symbol": "0050.TW", "value_twd": 200_000, "name": "元大台灣50"},
            {"symbol": "00878.TW", "value_twd": 100_000, "name": "國泰永續高股息"}, {"symbol": "VOO", "value_twd": 100_000, "name": "VOO"}]
    h = allocate.health(rows)
    assert h["current"]["2330.TW"] == pytest.approx(60) and h["risk"]["2330.TW"] > 60
    ms = {m["method"]: m for m in h["methods"]}
    assert set(ms) == set(allocate.METHODS) and all("error" not in m for m in ms.values())
    assert ms["current"]["oos"]["vol"] > ms["min_cvar"]["oos"]["vol"]
    assert any("台積電" in n and "貢獻" in n for n in h["notes"])
    with pytest.raises(ValueError):
        allocate.health(rows[:1])


def test_dca_split(A):
    allocate, px = A
    sp = allocate.dca_split(["0050.TW", "VOO", "2330.TW"], 10000, "risk_parity")
    amt = {r["symbol"]: r["amount"] for r in sp["rows"]}
    assert sum(amt.values()) == pytest.approx(10000) and all(a % 100 == 0 for a in amt.values())
    assert amt["2330.TW"] < amt["VOO"]
    eq = allocate.dca_split(["0050.TW", "VOO"], 5000, "equal")
    assert [r["amount"] for r in eq["rows"]] == [2500, 2500]
