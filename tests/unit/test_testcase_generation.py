"""用例生成：覆盖校验、压缩、token 预算组批单元测试。"""

from __future__ import annotations

from src.core.config import settings
from src.services.testcase_coverage import (
    build_fr_summaries_for_batch,
    build_slim_skill_instructions,
    compress_fr_summary,
    compress_test_point,
    pack_tp_batches_by_tokens,
    split_tp_batches,
    validate_ui_case_coverage,
)
from src.utils.analysis_logger import GenerationLogger
from src.services.testcase_generation_service import _normalize_case


def _make_tp(i: int, *, related_fr: str = "FR-001", fat: bool = False) -> dict:
    tp = {
        "id": f"TP-{i:03d}",
        "scenario": f"场景{i}",
        "priority": "高",
        "related_fr": related_fr,
        "positive_scenarios": [f"正向{i}"],
        "boundary_conditions": [],
        "negative_scenarios": [],
        "permission_scenarios": [],
        "noise_field": "should_drop",
    }
    if fat:
        tp["positive_scenarios"] = [f"正向细节{'x' * 80}-{j}" for j in range(8)]
        tp["boundary_conditions"] = [f"边界{'y' * 80}-{j}" for j in range(4)]
        tp["negative_scenarios"] = [f"异常{'z' * 80}-{j}" for j in range(4)]
    return tp


def test_validate_ui_case_coverage_ok() -> None:
    report = validate_ui_case_coverage(
        ["TP-001", "TP-002"],
        [
            {"test_point_id": "TP-001", "title": "a"},
            {"test_point_id": "TP-002", "title": "b"},
            {"test_point_id": "TP-001", "title": "c"},
        ],
    )
    assert report.ok
    assert report.missing_tp_ids == []
    assert report.case_count == 3


def test_validate_ui_case_coverage_missing() -> None:
    report = validate_ui_case_coverage(
        ["TP-001", "TP-002"],
        [{"test_point_id": "TP-001", "title": "a"}],
    )
    assert not report.ok
    assert report.missing_tp_ids == ["TP-002"]


def test_split_tp_batches() -> None:
    items = [{"id": f"TP-{i:03d}"} for i in range(1, 10)]
    batches = split_tp_batches(items, 4)
    assert len(batches) == 3
    assert len(batches[0]) == 4
    assert len(batches[-1]) == 1


def test_compress_test_point_drops_empty_and_noise() -> None:
    raw = _make_tp(1)
    raw["boundary_conditions"] = []
    compressed = compress_test_point(raw)
    assert compressed["id"] == "TP-001"
    assert "noise_field" not in compressed
    assert "boundary_conditions" not in compressed
    assert compressed["positive_scenarios"] == ["正向1"]


def test_compress_test_point_trims_long_scenarios() -> None:
    raw = _make_tp(2, fat=True)
    compressed = compress_test_point(raw)
    assert len(compressed["positive_scenarios"]) == 6
    assert all(len(s) <= 201 for s in compressed["positive_scenarios"])


def test_build_fr_summaries_dedup() -> None:
    fr_map = {
        "FR-001": {
            "id": "FR-001",
            "module": "登录",
            "description": "用户登录" + ("详" * 200),
            "priority": "高",
        },
        "FR-002": {
            "id": "FR-002",
            "module": "注册",
            "description": "用户注册",
            "priority": "中",
        },
    }
    batch = [_make_tp(1, related_fr="FR-001"), _make_tp(2, related_fr="FR-001"), _make_tp(3, related_fr="FR-002")]
    summaries = build_fr_summaries_for_batch(batch, fr_map)
    assert len(summaries) == 2
    assert summaries[0]["id"] == "FR-001"
    assert summaries[0]["description"].endswith("…")
    assert len(summaries[0]["description"]) <= 121


def test_compress_fr_summary() -> None:
    s = compress_fr_summary({"id": "FR-1", "module": "M", "description": "短", "priority": "低"})
    assert s == {"id": "FR-1", "module": "M", "description": "短", "priority": "低"}


def test_pack_respects_max_tps_per_batch() -> None:
    tps = [_make_tp(i) for i in range(1, 31)]
    batches = pack_tp_batches_by_tokens(
        tps,
        {},
        max_tps_per_batch=12,
        target_input_tokens=100000,
        fixed_overhead_tokens=100,
    )
    assert all(len(b) <= 12 for b in batches)
    assert sum(len(b) for b in batches) == 30
    assert len(batches) == 3


def test_pack_respects_token_budget() -> None:
    """用假估算器：每条 TP 占 1000 token，预算 2500 → 每批最多 2 条。"""

    def fake_est(batch, fr_map, *, fixed_overhead_tokens: int) -> int:
        return fixed_overhead_tokens + len(batch) * 1000

    tps = [_make_tp(i) for i in range(1, 11)]
    batches = pack_tp_batches_by_tokens(
        tps,
        {},
        max_tps_per_batch=12,
        target_input_tokens=2500,
        fixed_overhead_tokens=100,
        estimate_fn=fake_est,
    )
    assert all(len(b) <= 2 for b in batches)
    assert sum(len(b) for b in batches) == 10
    assert len(batches) == 5


def test_pack_always_places_at_least_one_tp() -> None:
    """单条超预算时仍单独成批，不丢弃。"""

    def huge_est(batch, fr_map, *, fixed_overhead_tokens: int) -> int:
        return 999999

    tps = [_make_tp(1), _make_tp(2)]
    batches = pack_tp_batches_by_tokens(
        tps,
        {},
        max_tps_per_batch=12,
        target_input_tokens=100,
        fixed_overhead_tokens=50,
        estimate_fn=huge_est,
    )
    assert len(batches) == 2
    assert [b[0]["id"] for b in batches] == ["TP-001", "TP-002"]


def test_pack_193_ui_tps_batch_count_under_20() -> None:
    """对齐 TCG-0002 规模：193 TP 应远少于旧固定 4 的 49 批。"""
    tps = [_make_tp(i, related_fr=f"FR-{(i % 20) + 1:03d}") for i in range(1, 194)]
    fr_map = {
        f"FR-{i:03d}": {
            "id": f"FR-{i:03d}",
            "module": f"模块{i}",
            "description": f"描述{i}",
            "priority": "中",
        }
        for i in range(1, 21)
    }
    cfg = settings.testcase_generation
    batches = pack_tp_batches_by_tokens(
        tps,
        fr_map,
        max_tps_per_batch=cfg.max_tps_per_batch,
        target_input_tokens=cfg.target_input_tokens,
        fixed_overhead_tokens=cfg.fixed_overhead_tokens,
    )
    assert sum(len(b) for b in batches) == 193
    assert all(len(b) <= cfg.max_tps_per_batch for b in batches)
    assert len(batches) <= 20
    assert len(batches) < 49


def test_build_slim_skill_instructions() -> None:
    text = build_slim_skill_instructions(platform_type="web", custom_prompt="覆盖登录")
    assert "硬规则" in text
    assert "web" in text
    assert "覆盖登录" in text
    assert "SKILL.md" not in text
    assert len(text) < 2000


def test_settings_testcase_generation_loaded() -> None:
    cfg = settings.testcase_generation
    assert cfg.max_tps_per_batch == 12
    assert cfg.target_input_tokens == 7000
    assert cfg.max_concurrency == 3
    assert cfg.fixed_overhead_tokens == 800


def test_normalize_case_requires_and_canonicalizes_module() -> None:
    case = _normalize_case(
        {
            "title": "阅读器-左右翻页",
            "module": "漫画阅读器（潘媛）",
            "steps": [
                {
                    "step": 1,
                    "action": "向左滑动阅读区域",
                    "expected": "页码由「1/145话」更新为「2/145话」",
                }
            ],
            "test_point_id": "TP-044",
            "automation_level": "ready",
        },
        "android",
    )
    assert case is not None
    assert case["module"] == "漫画阅读器"
    assert case["automation_level"] == "ready"


def test_normalize_case_infers_module_from_title() -> None:
    case = _normalize_case(
        {
            "title": "漫画详情-开始阅读",
            "steps": [
                {
                    "step": 1,
                    "action": "点击「开始阅读」",
                    "expected": "进入漫画阅读器",
                }
            ],
        },
        "android",
    )
    assert case is not None
    assert case["module"] == "漫画详情页"


def test_normalize_case_repairs_common_non_executable_actions() -> None:
    case = _normalize_case(
        {
            "title": "漫画阅读器会员条展示",
            "module": "漫画阅读器",
            "preconditions": "",
            "steps": [
                {
                    "step": 1,
                    "action": "使用非会员账号登录 App",
                    "expected": "登录成功",
                },
                {
                    "step": 2,
                    "action": "进入漫画 tab",
                    "expected": "漫画频道显示",
                },
                {
                    "step": 3,
                    "action": "点击「开始阅读」",
                    "expected": "进入漫画阅读器",
                },
                {
                    "step": 4,
                    "action": "观察阅读器内会员条",
                    "expected": "会员条显示",
                },
            ],
        },
        "android",
    )
    assert case is not None
    assert "已使用非会员账号登录 App" in case["preconditions"]
    assert [step["step"] for step in case["steps"]] == [1, 2, 3]
    assert case["steps"][0]["action"] == "点击底部「漫画」tab"
    assert case["steps"][1]["action"] == "点击「开始阅读」按钮"
    assert case["steps"][2]["action"] == "确认阅读器内会员条可见"


def test_slim_generation_rules_forbid_non_executable_verbs() -> None:
    text = build_slim_skill_instructions(platform_type="android")
    assert "禁止使用「进入、观察、使用账号登录」作为 action" in text
    assert "前置条件" in text
    assert "固定测试数据" in text
    assert "模块入口由执行器负责" in text
    assert "不要生成进入模块的导航步骤" in text


def test_normalize_case_strips_agent_generated_module_navigation_prefix() -> None:
    case = _normalize_case(
        {
            "title": "非会员阅读器会员条展示",
            "module": "漫画阅读器",
            "steps": [
                {
                    "step": 1,
                    "action": "点击底部「漫画」tab",
                    "expected": "漫画频道显示",
                },
                {
                    "step": 2,
                    "action": "点击漫画频道列表中首条漫画卡片",
                    "expected": "漫画阅读器加载完成",
                },
                {
                    "step": 3,
                    "action": "确认阅读器内会员条可见",
                    "expected": "会员条可见",
                },
            ],
            "step_contracts": [
                {
                    "step": 1,
                    "start_state": "home_main",
                    "expected_transition": "home_main -> comic_channel",
                },
                {
                    "step": 2,
                    "start_state": "comic_channel",
                    "expected_transition": "comic_channel -> reader_main",
                },
                {
                    "step": 3,
                    "start_state": "reader_main",
                    "expected_transition": "reader_main -> reader_main",
                },
            ],
        },
        "android",
    )

    assert case is not None
    assert [step["action"] for step in case["steps"]] == ["确认阅读器内会员条可见"]
    assert case["steps"][0]["step"] == 1
    assert case["step_contracts"][0]["step"] == 1
    assert case["step_contracts"][0]["start_state"] == "reader_main"


def test_normalize_case_keeps_mid_case_return_from_member_page() -> None:
    """已在 reader_main 开始的用例，member_page→reader_main 返回不得当入口剥掉。"""
    case = _normalize_case(
        {
            "title": "从会员页返回阅读器",
            "module": "漫画阅读器",
            "test_point_id": "TP-010",
            "steps": [
                {
                    "step": 1,
                    "action": "点击会员条「开通」",
                    "expected": "跳转会员页",
                },
                {
                    "step": 2,
                    "action": "点击系统返回键",
                    "expected": "回到阅读器且进度不变",
                },
            ],
            "step_contracts": [
                {
                    "step": 1,
                    "start_state": "reader_main",
                    "expected_transition": "reader_main -> member_page",
                },
                {
                    "step": 2,
                    "start_state": "member_page",
                    "expected_transition": "member_page -> reader_main",
                },
            ],
        },
        "android",
    )

    assert case is not None
    assert [step["action"] for step in case["steps"]] == [
        "点击会员条「开通」",
        "点击系统返回键",
    ]
    assert case["step_contracts"][1]["start_state"] == "member_page"
    assert case["step_contracts"][1]["expected_transition"] == (
        "member_page -> reader_main"
    )


def test_normalize_case_keeps_sole_return_step_when_strip_would_empty() -> None:
    """仅含「外部态→主状态」且剥完无剩余时，保留原步骤以免 TP 被丢弃。"""
    case = _normalize_case(
        {
            "title": "仅返回阅读器",
            "module": "漫画阅读器",
            "test_point_id": "TP-010",
            "steps": [
                {
                    "step": 1,
                    "action": "点击系统返回键",
                    "expected": "回到阅读器",
                },
            ],
            "step_contracts": [
                {
                    "step": 1,
                    "start_state": "member_page",
                    "expected_transition": "member_page -> reader_main",
                },
            ],
        },
        "android",
    )

    assert case is not None
    assert len(case["steps"]) == 1
    assert case["steps"][0]["action"] == "点击系统返回键"
    assert case["step_contracts"][0]["start_state"] == "member_page"


def test_generation_logger_jsonl(tmp_path, monkeypatch) -> None:
    import src.utils.analysis_logger as mod

    monkeypatch.setattr(mod, "TCG_STORAGE_BASE", tmp_path)
    glog = GenerationLogger("TCG-9999")
    glog._dir = tmp_path / "TCG-9999"
    glog._log_path = glog._dir / "generation.log"
    glog._seq = 0
    glog.log("task_created", analysis_id="RA-0001", tp_count=2)
    glog.save_snapshot("SKILL_used.md", "# skill")
    logs = glog.read_logs()
    assert len(logs) == 1
    assert logs[0]["step"] == "task_created"
    assert (glog.dir_path / "SKILL_used.md").exists()
