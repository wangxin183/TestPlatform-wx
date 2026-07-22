"""UI 测试点 → 测试用例覆盖校验与动态组批。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from src.agent_runtime.cli_shared import estimate_tokens


@dataclass
class CaseCoverageReport:
    ok: bool
    selected_tp_ids: list[str] = field(default_factory=list)
    covered_tp_ids: list[str] = field(default_factory=list)
    missing_tp_ids: list[str] = field(default_factory=list)
    case_count: int = 0
    summary: str = ""


# 注入每批 prompt 的精简指令（完整 SKILL.md 仅落盘审计，不整篇重复）
SLIM_SKILL_INSTRUCTIONS = """# UI 测试用例生成（测试点驱动）

根据本批 UI 测试点生成可执行、可判定、可追溯的 UI 测试用例。

## 硬规则
1. 仅输出 `test_type` 为 `ui` 的用例；忽略非 UI 测试点。
2. 每个测试点至少 1 条正向用例；若存在非空的 boundary/negative/permission 场景数组，对应维度至少各 1 条。
3. steps 的 action/expected 必须具体可判定，禁止「功能正常」「符合预期」。
4. 每条用例必须带正确的 `test_point_id`，可带 `related_fr`。
5. priority 用：严重/高/中/低。
6. 每条必须带 `module`，且只能取 ACN 一级模块：
Push/个人主页/动漫频道/动画半播页/图文帖详情页/圈子/安装启动/我的/搜索/消息/漫单详情页/漫画详情页/漫画阅读器/漫荒详情页/短视频横屏播放/短视频竖屏播放/社区/管控演练/视频帖子详情页/追更/长图帖详情页。
7. 模块入口由执行器负责；不要生成进入模块的导航步骤。`steps` 从已到达 module 页面后的首个业务操作或断言开始，同模块执行时平台会复用页面会话。
8. 每条必须带 `automation_level`（ready/semi/manual）、`precondition_spec`（login_state/user_type/entry_context）和逐步 `step_contracts`。
9. action 只能使用原子动作：点击、输入、滑动、等待、确认、检查、返回。
10. 禁止使用「进入、观察、使用账号登录」作为 action：
   - “进入漫画 tab”必须写成“点击底部「漫画」tab”；
   - “观察会员条”必须写成“确认会员条可见”；
11. expected 必须用「」包裹关键可见文案；负向写「不出现「xxx」」；禁止无引号锚点的「按钮显示/不展示」；跨页写清跳转至…页；禁止单独「流畅/符合预期」。句式示例见 Skill 目录 `examples.md`。
12. entry_context 取值：module_default/comic.member_free/comic.member_discount/comic.pay_per_episode/comic.free/comic.wait_free/anime.member_free/anime.free/reader.horizontal/reader.vertical。
   - 账号、登录态、设备状态写入前置条件；「已进入/已登录」由 entry_context/login_state 承接，不要在 steps 重复入口导航。
13. 禁止“任一、某个、相关、合适”等模糊目标。优先使用需求/测试点提供的固定测试数据；没有固定测试数据时标记 `agent_required`，不得臆造名称或 locator。
14. step_contracts 的 postconditions：正向 `text_visible:文案`，负向 `text_absent:文案`。

## 输出
只输出 JSON 数组，不要 markdown 代码块，不要其他文字：
[{"title":"...","description":"...","preconditions":"先进入模块页面","steps":[{"step":1,"action":"...","expected":"..."}],"priority":"高","test_type":"ui","tags":["..."],"platform_type":"{platform_type}","test_point_id":"TP-001","related_fr":"FR-001","module":"漫画阅读器","automation_level":"ready","step_contracts":[{"step":1,"start_state":"reader_main","intent":"...","target":{"description":"..."},"expected_transition":"reader_main -> reader_main","postconditions":["..."]}]}]
"""


def validate_ui_case_coverage(
    selected_tp_ids: list[str],
    cases: list[dict],
) -> CaseCoverageReport:
    """校验每个选中的 UI 测试点至少有 1 条用例。"""
    selected = [str(x).strip() for x in selected_tp_ids if str(x).strip()]
    covered: set[str] = set()
    for case in cases:
        tp = str(case.get("test_point_id") or "").strip()
        if tp:
            covered.add(tp)

    missing = [tp for tp in selected if tp not in covered]
    ok = len(missing) == 0 and len(cases) > 0
    return CaseCoverageReport(
        ok=ok,
        selected_tp_ids=selected,
        covered_tp_ids=sorted(covered),
        missing_tp_ids=missing,
        case_count=len(cases),
        summary=(
            f"选中TP={len(selected)}, 用例={len(cases)}, "
            f"已覆盖={len(covered)}, 缺失={len(missing)}"
        ),
    )


def split_tp_batches(test_points: list[dict], batch_size: int = 4) -> list[list[dict]]:
    """将测试点按固定大小分批（兼容旧逻辑）。"""
    if batch_size <= 0:
        batch_size = 4
    return [
        test_points[i : i + batch_size]
        for i in range(0, len(test_points), batch_size)
    ]


def compress_test_point(tp: dict) -> dict:
    """压缩单条 TP：只保留生成所需字段，去掉空场景数组。"""
    out: dict = {
        "id": tp.get("id"),
        "scenario": tp.get("scenario") or "",
        "priority": tp.get("priority") or "",
        "related_fr": tp.get("related_fr") or "",
    }
    if tp.get("related_nfr"):
        out["related_nfr"] = tp.get("related_nfr")
    for key in (
        "positive_scenarios",
        "boundary_conditions",
        "negative_scenarios",
        "permission_scenarios",
    ):
        vals = tp.get(key)
        if isinstance(vals, list) and vals:
            trimmed = []
            for v in vals[:6]:
                s = str(v).strip()
                if len(s) > 200:
                    s = s[:200] + "…"
                trimmed.append(s)
            out[key] = trimmed
    return out


def compress_fr_summary(fr: dict) -> dict:
    """压缩 FR 摘要。"""
    desc = str(fr.get("description") or "")
    if len(desc) > 120:
        desc = desc[:120] + "…"
    return {
        "id": fr.get("id"),
        "module": fr.get("module") or "",
        "description": desc,
        "priority": fr.get("priority") or "",
    }


def build_fr_summaries_for_batch(
    batch: list[dict],
    fr_map: dict,
) -> list[dict]:
    """按本批 related_fr 去重后生成 FR 摘要。"""
    seen: set[str] = set()
    out: list[dict] = []
    for tp in batch:
        rid = str(tp.get("related_fr") or "").strip()
        if not rid or rid in seen or rid not in fr_map:
            continue
        seen.add(rid)
        out.append(compress_fr_summary(fr_map[rid]))
    return out


def estimate_batch_payload_tokens(
    batch: list[dict],
    fr_map: dict,
    *,
    fixed_overhead_tokens: int,
) -> int:
    """估算一批（压缩后）可变部分 + 固定开销的 token。"""
    compressed = [compress_test_point(tp) for tp in batch]
    frs = build_fr_summaries_for_batch(batch, fr_map)
    payload = json.dumps(
        {"tps": compressed, "frs": frs},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return fixed_overhead_tokens + estimate_tokens(payload)


def pack_tp_batches_by_tokens(
    test_points: list[dict],
    fr_map: dict | None = None,
    *,
    max_tps_per_batch: int = 12,
    target_input_tokens: int = 7000,
    fixed_overhead_tokens: int = 800,
    estimate_fn: Callable[..., int] | None = None,
) -> list[list[dict]]:
    """按 token 预算动态组批。

    - 单批不超过 max_tps_per_batch
    - 单批估算输入不超过 target_input_tokens（至少放入 1 条 TP）
    """
    if not test_points:
        return []
    if max_tps_per_batch <= 0:
        max_tps_per_batch = 12
    if target_input_tokens <= 0:
        target_input_tokens = 7000

    fr_map = fr_map or {}
    estimator = estimate_fn or estimate_batch_payload_tokens

    batches: list[list[dict]] = []
    current: list[dict] = []

    for tp in test_points:
        candidate = current + [tp]
        if len(candidate) > max_tps_per_batch:
            if current:
                batches.append(current)
            current = [tp]
            continue

        tokens = estimator(
            candidate,
            fr_map,
            fixed_overhead_tokens=fixed_overhead_tokens,
        )
        if current and tokens > target_input_tokens:
            batches.append(current)
            current = [tp]
        else:
            current = candidate

    if current:
        batches.append(current)
    return batches


def build_slim_skill_instructions(platform_type: str = "", custom_prompt: str = "") -> str:
    """构建注入每批 prompt 的精简指令。"""
    text = SLIM_SKILL_INSTRUCTIONS.replace(
        "{platform_type}", platform_type or "通用"
    )
    extra = (custom_prompt or "").strip() or "无"
    return (
        f"{text}\n\n"
        f"目标平台：{platform_type or '通用'}\n"
        f"用户额外要求：{extra}\n"
    )
