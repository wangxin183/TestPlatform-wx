"""TCG 可执行性自愈与自然语言日志单测。"""

from __future__ import annotations

from src.services.narrative_log import enrich_log_entry, narrate


def test_narrate_heal_and_tcg_events():
    msg = narrate(
        "heal_plan",
        plan={"action": "recover_page", "rationale": "卡在阅读器", "category": "page_stuck"},
    )
    assert "回退" in msg or "恢复" in msg
    assert "卡在阅读器" in msg

    msg2 = narrate("exec_heal_agent_patch", title="会员条", compile_status="ok")
    assert "会员条" in msg2 or "ok" in msg2


def test_enrich_log_entry_adds_message():
    row = enrich_log_entry({"event": "pytest_start", "ts": "2026-01-01T00:00:00Z"})
    assert "pytest" in row["message"] or "执行" in row["message"]
