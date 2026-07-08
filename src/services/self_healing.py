"""自愈编排器 — 处理智能体执行失败时的自动诊断、修正和重试。

架构：
  - 基础设施故障 → runtime 退避重试 × N → 强制 fallback 一次
  - 输出格式/质量故障 → runtime 调用 `utility.diagnoser` 自诊断 × M → 输出修正结果

调用哪一个具体 backend（Claude Code / Codex / Cursor 等）完全由
`agent_runtime` 按 role 配置决定，本编排器不再包含任何 CLI 品牌硬编码分支。

Usage:
    from src.services.self_healing import SelfHealingOrchestrator, FailureInfo, HealingContext

    healer = SelfHealingOrchestrator(agent_runtime, feishu)
    result = await healer.handle(failure_info, context, alog)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.agent_runtime import AgentRunResult, AgentTask
from src.agent_runtime import agent_runtime as _default_runtime
from src.agent_runtime.cli_shared import (
    CLICallResult,
    JSONExtractResult,
    dynamic_timeout,
    estimate_tokens,
    extract_json,
    recover_json_from_workdir,
)
from src.services.feishu_notifier import FeishuNotifier
from src.services.testpoint_coverage import validate_testpoint_coverage
from src.utils.analysis_logger import AnalysisLogger
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


MAX_SELF_HEAL_ATTEMPTS = 1
INFRA_RETRY_DELAYS = [0]
DIAGNOSIS_TIMEOUT = 180
OUTPUT_QUALITY_MIN_FR = 1
OUTPUT_QUALITY_MIN_TP = 1

DEFAULT_DIAGNOSER_ROLE = "utility.diagnoser"


class FailureCategory(str, Enum):
    """失败类别。"""

    INFRA_TIMEOUT = "infra_timeout"
    INFRA_CLI_ERROR = "infra_cli_error"
    INFRA_CLI_NOT_FOUND = "infra_cli_not_found"
    OUTPUT_PARSE = "output_parse"
    OUTPUT_TYPE = "output_type"
    OUTPUT_QUALITY = "output_quality"
    UNKNOWN = "unknown"


@dataclass
class FailureInfo:
    """描述一次失败的完整信息。

    `agent_tool` 字段保留（向后兼容），语义已改为"当前失败的 backend name"
    （如 `claude_code` / `codex` / `cursor`），业务侧传入 result.backend 即可。
    """

    category: FailureCategory
    step_name: str = ""
    agent_tool: str = ""
    error_message: str = ""
    exit_code: int = -1
    raw_output: str = ""
    prompt: str = ""


@dataclass
class HealingContext:
    """自愈所需的上下文。

    `role` 决定重试与自诊断使用哪个 role（两段式命名）；未指定时默认
    `requirement.analyzer`，保持向后兼容。`diagnoser_role` 默认使用通用
    `utility.diagnoser` 角色。
    """

    analysis_id: str = ""
    doc_md: str = ""
    doc_summary: str = ""
    skill_body: str = ""
    knowledge_context: str = ""
    platform_type: str = ""
    custom_prompt: str = ""
    review_skill_body: str = ""
    original_analysis_json: Optional[dict] = None
    role: str = "requirement.analyzer"
    diagnoser_role: str = DEFAULT_DIAGNOSER_ROLE
    workdir: str = ""


@dataclass
class HealingResult:
    """自愈结果。"""

    success: bool
    output: Any = None
    raw_output: str = ""
    total_attempts: int = 0
    resolve_method: str = ""
    diagnosis_report: Optional[dict] = None
    final_error: str = ""


class SelfHealingOrchestrator:
    """编排失败恢复流程。

    构造时注入 `AgentRuntime`（或代理），不再直接持有具体 CLI 客户端。
    保留 `feishu` 用于失败/切换通知。
    """

    def __init__(self, runtime: Any = None, feishu: Optional[FeishuNotifier] = None):
        self.runtime = runtime if runtime is not None else _default_runtime
        self.feishu = feishu or FeishuNotifier()

    # ---- 公共入口 ----

    async def handle(
        self,
        failure: FailureInfo,
        context: HealingContext,
        alog: AnalysisLogger,
    ) -> HealingResult:
        alog.log(
            "self_heal_start",
            failure_category=failure.category.value,
            step_name=failure.step_name,
            role=context.role,
            backend=failure.agent_tool,
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
        return await self._handle_output_diagnosis(failure, context, alog)

    # ================================================================
    # 基础设施故障 — 退避重试 → 强制 fallback
    # ================================================================

    async def _handle_infrastructure(
        self,
        failure: FailureInfo,
        context: HealingContext,
        alog: AnalysisLogger,
    ) -> HealingResult:
        """基础设施故障恢复。

        AgentRuntime 已经天然承担了 primary → fallbacks 顺序尝试的语义，
        healer 只需在整条 chain 层面做**退避重试**：
        - 退避一轮 → 若 chain 内任一 backend 成功即返回；
        - 若 chain 内所有 backend 都失败，看到 chain 结果是"backend_switch"
          （因为 fallback 已经被走过），resolve_method 反映实际使用的 backend。
        """
        role = context.role
        original_backend = failure.agent_tool
        last_result: Optional[AgentRunResult] = None

        for idx, delay in enumerate(INFRA_RETRY_DELAYS):
            attempt = idx + 1

            await self.feishu.notify_text(
                f"🔄 自动重试 | 任务={context.analysis_id} | "
                f"role={role} | 步骤={failure.step_name} | "
                f"第{attempt}/{len(INFRA_RETRY_DELAYS)}次 | "
                f"原因={failure.error_message[:60]}"
            )

            alog.log(
                "self_heal_infra_retry",
                attempt=attempt,
                max_attempts=len(INFRA_RETRY_DELAYS),
                delay_s=delay,
                role=role,
                original_backend=original_backend,
                retry_prompt_len=len(failure.prompt),
            )

            if delay > 0:
                await asyncio.sleep(delay)

            timeout = dynamic_timeout(estimate_tokens(failure.prompt))
            result = await self.runtime.run(AgentTask(
                role=role,
                prompt=failure.prompt,
                workdir=context.workdir or None,
                timeout=timeout,
                stage_name="self_healing_infra_retry",
                task_id=context.analysis_id,
            ))
            last_result = result

            alog.log(
                "self_heal_infra_retry_done",
                attempt=attempt,
                role=role,
                backend=result.backend,
                fallback_from=result.fallback_from or "",
                success=result.success,
                exit_code=result.exit_code,
                output_len=len(result.raw_output) if result.success else 0,
                error=result.error[:150] if not result.success else "",
                timeout_used=timeout,
            )

            if result.success:
                # runtime 内部可能已经走了 fallback → resolve_method 反映实况
                resolve_method = (
                    "backend_switch"
                    if result.backend and result.backend != original_backend
                    else "infra_retry"
                )
                alog.log(
                    "self_heal_complete",
                    total_attempts=attempt,
                    outcome="success",
                    resolve_method=resolve_method,
                    backend=result.backend,
                    original_backend=original_backend,
                )
                return HealingResult(
                    success=True,
                    raw_output=result.raw_output,
                    total_attempts=attempt,
                    resolve_method=resolve_method,
                )

        # 全部退避重试尽头，chain 内所有 backend 都失败
        error_detail = (last_result.error if last_result else "unknown")[:200]
        alog.log(
            "self_heal_exhausted",
            total_attempts=len(INFRA_RETRY_DELAYS),
            role=role,
            errors=error_detail,
            decision="mark_failed",
        )

        await self.feishu.notify_failed(
            analysis_id=context.analysis_id,
            stage_name=failure.step_name,
            error_summary=(
                f"自愈失败：role={role} 全 chain 退避 {len(INFRA_RETRY_DELAYS)} 次均失败"
            ),
        )

        return HealingResult(
            success=False,
            total_attempts=len(INFRA_RETRY_DELAYS),
            resolve_method="exhausted",
            final_error=(
                f"基础设施自愈耗尽：role={role} 全 chain × {len(INFRA_RETRY_DELAYS)} "
                f"均失败 ({error_detail})"
            ),
        )

    # ================================================================
    # 输出故障 — 通过 utility.diagnoser 角色自诊断
    # ================================================================

    async def _handle_output_diagnosis(
        self,
        failure: FailureInfo,
        context: HealingContext,
        alog: AnalysisLogger,
    ) -> HealingResult:
        for attempt in range(1, MAX_SELF_HEAL_ATTEMPTS + 1):
            alog.log(
                "self_heal_diagnosis_attempt",
                attempt=attempt,
                max_attempts=MAX_SELF_HEAL_ATTEMPTS,
                failure_category=failure.category.value,
                role=context.diagnoser_role,
                origin_role=context.role,
                origin_backend=failure.agent_tool,
            )

            diagnosis_prompt = self._build_diagnosis_prompt(failure, context, attempt)
            alog.save_snapshot(f"self_heal_diagnosis_prompt_{attempt}.txt", diagnosis_prompt)

            alog.log(
                "self_heal_diagnosis_start",
                attempt=attempt,
                role=context.diagnoser_role,
                diagnosis_prompt_len=len(diagnosis_prompt),
            )

            diag_result = await self.runtime.run(AgentTask(
                role=context.diagnoser_role,
                prompt=diagnosis_prompt,
                workdir=context.workdir or None,
                timeout=DIAGNOSIS_TIMEOUT,
                stage_name="self_healing_diagnosis",
                task_id=context.analysis_id,
                metadata={"origin_role": context.role, "origin_backend": failure.agent_tool},
            ))

            if not diag_result.success:
                alog.log(
                    "self_heal_diagnosis_failed",
                    attempt=attempt,
                    role=context.diagnoser_role,
                    backend=diag_result.backend,
                    error=diag_result.error[:200],
                    exit_code=diag_result.exit_code,
                )
                continue

            alog.save_snapshot(f"self_heal_diagnosis_raw_{attempt}.txt", diag_result.raw_output)

            diag_json = extract_json(diag_result.raw_output)
            if not diag_json.success or not isinstance(diag_json.data, dict):
                # 诊断 Agent 也可能「写文件代答」；尝试从 workdir 恢复
                recovered = recover_json_from_workdir(
                    context.workdir,
                    raw_output=diag_result.raw_output,
                    preferred_names=[
                        "self_heal_corrected_output_compact.json",
                        "self_heal_corrected_output.json",
                        "corrected_output.json",
                        "test_points_output.json",
                    ],
                )
                if recovered.success and isinstance(recovered.data, dict):
                    alog.log(
                        "self_heal_recovered_from_file",
                        attempt=attempt,
                        extract_method=recovered.extract_method,
                    )
                    # 兼容：直接是 corrected payload，或带 diagnosis 包装
                    if "corrected_output" in recovered.data or "diagnosis" in recovered.data:
                        diag_json = recovered
                    else:
                        diag_json = JSONExtractResult(
                            success=True,
                            data={
                                "diagnosis": {
                                    "root_cause": "stdout 无 JSON，已从落盘文件恢复",
                                    "failure_category": "other",
                                },
                                "corrected_output": recovered.data,
                            },
                            extract_method=recovered.extract_method,
                        )
                else:
                    alog.log(
                        "self_heal_diagnosis_parse_failed",
                        attempt=attempt,
                        role=context.diagnoser_role,
                        backend=diag_result.backend,
                        error=diag_json.error[:200],
                        recover_error=recovered.error[:200],
                    )
                    continue

            if not diag_json.success or not isinstance(diag_json.data, dict):
                alog.log(
                    "self_heal_diagnosis_parse_failed",
                    attempt=attempt,
                    role=context.diagnoser_role,
                    backend=diag_result.backend,
                    error=(diag_json.error or "empty")[:200],
                )
                continue

            diag_data = diag_json.data
            diagnosis = diag_data.get("diagnosis", {})
            corrected = diag_data.get("corrected_output")

            # 若包装结构缺失但整包就是合法分析结果/测试点，直接当作 corrected
            if corrected is None and isinstance(diag_data, dict):
                if "test_points" in diag_data or "functional_requirements" in diag_data:
                    corrected = diag_data
                    diagnosis = diagnosis or {
                        "root_cause": "诊断输出缺少包装字段，已直接采用根对象",
                        "failure_category": "other",
                    }

            root_cause = diagnosis.get("root_cause", "未知") if isinstance(diagnosis, dict) else "未知"
            failure_cat = diagnosis.get("failure_category", "unknown") if isinstance(diagnosis, dict) else "unknown"

            alog.log(
                "self_heal_diagnosis_done",
                attempt=attempt,
                role=context.diagnoser_role,
                backend=diag_result.backend,
                root_cause=root_cause[:200],
                failure_category=failure_cat,
                has_corrected_output=corrected is not None,
            )

            if corrected is None:
                # 再兜底读盘一次（诊断文本声称写入但 JSON 包装失败）
                recovered = recover_json_from_workdir(
                    context.workdir,
                    raw_output=diag_result.raw_output,
                    preferred_names=[
                        "self_heal_corrected_output_compact.json",
                        "self_heal_corrected_output.json",
                        "test_points_output.json",
                    ],
                )
                if recovered.success and isinstance(recovered.data, dict):
                    corrected = recovered.data.get("corrected_output", recovered.data)
                    alog.log(
                        "self_heal_corrected_recovered_from_file",
                        attempt=attempt,
                        extract_method=recovered.extract_method,
                    )
                else:
                    alog.log(
                        "self_heal_no_correction",
                        attempt=attempt,
                        note="诊断报告中无 corrected_output 字段",
                    )
                    continue

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

                # 测试点阶段：仅有 test_points 也视为有效修正
                is_tp_only = (
                    context.role == "requirement.testpoint_designer"
                    or failure.step_name.startswith("testpoint")
                )
                quality_ok = True
                if (
                    failure.category != FailureCategory.OUTPUT_QUALITY
                    and not is_tp_only
                    and fr_count < OUTPUT_QUALITY_MIN_FR
                    and tp_count < OUTPUT_QUALITY_MIN_TP
                ):
                    quality_ok = False
                if is_tp_only and tp_count < 1:
                    quality_ok = False
                elif is_tp_only and tp_count >= 1:
                    ctx_json = context.original_analysis_json or {}
                    if ctx_json.get("functional_requirements") or ctx_json.get(
                        "non_functional_requirements"
                    ):
                        cov = validate_testpoint_coverage(
                            {
                                **ctx_json,
                                "test_points": corrected.get("test_points", []),
                            },
                            require_full=bool(
                                failure.step_name.startswith("testpoint_coverage")
                                or failure.category == FailureCategory.OUTPUT_QUALITY
                            ),
                        )
                        if not cov.ok:
                            quality_ok = False
                            alog.log(
                                "self_heal_tp_coverage_insufficient",
                                attempt=attempt,
                                summary=cov.summary(),
                                errors=cov.errors[:5],
                            )

                if not quality_ok:
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
                    backend=diag_result.backend,
                )
                return HealingResult(
                    success=True,
                    output=corrected,
                    raw_output=diag_result.raw_output,
                    total_attempts=attempt,
                    resolve_method="self_diagnosis",
                    diagnosis_report=diagnosis if isinstance(diagnosis, dict) else {},
                )

            alog.log(
                "self_heal_correction_type_unexpected",
                attempt=attempt,
                actual_type=type(corrected).__name__,
            )

        alog.log(
            "self_heal_exhausted",
            total_attempts=MAX_SELF_HEAL_ATTEMPTS,
            role=context.diagnoser_role,
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
        raw_output = failure.raw_output
        if len(raw_output) > 6000:
            raw_output = (
                raw_output[:3000]
                + "\n\n...（输出过长，中间已截断）...\n\n"
                + raw_output[-3000:]
            )

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
- 原调用 role：{context.role}
- 原执行 backend：{failure.agent_tool}

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

**2. 输出修正结果**：按照原始 JSON Schema 输出一份完整、正确的分析结果。
- 必须是一个 JSON 对象 `{{}}`，不是数组 `[]`
- 包含 `meta`、`functional_requirements`、`non_functional_requirements`、`test_points`、`risks`、`analysis_notes` 全部字段

{escalation}

### 输出格式

**禁止写文件代答**：必须把完整 JSON 打在回复里（stdout），不要写「已写入 xxx.json」。平台只解析回复文本。

只输出此 JSON 对象（第一个字符必须是 `{{`）：
{{
  "diagnosis": {{
    "root_cause": "根因分析（中文，具体描述哪里出了问题）",
    "failure_category": "json_escape | schema_violation | truncated | quality_insufficient | api_timeout | other"
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

若原任务是测试点设计（origin role 含 testpoint），则 `corrected_output` 可为：
{{ "test_points": [ ... ] }}
"""
        return prompt


# ============================================================
# 辅助函数 — 快速分类失败
# ============================================================


def classify_failure(
    cli_result: Optional[Any] = None,
    json_result: Optional[JSONExtractResult] = None,
    agent_tool: str = "",
    step_name: str = "",
    prompt: str = "",
    raw_output: str = "",
) -> FailureInfo:
    """根据智能体调用结果（`AgentRunResult` 或旧 `CLICallResult`）快速分类失败。

    `cli_result` 接受任何具备 `success` / `error` / `exit_code` / `raw_output`
    字段的对象（鸭子类型），使 self_healing 与 agent_runtime 解耦。
    """

    if cli_result is not None and not getattr(cli_result, "success", True):
        error = getattr(cli_result, "error", "") or ""
        exit_code = getattr(cli_result, "exit_code", -1)
        result_raw = getattr(cli_result, "raw_output", "") or ""
        if "超时" in error or "timeout" in error.lower():
            return FailureInfo(
                category=FailureCategory.INFRA_TIMEOUT,
                step_name=step_name,
                agent_tool=agent_tool,
                error_message=error,
                exit_code=exit_code,
                raw_output=raw_output or result_raw,
                prompt=prompt,
            )
        if "未找到命令" in error or "not found" in error.lower():
            return FailureInfo(
                category=FailureCategory.INFRA_CLI_NOT_FOUND,
                step_name=step_name,
                agent_tool=agent_tool,
                error_message=error,
                exit_code=exit_code,
                raw_output=raw_output or result_raw,
                prompt=prompt,
            )
        return FailureInfo(
            category=FailureCategory.INFRA_CLI_ERROR,
            step_name=step_name,
            agent_tool=agent_tool,
            error_message=error,
            exit_code=exit_code,
            raw_output=raw_output or result_raw,
            prompt=prompt,
        )

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
        error_message="未知失败类型",
        raw_output=raw_output,
        prompt=prompt,
    )


__all__ = [
    "SelfHealingOrchestrator",
    "FailureInfo",
    "FailureCategory",
    "HealingContext",
    "HealingResult",
    "classify_failure",
    "CLICallResult",
    "JSONExtractResult",
]
