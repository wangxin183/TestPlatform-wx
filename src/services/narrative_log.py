"""将结构化任务/自愈事件转成中文自然语言说明，供落盘与前端展示。"""

from __future__ import annotations

from typing import Any


def _s(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def narrate(event: str, **payload: Any) -> str:
    """根据 event/step 名与字段生成一句可读中文。"""
    name = str(event or payload.get("step") or "").strip()
    handlers = {
        # —— 执行运行时 ——
        "task_loaded": lambda: (
            f"已加载执行任务 {_s(payload.get('run_id'))}，"
            f"共 {payload.get('case_count', '?')} 条用例"
        ),
        "precheck_start": lambda: "开始环境预检，确认 Appium/设备/被测 App 可用",
        "precheck_done": lambda: (
            "环境预检完成"
            + ("，存在告警" if payload.get("warnings") else "，可以开始执行")
        ),
        "cases_accepted": lambda: f"已接受 {payload.get('count', '?')} 条可执行用例",
        "cases_rejected": lambda: (
            f"有 {payload.get('count', '?')} 条用例未通过校验被拒绝"
        ),
        "compile_start": lambda: (
            f"开始编译用例「{_s(payload.get('title') or payload.get('case_id'), 40)}」"
            if payload.get("case_id") or payload.get("title")
            else f"开始编译用例 DSL（共 {payload.get('case_count', '?')} 条）"
        ),
        "compile_done": lambda: (
            f"用例「{_s(payload.get('title') or payload.get('case_id'), 40)}」编译完成"
            f"（状态 {_s(payload.get('compile_status') or payload.get('status') or 'ok')}）"
            if payload.get("case_id") or payload.get("title")
            else f"用例编译完成，成功 {payload.get('ok', '?')} 条"
        ),
        "compile_failed": lambda: (
            f"用例 {_s(payload.get('case_id'))} 编译失败："
            f"{_s(payload.get('error'), 160)}"
        ),
        "pytest_start": lambda: "启动 pytest，开始在真机/模拟器上执行用例",
        "pytest_done": lambda: f"pytest 执行结束，退出码 {payload.get('returncode', '?')}",
        "allure_generated": lambda: "已生成 Allure 测试报告",
        "allure_skip": lambda: f"跳过 Allure 报告：{_s(payload.get('reason'), 120)}",
        "allure_failed": lambda: f"Allure 报告生成失败：{_s(payload.get('stderr'), 120)}",
        "allure_error": lambda: f"Allure 报告异常：{_s(payload.get('error'), 120)}",
        "run_completed": lambda: (
            f"本轮执行完成：通过 {payload.get('passed', '?')}，"
            f"失败 {payload.get('failed', '?')}，"
            f"中断 {payload.get('broken', '?')}"
        ),
        "run_aborted": lambda: f"执行中止：{_s(payload.get('reason'), 160)}",
        "export_start": lambda: f"导出执行任务，准备跑 {payload.get('case_count', '?')} 条用例",
        "export_done": lambda: "任务文件已导出，准备拉起执行子进程",
        "subprocess_start": lambda: f"执行子进程已启动（pid={payload.get('pid', '?')}）",
        "subprocess_done": lambda: f"执行子进程结束（code={payload.get('returncode', '?')}）",
        "runner_stdout": lambda: f"运行输出：{_s(payload.get('line'), 200)}",
        "db_import_done": lambda: "执行结果已写回平台数据库",
        "db_import_failed": lambda: f"结果入库失败：{_s(payload.get('error'), 160)}",
        "run_failed": lambda: f"执行任务失败：{_s(payload.get('error'), 200)}",
        "module_setup_start": lambda: (
            f"开始进入模块「{_s(payload.get('module'))}」"
        ),
        "module_setup_failed": lambda: (
            f"模块「{_s(payload.get('module'))}」入口失败："
            f"{_s(payload.get('reason'), 160)}"
        ),
        "heal_seed_failure": lambda: (
            f"检测到阻塞（{_s(payload.get('scope'))}/"
            f"{_s(payload.get('category'))}）：{_s(payload.get('message'), 160)}"
        ),
        "heal_first_pass": lambda: f"本阶段首次尝试即成功（范围：{_s(payload.get('scope'))}）",
        "heal_attempt_failed": lambda: (
            f"第 {payload.get('attempt', '?')} 次尝试失败"
            f"（{_s(payload.get('category'))}）：{_s(payload.get('message'), 160)}"
        ),
        "heal_diagnose_start": lambda: (
            f"开始自愈诊断（第 {payload.get('heal_index', '?')} 轮，"
            f"类别 {_s(payload.get('category'))}）"
        ),
        "heal_plan": lambda: _narrate_heal_plan(payload.get("plan") or {}),
        "heal_apply_failed": lambda: f"自愈动作执行失败：{_s(payload.get('error'), 160)}",
        "heal_success": lambda: (
            f"自愈成功：通过动作「{_s(payload.get('action'))}」恢复后重试通过"
            f"（共用尝试 {payload.get('attempts', '?')} 次）"
        ),
        "heal_retry_failed": lambda: (
            f"自愈后重试仍失败（{_s(payload.get('category'))}）："
            f"{_s(payload.get('message'), 160)}"
        ),
        "heal_exhausted": lambda: (
            f"自愈次数用尽（范围 {_s(payload.get('scope'))}）："
            f"{_s(payload.get('final_error'), 160)}"
        ),
        "heal_product_defect": lambda: (
            f"判定为产品缺陷，停止自愈：{_s(payload.get('message'), 160)}"
        ),
        "tool_call": lambda: (
            f"Agent 调用工具「{_s(payload.get('tool'))}」"
            f"（步骤 {payload.get('step', '?')}，第 {payload.get('call_index', '?')} 次）"
        ),
        "tool_failed": lambda: (
            f"工具「{_s(payload.get('tool'))}」失败：{_s(payload.get('error'), 160)}"
        ),
        "step_verified": lambda: (
            f"步骤 {payload.get('step', '?')} 已满足合同验收"
            f"（末次工具 {_s(payload.get('tool'))}）"
        ),
        "step_not_verified": lambda: (
            f"步骤 {payload.get('step', '?')} 尚未满足验收："
            f"{_s(payload.get('reasons'), 160)}"
        ),
        "page_state_mismatch": lambda: (
            f"页面状态偏离预期，准备恢复：{_s(payload.get('reason'), 160)}"
        ),
        "page_recovery_back": lambda: (
            f"页面恢复：回退 {payload.get('backs', '?')} 次"
            f"（via={_s(payload.get('via'))}）"
        ),
        "module_navigation_succeeded": lambda: (
            f"已智能导航进入模块「{_s(payload.get('module'))}」"
            f"（Agent 调用 {payload.get('agent_calls', 0)} 次）"
        ),
        "module_navigation_failed": lambda: (
            f"模块「{_s(payload.get('module'))}」智能入口失败："
            f"{_s(payload.get('reason'), 160)}"
        ),
        "skip_setup": lambda: f"同模块会话复用，跳过入口 Setup（{_s(payload.get('module'))}）",
        # —— TCG ——
        "task_created": lambda: (
            f"已创建用例生成任务，来源分析 {_s(payload.get('analysis_id'))}"
        ),
        "source_loaded": lambda: (
            f"已加载 UI 测试点 {payload.get('tp_count', payload.get('selected', '?'))} 个"
        ),
        "batch_plan": lambda: (
            f"按 token 预算分成 {payload.get('batch_count', '?')} 批生成"
            f"（并发上限 {payload.get('max_concurrency', '?')}）"
        ),
        "agent_start": lambda: f"开始调用用例生成 Agent（批次 {payload.get('batch', '?')}）",
        "agent_done": lambda: (
            f"批次 {payload.get('batch', '?')} 生成完成，产出 "
            f"{payload.get('case_count', '?')} 条用例"
        ),
        "agent_failed": lambda: (
            f"批次 {payload.get('batch', '?')} 生成失败：{_s(payload.get('error'), 160)}"
        ),
        "coverage_check": lambda: (
            f"覆盖校验：{_s(payload.get('summary'))}"
            + ("（自愈后）" if payload.get("after_heal") else "")
        ),
        "self_heal_start": lambda: (
            f"启动自愈：{_s(payload.get('step_name') or payload.get('failure_category'))}"
            + (
                f"，缺失 {payload.get('missing_count')} 个测试点"
                if payload.get("missing_count")
                else ""
            )
            + (
                f"，待修 {payload.get('failed_compile_count')} 条编译失败用例"
                if payload.get("failed_compile_count")
                else ""
            )
        ),
        "self_heal_batch_plan": lambda: (
            f"自愈补批计划：{payload.get('batch_count', '?')} 批"
        ),
        "self_heal_complete": lambda: "自愈完成，阶段目标已满足",
        "self_heal_exhausted": lambda: (
            f"自愈未完全成功：{_s(payload.get('missing_tp_ids') or payload.get('outcome'))}"
        ),
        "exec_heal_deterministic": lambda: (
            f"对「{_s(payload.get('title'), 40)}」做了确定性 expected 加固"
            f"（{payload.get('changed_steps', 0)} 步），编译状态 "
            f"{_s(payload.get('compile_status'))}"
        ),
        "exec_heal_agent_patch": lambda: (
            f"对「{_s(payload.get('title'), 40)}」调用 Agent 定点改写 expected，"
            f"结果 compile={_s(payload.get('compile_status'))}"
        ),
        "exec_heal_regen": lambda: (
            f"对测试点 {_s(payload.get('test_point_id'))} 整案重生兜底，"
            f"得到 {payload.get('case_count', 0)} 条候选"
        ),
        "cases_persisted": lambda: f"已写入用例库 {payload.get('inserted', '?')} 条",
        "pipeline_done": lambda: "用例生成完成，进入待人工评审",
        "pipeline_error": lambda: f"用例生成流水线错误：{_s(payload.get('error'), 200)}",
        "batch_partial_errors": lambda: (
            f"部分批次失败 {payload.get('error_count', '?')} 个，继续合并成功结果"
        ),
    }
    fn = handlers.get(name)
    if fn:
        try:
            return fn()
        except Exception:  # noqa: BLE001
            pass
    # 通用兜底
    bits = [f"事件「{name}」"]
    for key in (
        "message",
        "error",
        "summary",
        "module",
        "case_id",
        "title",
        "action",
        "tool",
    ):
        if payload.get(key) not in (None, "", [], {}):
            bits.append(f"{key}={_s(payload.get(key), 80)}")
            break
    return "；".join(bits)


def _narrate_heal_plan(plan: dict[str, Any]) -> str:
    action = _s(plan.get("action") or "unknown")
    rationale = _s(plan.get("rationale"), 120)
    category = _s(plan.get("category"))
    mapping = {
        "recover_page": "回退/恢复页面",
        "dismiss_and_retry": "关闭遮挡后重试",
        "reenter_module": "重新进入模块",
        "retry_agent_step": "用 Agent 重跑当前步骤",
        "retry_dsl": "按 DSL 再试一次",
        "launch_app": "重新拉起 App",
        "give_up": "放弃自愈",
        "give_up_defect": "记为缺陷并停止自愈",
        "repair_expected": "定点改写期望结果",
        "regen_case": "整案重新生成",
    }
    label = mapping.get(action, action)
    msg = f"自愈方案：{label}"
    if category:
        msg += f"（类别 {category}）"
    if rationale:
        msg += f" — {rationale}"
    return msg


def enrich_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """若无中文 message，则补上自然语言说明。"""
    out = dict(entry)
    event = str(out.get("event") or out.get("step") or "")
    existing = str(out.get("message") or "").strip()
    if existing and any("\u4e00" <= ch <= "\u9fff" for ch in existing):
        return out
    payload = {
        k: v
        for k, v in out.items()
        if k not in {"ts", "timestamp", "seq", "event", "step", "message", "source"}
    }
    out["message"] = narrate(event, **payload)
    return out
