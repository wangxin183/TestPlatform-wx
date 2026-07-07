"""自愈编排器 — 处理 Agent 执行失败时的自动诊断、修正和重试。

Supports:
  基础设施故障 → Python 侧退避重试 × 1 → 切换 Agent 回退
  输出格式故障 → Agent 自诊断 × 1 → 修正 JSON

日志全部通过 AnalysisLogger 确定性记录，飞书通知通过 FeishuNotifier 发送。

Usage:
    from src.services.self_healing import SelfHealingOrchestrator, FailureInfo, HealingContext

    healer = SelfHealingOrchestrator(cli, feishu)
    result = await healer.handle(failure_info, context, alog)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.services.agent_cli import AgentCLI, CLICallResult, JSONExtractResult
from src.services.feishu_notifier import FeishuNotifier
from src.utils.analysis_logger import AnalysisLogger
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ============================================================
# 配置常量
# ============================================================

MAX_SELF_HEAL_ATTEMPTS = 1        # 最大自愈尝试次数（每类故障，节约 token）
INFRA_RETRY_DELAYS = [0]           # 基础设施退避间隔（秒），仅立即重试 1 次
DIAGNOSIS_TIMEOUT = 180           # 自诊断 Agent 超时（秒）
OUTPUT_QUALITY_MIN_FR = 1         # 最少功能需求数
OUTPUT_QUALITY_MIN_TP = 1         # 最少测试点数

# ============================================================
# 枚举与数据类
# ============================================================


class FailureCategory(str, Enum):
    """失败类别"""
    INFRA_TIMEOUT = "infra_timeout"           # CLI 执行超时
    INFRA_CLI_ERROR = "infra_cli_error"       # CLI 非零退出码
    INFRA_CLI_NOT_FOUND = "infra_cli_not_found"  # CLI 二进制不存在
    OUTPUT_PARSE = "output_parse"             # JSON 解析失败
    OUTPUT_TYPE = "output_type"               # JSON 类型错误（list/非dict）
    OUTPUT_QUALITY = "output_quality"         # JSON 内容质量异常
    UNKNOWN = "unknown"


@dataclass
class FailureInfo:
    """描述一次失败的完整信息"""
    category: FailureCategory
    step_name: str = ""                       # 失败的步骤名（如 "claude_analysis"）
    agent_tool: str = ""                      # 当前使用的 Agent 工具（claude / codex）
    error_message: str = ""                   # 错误信息
    exit_code: int = -1                       # CLI 退出码
    raw_output: str = ""                      # 原始输出（用于自诊断）
    prompt: str = ""                          # 原始发送的 prompt（用于基础设施重试）


@dataclass
class HealingContext:
    """自愈所需的原始上下文（用于重建 prompt 和自诊断）"""
    analysis_id: str = ""
    doc_md: str = ""                          # 原始需求文档 Markdown
    doc_summary: str = ""                     # 文档摘要（前 500 字，用于自诊断）
    skill_body: str = ""                      # 原始 SKILL.md 内容
    knowledge_context: str = ""               # 知识库上下文文本
    platform_type: str = ""                   # 平台类型
    custom_prompt: str = ""                   # 用户自定义提示
    review_skill_body: str = ""               # 审查 SKILL（用于 Codex 审查步骤）
    original_analysis_json: dict | None = None  # 步骤 4 的产出（用于步骤 5 失败重试）


@dataclass
class HealingResult:
    """自愈结果"""
    success: bool
    output: Any = None                        # 修正后的输出数据（dict/list）
    raw_output: str = ""                      # Agent 的原始文本输出
    total_attempts: int = 0                   # 总尝试次数
    resolve_method: str = ""                  # 解决方式：infra_retry | agent_switch | self_diagnosis
    diagnosis_report: dict | None = None      # Agent 自诊断报告（含 root_cause）
    final_error: str = ""


# ============================================================
# SelfHealingOrchestrator
# ============================================================


class SelfHealingOrchestrator:
    """编排失败恢复流程。

    每种失败类型独立处理，保持 a-log 全链路日志和飞书通知。
    """

    def __init__(self, cli: AgentCLI, feishu: FeishuNotifier):
        self.cli = cli
        self.feishu = feishu

    # ---- 公共入口 ----

    async def handle(
        self,
        failure: FailureInfo,
        context: HealingContext,
        alog: AnalysisLogger,
    ) -> HealingResult:
        """统一自愈入口。

        根据失败类别分派到基础设施重试或 Agent 自诊断。
        """
        alog.log(
            "self_heal_start",
            failure_category=failure.category.value,
            step_name=failure.step_name,
            agent_tool=failure.agent_tool,
            error=failure.error_message[:200],
            max_infra_retries=len(INFRA_RETRY_DELAYS),
            max_diagnosis_attempts=MAX_SELF_HEAL_ATTEMPTS,
        )

        if failure.category in (
            FailureCategory.INFRA_TIMEOUT,
            FailureCategory.INFRA_CLI_ERROR,
            FailureCategory.INFRA_CLI_NOT_FOUND,
        ):
            return await self._handle_infrastructure(failure, context, alog)
        else:
            return await self._handle_output_diagnosis(failure, context, alog)

    # ================================================================
    # 基础设施故障 — 退避重试 → 切换 Agent
    # ================================================================

    async def _handle_infrastructure(
        self,
        failure: FailureInfo,
        context: HealingContext,
        alog: AnalysisLogger,
    ) -> HealingResult:
        """基础设施故障恢复：退避重试 1 次 + Agent 切换回退 1 次。"""
        current_tool = failure.agent_tool

        # ── 阶段 1：退避重试 ──
        for idx, delay in enumerate(INFRA_RETRY_DELAYS):
            attempt = idx + 1

            await self.feishu.notify_text(
                f"🔄 自动重试 | 任务={context.analysis_id} | "
                f"步骤={failure.step_name} | 第{attempt}/{len(INFRA_RETRY_DELAYS)}次 | "
                f"原因={failure.error_message[:60]}"
            )

            alog.log(
                "self_heal_infra_retry",
                attempt=attempt,
                max_attempts=len(INFRA_RETRY_DELAYS),
                delay_s=delay,
                agent_tool=current_tool,
                retry_prompt_len=len(failure.prompt),
            )

            if delay > 0:
                await asyncio.sleep(delay)

            # 使用动态超时重试
            estimated_tokens = AgentCLI.estimate_tokens(failure.prompt)
            timeout = AgentCLI.dynamic_timeout(estimated_tokens)

            if current_tool == "claude":
                result = await self.cli.claude(
                    prompt=failure.prompt,
                    timeout=timeout,
                )
            else:
                result = await self.cli.codex(
                    prompt=failure.prompt,
                    timeout=timeout,
                )

            alog.log(
                "self_heal_infra_retry_done",
                attempt=attempt,
                success=result.success,
                exit_code=result.exit_code,
                output_len=len(result.raw_output) if result.success else 0,
                error=result.error[:150] if not result.success else "",
                timeout_used=timeout,
            )

            if result.success:
                alog.log(
                    "self_heal_complete",
                    total_attempts=attempt,
                    outcome="success",
                    resolve_method="infra_retry",
                )
                return HealingResult(
                    success=True,
                    raw_output=result.raw_output,
                    total_attempts=attempt,
                    resolve_method="infra_retry",
                )

        # ── 阶段 2：3 次全失败 → 切换 Agent 回退 ──
        fallback_tool = "codex" if current_tool == "claude" else "claude"
        fallback_attempt = len(INFRA_RETRY_DELAYS) + 1

        await self.feishu.notify_text(
            f"🔄 切换Agent | 任务={context.analysis_id} | "
            f"{current_tool} 连续 {len(INFRA_RETRY_DELAYS)} 次失败 → 切换 {fallback_tool} 执行"
        )

        alog.log(
            "self_heal_agent_switch",
            from_agent=current_tool,
            to_agent=fallback_tool,
            attempt=fallback_attempt,
            reason=f"{current_tool} 基础设施连续 {len(INFRA_RETRY_DELAYS)} 次失败",
        )

        estimated_tokens = AgentCLI.estimate_tokens(failure.prompt)
        timeout = AgentCLI.dynamic_timeout(estimated_tokens)

        if fallback_tool == "claude":
            result = await self.cli.claude(
                prompt=failure.prompt,
                timeout=timeout,
            )
        else:
            result = await self.cli.codex(
                prompt=failure.prompt,
                timeout=timeout,
            )

        alog.log(
            "self_heal_agent_switch_done",
            fallback_tool=fallback_tool,
            success=result.success,
            exit_code=result.exit_code,
            output_len=len(result.raw_output) if result.success else 0,
            error=result.error[:150] if not result.success else "",
        )

        if result.success:
            alog.log(
                "self_heal_complete",
                total_attempts=fallback_attempt,
                outcome="success",
                resolve_method="agent_switch",
            )
            return HealingResult(
                success=True,
                raw_output=result.raw_output,
                total_attempts=fallback_attempt,
                resolve_method="agent_switch",
            )

        # ── 阶段 3：全部失败 ──
        alog.log(
            "self_heal_exhausted",
            total_attempts=fallback_attempt,
            attempts_detail=[
                *(f"infra_retry_{current_tool}:{failure.error_message[:40]}" for _ in INFRA_RETRY_DELAYS),
                f"agent_switch_{fallback_tool}:{result.error[:40]}",
            ],
            decision="mark_failed",
        )

        await self.feishu.notify_failed(
            analysis_id=context.analysis_id,
            stage_name=failure.step_name,
            error_summary=f"自愈失败：{current_tool} 退避重试 {len(INFRA_RETRY_DELAYS)} "
                          f"次 + 切换 {fallback_tool} 回退 1 次均失败",
        )

        return HealingResult(
            success=False,
            total_attempts=fallback_attempt,
            resolve_method="exhausted",
            final_error=f"基础设施自愈耗尽：{current_tool}×{len(INFRA_RETRY_DELAYS)} + {fallback_tool}×1 全失败",
        )

    # ================================================================
    # 输出故障 — Agent 自诊断 × 3
    # ================================================================

    async def _handle_output_diagnosis(
        self,
        failure: FailureInfo,
        context: HealingContext,
        alog: AnalysisLogger,
    ) -> HealingResult:
        """输出故障恢复：Agent 自诊断根因并输出修正后的 JSON。"""
        current_tool = failure.agent_tool

        for attempt in range(1, MAX_SELF_HEAL_ATTEMPTS + 1):
            alog.log(
                "self_heal_diagnosis_attempt",
                attempt=attempt,
                max_attempts=MAX_SELF_HEAL_ATTEMPTS,
                failure_category=failure.category.value,
            )

            # 构建诊断提示词
            diagnosis_prompt = self._build_diagnosis_prompt(failure, context, attempt)
            alog.save_snapshot(f"self_heal_diagnosis_prompt_{attempt}.txt", diagnosis_prompt)

            alog.log(
                "self_heal_diagnosis_start",
                attempt=attempt,
                agent=current_tool,
                diagnosis_prompt_len=len(diagnosis_prompt),
            )

            # 使用当前工具执行自诊断（Agent 可在输出中建议换工具）
            timeout = DIAGNOSIS_TIMEOUT
            if current_tool == "claude":
                diag_result = await self.cli.claude(
                    prompt=diagnosis_prompt,
                    timeout=timeout,
                )
            else:
                diag_result = await self.cli.codex(
                    prompt=diagnosis_prompt,
                    timeout=timeout,
                )

            if not diag_result.success:
                alog.log(
                    "self_heal_diagnosis_failed",
                    attempt=attempt,
                    error=diag_result.error[:200],
                    exit_code=diag_result.exit_code,
                )
                continue

            alog.save_snapshot(f"self_heal_diagnosis_raw_{attempt}.txt", diag_result.raw_output)

            # 解析诊断结果
            diag_json = self.cli.extract_json(diag_result.raw_output)
            if not diag_json.success or not isinstance(diag_json.data, dict):
                alog.log(
                    "self_heal_diagnosis_parse_failed",
                    attempt=attempt,
                    error=diag_json.error[:200],
                )
                continue

            diag_data = diag_json.data
            diagnosis = diag_data.get("diagnosis", {})
            corrected = diag_data.get("corrected_output")

            root_cause = diagnosis.get("root_cause", "未知") if isinstance(diagnosis, dict) else "未知"
            failure_cat = diagnosis.get("failure_category", "unknown") if isinstance(diagnosis, dict) else "unknown"
            preferred_tool = diagnosis.get("preferred_tool", current_tool) if isinstance(diagnosis, dict) else current_tool
            tool_switch_reason = diagnosis.get("tool_switch_reason", "") if isinstance(diagnosis, dict) else ""

            alog.log(
                "self_heal_diagnosis_done",
                attempt=attempt,
                root_cause=root_cause[:200],
                failure_category=failure_cat,
                preferred_tool=preferred_tool,
                tool_switch_reason=tool_switch_reason[:100] if tool_switch_reason else "",
                has_corrected_output=corrected is not None,
            )

            if corrected is None:
                alog.log(
                    "self_heal_no_correction",
                    attempt=attempt,
                    note="Agent 诊断报告中无 corrected_output 字段",
                )
                # 如果 Agent 建议换工具且还没换过，尝试切换
                if preferred_tool and preferred_tool != current_tool:
                    alog.log(
                        "self_heal_agent_suggested_switch",
                        from_agent=current_tool,
                        to_agent=preferred_tool,
                        reason=tool_switch_reason[:150] if tool_switch_reason else "Agent 建议切换",
                    )
                    current_tool = preferred_tool
                continue

            # 验证修正结果
            if isinstance(corrected, list):
                alog.log(
                    "self_heal_correction_wrong_type",
                    attempt=attempt,
                    actual_type="list",
                    expected_type="dict",
                )
                continue

            if isinstance(corrected, dict):
                fr_count = len(corrected.get("functional_requirements", []))
                tp_count = len(corrected.get("test_points", []))
                alog.log(
                    "self_heal_correction_applied",
                    attempt=attempt,
                    fr_count=fr_count,
                    nfr_count=len(corrected.get("non_functional_requirements", [])),
                    tp_count=tp_count,
                    risk_count=len(corrected.get("risks", [])),
                )

                # 内容质量检查
                if (
                    failure.category != FailureCategory.OUTPUT_QUALITY
                    and fr_count < OUTPUT_QUALITY_MIN_FR
                    and tp_count < OUTPUT_QUALITY_MIN_TP
                ):
                    alog.log(
                        "self_heal_quality_insufficient",
                        attempt=attempt,
                        fr_count=fr_count,
                        tp_count=tp_count,
                        note="修正后内容质量仍不满足最低要求，继续尝试",
                    )
                    continue

                alog.log(
                    "self_heal_complete",
                    total_attempts=attempt,
                    outcome="success",
                    resolve_method="self_diagnosis",
                )
                return HealingResult(
                    success=True,
                    output=corrected,
                    raw_output=diag_result.raw_output,
                    total_attempts=attempt,
                    resolve_method="self_diagnosis",
                    diagnosis_report=diagnosis if isinstance(diagnosis, dict) else {},
                )

            # 修正结果类型异常
            alog.log(
                "self_heal_correction_type_unexpected",
                attempt=attempt,
                actual_type=type(corrected).__name__,
            )

            # Agent 建议换工具
            if preferred_tool and preferred_tool != current_tool:
                alog.log(
                    "self_heal_agent_suggested_switch",
                    from_agent=current_tool,
                    to_agent=preferred_tool,
                    reason=tool_switch_reason[:150] if tool_switch_reason else "Agent 建议切换",
                )
                current_tool = preferred_tool

        # ── 全部尝试耗尽 ──
        alog.log(
            "self_heal_exhausted",
            total_attempts=MAX_SELF_HEAL_ATTEMPTS,
            attempts_detail=[f"diagnosis_attempt_{i}" for i in range(1, MAX_SELF_HEAL_ATTEMPTS + 1)],
            decision="mark_failed",
            failure_category=failure.category.value,
        )

        await self.feishu.notify_failed(
            analysis_id=context.analysis_id,
            stage_name=failure.step_name,
            error_summary=f"输出自诊断失败（{MAX_SELF_HEAL_ATTEMPTS} 次尝试均未产出合法 JSON）",
        )

        return HealingResult(
            success=False,
            total_attempts=MAX_SELF_HEAL_ATTEMPTS,
            resolve_method="exhausted",
            final_error=f"输出自诊断耗尽：{MAX_SELF_HEAL_ATTEMPTS} 次尝试均失败",
        )

    # ================================================================
    # 诊断 Prompt 构建
    # ================================================================

    def _build_diagnosis_prompt(
        self,
        failure: FailureInfo,
        context: HealingContext,
        attempt: int,
    ) -> str:
        """构建 Agent 自诊断提示词。

        包含：错误信息 + 原始输出 + 原始任务上下文。
        第 2/3 次尝试会附加更严厉的指令。
        """
        # 截断原始输出（保留头尾以定位问题）
        raw_output = failure.raw_output
        if len(raw_output) > 6000:
            raw_output = raw_output[:3000] + "\n\n...（输出过长，中间已截断）...\n\n" + raw_output[-3000:]

        escalation = ""
        if attempt >= 3:
            escalation = (
                "\n\n## ⚠️ 最后机会\n"
                "这是第 3 次也是最后一次修正机会。"
                "如果这次仍无法输出合法 JSON，此分析任务将标记为失败。"
                "请务必在 `corrected_output` 中输出完整的、可被 JSON.parse() 解析的对象。"
            )

        prompt = f"""## 自诊断任务

你之前执行了一次分析任务，但输出未能通过系统校验。你的任务是诊断失败原因，并输出修正后的正确结果。

### 失败信息

- 失败类型：**{failure.category.value}**
- 错误描述：{failure.error_message[:300]}
- 你使用的工具：{failure.agent_tool}

### 你上一次的原始输出

```
{raw_output}
```

### 原始任务上下文

<原始需求文档摘要>
{context.doc_summary[:1500]}
</原始需求文档摘要>

<原始分析要求>
{context.skill_body[:2000]}
</原始分析要求>

### 你的任务

请完成以下两项工作：

**1. 诊断根因**：分析上一次输出为什么没能通过校验。具体指出：
- 哪个位置出了问题（如第几行、哪个字段）
- 根本原因是什么（未转义字符 / Schema 不匹配 / 输出被截断 / API 超时等）
- 如果需要更换 Agent 工具重新执行（如 API 超时建议用另一个 CLI），在 preferred_tool 中指定

**2. 输出修正结果**：按照原始 JSON Schema 输出一份完整、正确的分析结果。
- 必须是一个 JSON 对象 `{{}}`，不是数组 `[]`
- 包含 `meta`、`functional_requirements`、`non_functional_requirements`、`test_points`、`risks`、`analysis_notes` 全部字段

{escalation}

### 输出格式

只输出此 JSON 对象（第一个字符必须是 `{{`）：
{{
  "diagnosis": {{
    "root_cause": "根因分析（中文，具体描述哪里出了问题）",
    "failure_category": "json_escape | schema_violation | truncated | quality_insufficient | api_timeout | other",
    "preferred_tool": "claude" | "codex",
    "tool_switch_reason": "如果建议换工具，说明原因；不换则填 null"
  }},
  "corrected_output": {{
    "meta": {{}},
    "functional_requirements": [...],
    "non_functional_requirements": [...],
    "test_points": [...],
    "risks": [...],
    "analysis_notes": {{}}
  }}
}}
"""
        return prompt


# ============================================================
# 辅助函数 — 快速分类失败
# ============================================================


def classify_failure(
    cli_result: CLICallResult | None = None,
    json_result: JSONExtractResult | None = None,
    agent_tool: str = "claude",
    step_name: str = "",
    prompt: str = "",
    raw_output: str = "",
) -> FailureInfo:
    """根据 CLI 调用结果快速分类失败类别。

    优先级：基础设施故障 > 输出解析故障 > 类型故障 > 未知
    """
    # 基础设施故障：CLI 返回了错误
    if cli_result and not cli_result.success:
        error = cli_result.error
        if "超时" in error or "timeout" in error.lower():
            return FailureInfo(
                category=FailureCategory.INFRA_TIMEOUT,
                step_name=step_name,
                agent_tool=agent_tool,
                error_message=error,
                exit_code=cli_result.exit_code,
                raw_output=raw_output or cli_result.raw_output,
                prompt=prompt,
            )
        elif "未找到命令" in error or "not found" in error.lower():
            return FailureInfo(
                category=FailureCategory.INFRA_CLI_NOT_FOUND,
                step_name=step_name,
                agent_tool=agent_tool,
                error_message=error,
                exit_code=cli_result.exit_code,
                raw_output=raw_output or cli_result.raw_output,
                prompt=prompt,
            )
        else:
            return FailureInfo(
                category=FailureCategory.INFRA_CLI_ERROR,
                step_name=step_name,
                agent_tool=agent_tool,
                error_message=error,
                exit_code=cli_result.exit_code,
                raw_output=raw_output or cli_result.raw_output,
                prompt=prompt,
            )

    # 输出故障：JSON 解析失败
    if json_result and not json_result.success:
        return FailureInfo(
            category=FailureCategory.OUTPUT_PARSE,
            step_name=step_name,
            agent_tool=agent_tool,
            error_message=json_result.error,
            raw_output=raw_output,
            prompt=prompt,
        )

    return FailureInfo(
        category=FailureCategory.UNKNOWN,
        step_name=step_name,
        agent_tool=agent_tool,
        error_message="未知失败原因",
        raw_output=raw_output,
        prompt=prompt,
    )
