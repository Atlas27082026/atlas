from types import SimpleNamespace

from core.decision_audit import DecisionAuditWriter


def test_decision_audit_writes_required_fields(tmp_path):
    path = tmp_path / "decision_audit.csv"
    writer = DecisionAuditWriter(path)
    result = SimpleNamespace(
        symbol="JSWSTEEL",
        direction="BULL",
        model="TREND_CONTINUATION",
        metrics={
            "market_state": "BULL",
            "trade_quality_score": 82.5,
            "macro_ok": True,
            "rvol_ok": True,
            "entry_trigger_ok": True,
        },
    )

    writer.append(result, "OK", "ENTRY_ACCEPTED", "OK")

    text = path.read_text(encoding="utf-8")
    assert "symbol,direction,model,market_state,quality_score" in text
    assert "JSWSTEEL,BULL,TREND_CONTINUATION,BULL,82.5,True,True,True,OK,ENTRY_ACCEPTED,OK" in text
