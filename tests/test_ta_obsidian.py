"""TradingAgents → Obsidian: one note per analyst under trading-agent/<run time> <ticker>/."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))


def test_run_folder_and_analyst_notes(tmp_path, monkeypatch):
    from ft_common import notes
    monkeypatch.setattr(notes, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(notes, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(notes, "_name", lambda s: "台積電")
    folder = notes.ta_run_folder("20261027-163612-ab12cd", "2330.TW")
    assert folder == "trading-agent/2026-10-27 16.36 2330.TW 台積電"
    meta = {"ticker": "2330.TW", "name": "台積電", "date": "2026-10-27", "run": "2026-10-27 16:36", "job_id": "j", "model": "m"}
    rel = notes.write_ta_analyst(folder, "技術分析師", "## 技術面\n站上月線", meta)
    assert rel == f"{folder}/技術分析師.md"
    text = (tmp_path / "outbox" / rel).read_text()
    assert "analyst: \"技術分析師\"" in text and "站上月線" in text and "# 技術分析師｜2330.TW 台積電" in text
    notes.write_ta_analyst(folder, "新聞分析師", "新聞內容", {**meta, "reused": "沿用 10-27 16:00 的報告"})
    assert "沿用" in (tmp_path / "outbox" / folder / "新聞分析師.md").read_text()
    s = notes.write_ta_summary(folder, {**meta, "status": "done", "signal": "Buy"}, ["技術分析師", "新聞分析師"],
                               [("最終決策", "買進")])
    out = (tmp_path / "outbox" / s).read_text()
    assert f"[[{folder}/技術分析師|技術分析師]]" in out and "評等**：Buy" in out and "買進" in out


def test_respects_obsidian_off(tmp_path, monkeypatch):
    from ft_common import notes
    monkeypatch.setattr(notes, "OUTBOX", tmp_path / "outbox")
    monkeypatch.setattr(notes, "SETTINGS", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(json.dumps({"obsidian_auto": False}))
    assert notes.write_ta_analyst("trading-agent/x", "技術分析師", "r", {}) is None
    assert not (tmp_path / "outbox").exists()
