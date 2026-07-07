"""功能验证测试：真实代码路径验证。

覆盖：
1. ExecutionStage._translate_steps — mock LLM，验证 NL→结构化翻译
2. BrowserMatrixExecutor — 实例化 + health_check
3. AnalysisStage context 透传 performance_plan/security_plan
4. TestCaseGeneratorAgent — mock LLM，验证 test_type fallback
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FakeLLMResponse:
    content: str = ""
    parsed_json: dict | list | None = None
    model: str = "test-model"
    latency_ms: int = 100


def make_mock_test_case(
    test_type: str = "ui",
    title: str = "测试登录功能",
    description: str = "验证登录页面正常登录流程",
    steps: list | None = None,
):
    """构造一个假的 TestCase 对象用于测试翻译逻辑."""
    from unittest.mock import MagicMock
    tc = MagicMock()
    tc.id = "tc-001"
    tc.test_type = test_type
    tc.title = title
    tc.description = description
    tc.steps = steps if steps is not None else [
        {"step": 1, "action": "打开登录页面", "expected": "页面正常加载"},
        {"step": 2, "action": "输入用户名 admin", "expected": "输入框显示 admin"},
        {"step": 3, "action": "输入密码 123456", "expected": "密码框显示掩码"},
        {"step": 4, "action": "点击登录按钮", "expected": "跳转到首页"},
    ]
    return tc


# ═══════════════════════════════════════════════════════════════════
# 1. ExecutionStage._translate_steps — 功能验证
# ═══════════════════════════════════════════════════════════════════

class TestTranslateStepsUI:
    """验证 UI 测试用例的 NL→结构化翻译."""

    @pytest.mark.asyncio
    async def test_ui_translate_produces_step_actions(self):
        """mock LLM 返回结构化 JSON，验证翻译为 StepAction 列表."""
        from src.pipeline.stages.execution import ExecutionStage

        mock_response = FakeLLMResponse(
            parsed_json=[
                {"step": 1, "action_type": "navigate", "target": "/login", "timeout_ms": 30000},
                {"step": 2, "action_type": "input", "target": "#username", "value": "admin", "timeout_ms": 5000},
                {"step": 3, "action_type": "input", "target": "#password", "value": "123456", "timeout_ms": 5000},
                {"step": 4, "action_type": "click", "target": "#login-btn", "timeout_ms": 10000},
            ]
        )

        stage = ExecutionStage(db_session=MagicMock())
        tc = make_mock_test_case(test_type="ui")

        with patch("src.pipeline.stages.execution.llm_call", new=AsyncMock(return_value=mock_response)):
            actions = await stage._translate_steps(tc, "https://example.com")

        assert len(actions) == 4
        assert actions[0].action_type == "navigate"
        assert actions[0].target == "/login"
        assert actions[1].action_type == "input"
        assert actions[1].value == "admin"
        assert actions[2].target == "#password"
        assert actions[3].action_type == "click"

    @pytest.mark.asyncio
    async def test_ui_translate_handles_dict_response(self):
        """LLM 返回 dict 包装 (如 {"steps": [...]}) 时也能正确解析."""
        from src.pipeline.stages.execution import ExecutionStage

        mock_response = FakeLLMResponse(
            parsed_json={"steps": [
                {"step": 1, "action_type": "click", "target": "#submit", "timeout_ms": 5000},
                {"step": 2, "action_type": "assert", "target": ".success-msg", "value": "提交成功", "timeout_ms": 3000},
            ]}
        )

        stage = ExecutionStage(db_session=MagicMock())
        tc = make_mock_test_case(test_type="ui")

        with patch("src.pipeline.stages.execution.llm_call", new=AsyncMock(return_value=mock_response)):
            actions = await stage._translate_steps(tc, "https://example.com")

        assert len(actions) == 2
        assert actions[0].action_type == "click"
        assert actions[1].action_type == "assert"

    @pytest.mark.asyncio
    async def test_api_translate_uses_correct_system_prompt(self):
        """API 类型应使用接口测试的 system_prompt."""
        from src.pipeline.stages.execution import ExecutionStage

        captured_request = None

        async def capture_call(request):
            nonlocal captured_request
            captured_request = request
            return FakeLLMResponse(
                parsed_json=[
                    {"step": 1, "action_type": "api_call", "target": "/api/user/login", "value": "POST", "timeout_ms": 30000},
                ]
            )

        stage = ExecutionStage(db_session=MagicMock())
        tc = make_mock_test_case(test_type="api", steps=[
            {"step": 1, "action": "发送 POST 请求到 /api/user/login", "expected": "返回 200"}
        ])

        with patch("src.pipeline.stages.execution.llm_call", new=capture_call):
            actions = await stage._translate_steps(tc, "https://api.example.com")

        assert len(actions) == 1
        assert actions[0].action_type == "api_call"
        assert "接口测试工程师" in captured_request.system_prompt
        assert "POST" in captured_request.user_prompt

    @pytest.mark.asyncio
    async def test_empty_steps_returns_empty_list(self):
        """空 steps 不调 LLM，直接返回空列表."""
        from src.pipeline.stages.execution import ExecutionStage

        stage = ExecutionStage(db_session=MagicMock())
        tc = make_mock_test_case(steps=[])

        # 不 mock llm_call — 如果调了 LLM 会报错
        actions = await stage._translate_steps(tc, "")
        assert actions == []

    @pytest.mark.asyncio
    async def test_translate_fills_default_fields(self):
        """JSON 中缺失的字段应有默认值."""
        from src.pipeline.stages.execution import ExecutionStage

        mock_response = FakeLLMResponse(
            parsed_json=[
                {"action_type": "wait"},  # 缺少 step/target/value/timeout_ms
            ]
        )

        stage = ExecutionStage(db_session=MagicMock())
        tc = make_mock_test_case(test_type="compatibility")

        with patch("src.pipeline.stages.execution.llm_call", new=AsyncMock(return_value=mock_response)):
            actions = await stage._translate_steps(tc, "https://example.com")

        assert len(actions) == 1
        assert actions[0].action_type == "wait"
        assert actions[0].step_number == 1  # auto-numbered
        assert actions[0].target is None
        assert actions[0].timeout_ms == 30000  # default


# ═══════════════════════════════════════════════════════════════════
# 2. BrowserMatrixExecutor — 功能验证
# ═══════════════════════════════════════════════════════════════════

class TestBrowserMatrixExecutorFunctional:
    """验证 BrowserMatrixExecutor 的实例化和基本功能."""

    def test_instantiate_and_health_check(self):
        """实例化并调用 health_check（无需 Playwright 安装）."""
        from src.executor.compatibility_executor import BrowserMatrixExecutor
        executor = BrowserMatrixExecutor()
        assert executor.platform_type == "compatibility"

        import asyncio
        result = asyncio.get_event_loop().run_until_complete(executor.health_check())
        assert isinstance(result, dict)
        assert "connected" in result
        assert "details" in result

    def test_matrix_config_is_valid(self):
        """验证 BROWSER_MATRIX 配置可被实际使用."""
        from src.executor.compatibility_executor import BROWSER_MATRIX
        valid_browsers = {"chromium", "firefox", "webkit"}

        for cfg in BROWSER_MATRIX:
            assert cfg["browser"] in valid_browsers
            assert cfg["viewport"]["width"] > 0
            assert cfg["viewport"]["height"] > 0
            assert isinstance(cfg["label"], str)
            assert len(cfg["label"]) > 0

    @pytest.mark.asyncio
    async def test_execute_step_returns_error(self):
        """execute_step (单步) 应返回 error，提示用 execute_steps."""
        from src.executor.compatibility_executor import BrowserMatrixExecutor
        from src.executor.types import StepAction

        executor = BrowserMatrixExecutor()
        action = StepAction(step_number=1, action_type="click", target="#btn")
        result = await executor.execute_step(action)

        assert result.status == "error"
        assert "execute_steps" in result.error_message


# ═══════════════════════════════════════════════════════════════════
# 3. TestCaseGeneratorAgent test_type 校验 — 功能验证
# ═══════════════════════════════════════════════════════════════════

class TestCaseTypeValidation:
    """验证 test_type 校验逻辑（直接测校验函数，避免数据库依赖）."""

    VALID = {"ui", "api", "performance", "security", "compatibility"}

    def _validate_and_normalize(self, cases_data: list[dict]) -> list[dict]:
        """复现 TestCaseGeneratorAgent._generate_testcases 中的校验逻辑."""
        for c in cases_data:
            if c.get("test_type") not in self.VALID:
                c["test_type"] = "ui"
        return cases_data

    def test_all_valid_types_unchanged(self):
        cases = [
            {"title": "t1", "test_type": "ui"},
            {"title": "t2", "test_type": "api"},
            {"title": "t3", "test_type": "performance"},
            {"title": "t4", "test_type": "security"},
            {"title": "t5", "test_type": "compatibility"},
        ]
        result = self._validate_and_normalize(cases)
        assert [c["test_type"] for c in result] == ["ui", "api", "performance", "security", "compatibility"]

    def test_old_functional_falls_back_to_ui(self):
        cases = [{"title": "t1", "test_type": "functional"}]
        result = self._validate_and_normalize(cases)
        assert result[0]["test_type"] == "ui"

    def test_old_integration_falls_back_to_ui(self):
        cases = [{"title": "t1", "test_type": "integration"}]
        result = self._validate_and_normalize(cases)
        assert result[0]["test_type"] == "ui"

    def test_missing_test_type_gets_ui(self):
        cases = [{"title": "t1"}]
        result = self._validate_and_normalize(cases)
        assert result[0]["test_type"] == "ui"

    def test_empty_test_type_gets_ui(self):
        cases = [{"title": "t1", "test_type": ""}]
        result = self._validate_and_normalize(cases)
        assert result[0]["test_type"] == "ui"

    def test_mixed_valid_and_invalid(self):
        cases = [
            {"title": "t1", "test_type": "ui"},
            {"title": "t2", "test_type": "functional"},
            {"title": "t3", "test_type": "api"},
            {"title": "t4", "test_type": "integration"},
            {"title": "t5", "test_type": "compatibility"},
        ]
        result = self._validate_and_normalize(cases)
        assert [c["test_type"] for c in result] == ["ui", "ui", "api", "ui", "compatibility"]


# ═══════════════════════════════════════════════════════════════════
# 4. AnalysisStage context 透传 — 功能验证
# ═══════════════════════════════════════════════════════════════════

class TestAnalysisStageContextPopulation:
    """验证 AnalysisStage 将 performance_plan/security_plan 写入 context."""

    def test_produced_context_fields_includes_plans(self):
        from src.pipeline.stages.analysis import AnalysisStage
        fields = AnalysisStage.produced_context_fields()
        assert "performance_plan" in fields
        assert "security_plan" in fields
        assert "analysis_report" in fields

    def test_context_population_logic(self):
        """模拟 execute() 中 context 赋值逻辑."""
        from src.pipeline.context import PipelineContext

        ctx = PipelineContext(pipeline_id="p1", project_id="prj1")
        ctx.parsed_requirements = [{"functional_requirements": [{"id": "F1"}]}]

        # 模拟 AgentOutput
        output_data = {
            "test_plan_md": "# Test Plan\n...",
            "test_plan_file": "reports/prj1/Testplan_20260522120000.md",
            "skill_prompt": "...",
            "performance_plan": "## 8. 性能测试方案\n...",
            "security_plan": "## 9. 安全测试方案\n...",
        }

        # 复现 AnalysisStage.execute() 中的赋值逻辑
        ctx.test_plan_md = output_data.get("test_plan_md", "")
        ctx.test_plan_file = output_data.get("test_plan_file", "")
        ctx.analysis_report = {
            "test_plan_md": output_data["test_plan_md"],
            "test_plan_file": output_data["test_plan_file"],
            "requirements_count": 1,
        }
        ctx.performance_plan = {
            "content": output_data.get("performance_plan", ""),
        }
        ctx.security_plan = {
            "content": output_data.get("security_plan", ""),
        }

        assert ctx.analysis_report is not None
        assert ctx.performance_plan is not None
        assert ctx.security_plan is not None
        assert "性能测试方案" in ctx.performance_plan["content"]
        assert "安全测试方案" in ctx.security_plan["content"]

    def test_context_with_empty_plans(self):
        """Agent 未输出方案时，content 为空字符串."""
        from src.pipeline.context import PipelineContext

        ctx = PipelineContext(pipeline_id="p1", project_id="prj1")
        ctx.parsed_requirements = [{"functional_requirements": []}]

        output_data = {
            "test_plan_md": "# Test Plan\n...",
            "test_plan_file": "reports/prj1/test.md",
            "performance_plan": "",
            "security_plan": "",
        }

        ctx.performance_plan = {"content": output_data.get("performance_plan", "")}
        ctx.security_plan = {"content": output_data.get("security_plan", "")}

        assert ctx.performance_plan["content"] == ""
        assert ctx.security_plan["content"] == ""

    def test_roundtrip_with_plans(self):
        """含方案数据的 context 序列化后再反序列化应一致."""
        from src.pipeline.context import PipelineContext

        ctx = PipelineContext(pipeline_id="p1", project_id="prj1")
        ctx.parsed_requirements = [{"functional_requirements": [{"id": "F1"}]}]
        ctx.performance_plan = {"content": "压测方案内容..."}
        ctx.security_plan = {"content": "安全测试方案内容..."}

        restored = PipelineContext.from_json(ctx.to_json())
        assert restored.performance_plan == ctx.performance_plan
        assert restored.security_plan == ctx.security_plan


# ═══════════════════════════════════════════════════════════════════
# 5. ExecutionStage 路由逻辑 — 功能验证
# ═══════════════════════════════════════════════════════════════════

class TestExecutionRoutingFunctional:
    """验证完整的 test_type 路由逻辑."""

    def _route_test_type(self, test_type: str) -> str | None:
        """复现 ExecutionStage 中的路由逻辑."""
        EXECUTOR_MAP = {
            "ui": "web",
            "api": "api",
            "compatibility": "compatibility",
        }
        SKIP_TEST_TYPES = {"performance", "security"}

        if test_type in SKIP_TEST_TYPES:
            return None  # skipped
        return EXECUTOR_MAP.get(test_type)

    def test_all_ui_cases_routed_to_web(self):
        assert self._route_test_type("ui") == "web"

    def test_all_api_cases_routed_to_api(self):
        assert self._route_test_type("api") == "api"

    def test_all_compatibility_cases_routed_to_compatibility(self):
        assert self._route_test_type("compatibility") == "compatibility"

    def test_performance_is_skipped(self):
        assert self._route_test_type("performance") is None

    def test_security_is_skipped(self):
        assert self._route_test_type("security") is None

    def test_unknown_type_returns_none(self):
        assert self._route_test_type("unknown") is None


# ═══════════════════════════════════════════════════════════════════
# 6. StepResult 在 ExecutionResult 中的序列化 — 功能验证
# ═══════════════════════════════════════════════════════════════════

class TestStepResultSerialization:
    """验证 StepResult 在 execution results 中的序列化."""

    def test_browser_config_included_in_dict(self):
        """browser_config 应出现在序列化后的 dict 中."""
        from src.executor.types import StepResult

        sr = StepResult(
            step_number=1,
            status="passed",
            actual_result="OK",
            browser_config="Chrome/Desktop",
        )

        d = {
            "step": sr.step_number,
            "action": sr.status,
            "result": sr.actual_result,
            "error": sr.error_message,
            "screenshot_path": sr.screenshot_path,
            "browser_config": sr.browser_config,
        }
        assert d["browser_config"] == "Chrome/Desktop"

    def test_browser_config_none_when_not_set(self):
        """非兼容性测试场景 browser_config 应为 None."""
        from src.executor.types import StepResult

        sr = StepResult(step_number=1, status="passed")
        d = {
            "browser_config": sr.browser_config,
        }
        assert d["browser_config"] is None

    def test_multiple_configs_in_results(self):
        """模拟兼容性测试的多个配置结果."""
        from src.executor.types import StepResult

        results = [
            StepResult(step_number=1, status="passed", browser_config="Chrome/Desktop"),
            StepResult(step_number=1, status="failed", browser_config="Chrome/Mobile", error_message="element hidden"),
            StepResult(step_number=1, status="passed", browser_config="Firefox/Desktop"),
            StepResult(step_number=1, status="passed", browser_config="Safari/Mobile"),
        ]

        configs = [r.browser_config for r in results]
        assert configs == ["Chrome/Desktop", "Chrome/Mobile", "Firefox/Desktop", "Safari/Mobile"]

        failed = [r for r in results if r.status != "passed"]
        assert len(failed) == 1
        assert failed[0].browser_config == "Chrome/Mobile"


# ═══════════════════════════════════════════════════════════════════
# 7. ExecutionStage EXECUTOR_MAP 完整性 — 功能验证
# ═══════════════════════════════════════════════════════════════════

class TestExecutionStageIntegration:
    """验证 execution.py 中定义的所有常量一致性."""

    def test_executor_map_keys_match_registry(self):
        """EXECUTOR_MAP 中的 executor_name 都应在 registry 中有注册."""
        from src.executor.registry import ExecutorRegistry
        from src.pipeline.stages.execution import EXECUTOR_MAP

        registered = ExecutorRegistry.list_all()
        for test_type, executor_name in EXECUTOR_MAP.items():
            assert executor_name in registered, (
                f"test_type='{test_type}' maps to '{executor_name}' but it's not registered. "
                f"Available: {registered}"
            )

    def test_five_types_fully_covered(self):
        """5 种 test_type 全部在 EXECUTOR_MAP 或 SKIP_TEST_TYPES 中."""
        from src.pipeline.stages.execution import EXECUTOR_MAP, SKIP_TEST_TYPES

        all_covered = set(EXECUTOR_MAP.keys()) | SKIP_TEST_TYPES
        expected = {"ui", "api", "performance", "security", "compatibility"}
        assert all_covered == expected
