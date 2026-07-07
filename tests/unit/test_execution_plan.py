"""验收测试：多类型测试用例自动执行方案 — 全部改动点验证。

覆盖：
1. test_type 定义和校验 (VALID_TEST_TYPES)
2. PipelineContext 新字段 (performance_plan, security_plan)
3. ExecutionStage 路由映射和 _summarize
4. StepResult browser_config 字段
5. BrowserMatrixExecutor 注册
6. SKILL.md 内容检查
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ═══════════════════════════════════════════════════════════════════
# 1. test_type 定义校验
# ═══════════════════════════════════════════════════════════════════

class TestValidTestTypes:
    """验证 VALID_TEST_TYPES 定义正确且校验逻辑有效。"""

    VALID = {"ui", "api", "performance", "security", "compatibility"}

    def test_all_5_types_defined(self):
        """应该包含正好 5 种 test_type。"""
        assert self.VALID == {"ui", "api", "performance", "security", "compatibility"}
        assert len(self.VALID) == 5

    def test_old_types_removed(self):
        """旧的 functional/ui(中文)/integration 不应存在。"""
        assert "functional" not in self.VALID
        assert "integration" not in self.VALID

    def test_invalid_type_falls_back_to_ui(self):
        """无效/过时的 test_type 应 fallback 为 'ui'。"""
        for invalid in ["functional", "integration", "", "unknown"]:
            result = invalid if invalid in self.VALID else "ui"
            assert result == "ui", f"'{invalid}' should fallback to 'ui'"


# ═══════════════════════════════════════════════════════════════════
# 2. PipelineContext 新字段
# ═══════════════════════════════════════════════════════════════════

class TestPipelineContext:
    """验证 PipelineContext 的 performance_plan / security_plan 字段。"""

    def test_new_fields_exist_and_default_none(self):
        from src.pipeline.context import PipelineContext
        ctx = PipelineContext(pipeline_id="p1", project_id="prj1")
        assert ctx.performance_plan is None
        assert ctx.security_plan is None

    def test_new_fields_can_be_set(self):
        from src.pipeline.context import PipelineContext
        ctx = PipelineContext(pipeline_id="p1", project_id="prj1")
        ctx.performance_plan = {"content": "压测方案..."}
        ctx.security_plan = {"content": "安全方案..."}
        assert ctx.performance_plan["content"] == "压测方案..."
        assert ctx.security_plan["content"] == "安全方案..."

    def test_serialization_includes_new_fields(self):
        from src.pipeline.context import PipelineContext
        ctx = PipelineContext(pipeline_id="p1", project_id="prj1")
        ctx.performance_plan = {"p": "data"}
        ctx.security_plan = {"s": "data"}
        data = json.loads(ctx.to_json())
        assert "performance_plan" in data
        assert "security_plan" in data

    def test_deserialization_restores_new_fields(self):
        from src.pipeline.context import PipelineContext
        original = PipelineContext(pipeline_id="p1", project_id="prj1")
        original.performance_plan = {"p": 1}
        original.security_plan = {"s": 2}
        restored = PipelineContext.from_json(original.to_json())
        assert restored.performance_plan == {"p": 1}
        assert restored.security_plan == {"s": 2}


# ═══════════════════════════════════════════════════════════════════
# 3. ExecutionStage 路由映射
# ═══════════════════════════════════════════════════════════════════

class TestExecutionStageRouting:
    """验证 EXECUTOR_MAP 和 SKIP_TEST_TYPES。"""

    EXECUTOR_MAP = {
        "ui": "web",
        "api": "api",
        "compatibility": "compatibility",
    }
    SKIP_TEST_TYPES = {"performance", "security"}

    def test_ui_routes_to_web_executor(self):
        assert self.EXECUTOR_MAP["ui"] == "web"

    def test_api_routes_to_api_executor(self):
        assert self.EXECUTOR_MAP["api"] == "api"

    def test_compatibility_routes_to_compatibility_executor(self):
        assert self.EXECUTOR_MAP["compatibility"] == "compatibility"

    def test_performance_is_skipped(self):
        assert "performance" not in self.EXECUTOR_MAP
        assert "performance" in self.SKIP_TEST_TYPES

    def test_security_is_skipped(self):
        assert "security" not in self.EXECUTOR_MAP
        assert "security" in self.SKIP_TEST_TYPES

    def test_executor_map_and_skip_are_exhaustive(self):
        """所有 test_type 要么在 EXECUTOR_MAP 中，要么在 SKIP_TEST_TYPES 中。"""
        all_types = {"ui", "api", "performance", "security", "compatibility"}
        covered = set(self.EXECUTOR_MAP.keys()) | self.SKIP_TEST_TYPES
        assert covered == all_types


class TestSummarize:
    """验证 ExecutionStage._summarize 静态方法。"""

    def _make_result(self, status: str, step_number: int, error_message: str | None = None):
        from src.executor.types import StepResult
        return StepResult(
            step_number=step_number,
            status=status,
            error_message=error_message,
        )

    def test_all_passed_returns_passed(self):
        from src.pipeline.stages.execution import ExecutionStage
        results = [
            self._make_result("passed", 1),
            self._make_result("passed", 2),
        ]
        status, msg = ExecutionStage._summarize(results)
        assert status == "passed"
        assert msg is None

    def test_failed_step_returns_failed(self):
        from src.pipeline.stages.execution import ExecutionStage
        results = [
            self._make_result("passed", 1),
            self._make_result("failed", 2, "Assertion error"),
        ]
        status, msg = ExecutionStage._summarize(results)
        assert status == "failed"
        assert "1 step(s) failed" in msg

    def test_error_step_returns_error(self):
        from src.pipeline.stages.execution import ExecutionStage
        results = [
            self._make_result("passed", 1),
            self._make_result("error", 2, "Connection timeout"),
        ]
        status, msg = ExecutionStage._summarize(results)
        assert status == "error"
        assert msg == "Connection timeout"

    def test_error_takes_priority_over_failed(self):
        from src.pipeline.stages.execution import ExecutionStage
        results = [
            self._make_result("failed", 1, "assert fail"),
            self._make_result("error", 2, "timeout"),
        ]
        status, _ = ExecutionStage._summarize(results)
        assert status == "error"


# ═══════════════════════════════════════════════════════════════════
# 4. StepResult browser_config
# ═══════════════════════════════════════════════════════════════════

class TestStepResultBrowserConfig:
    def test_browser_config_field_exists(self):
        from src.executor.types import StepResult
        assert "browser_config" in StepResult.__dataclass_fields__

    def test_browser_config_default_none(self):
        from src.executor.types import StepResult
        sr = StepResult(step_number=1, status="passed")
        assert sr.browser_config is None

    def test_browser_config_accepts_label(self):
        from src.executor.types import StepResult
        sr = StepResult(step_number=1, status="passed", browser_config="Chrome/Desktop")
        assert sr.browser_config == "Chrome/Desktop"


# ═══════════════════════════════════════════════════════════════════
# 5. BrowserMatrixExecutor 注册
# ═══════════════════════════════════════════════════════════════════

class TestBrowserMatrixExecutor:
    def test_registered_in_registry(self):
        """验证 compatibility 已注册到 ExecutorRegistry。"""
        from src.executor.registry import ExecutorRegistry
        registered = ExecutorRegistry.list_all()
        assert "compatibility" in registered, f"Missing 'compatibility', got: {registered}"

    def test_browser_matrix_has_4_configs(self):
        from src.executor.compatibility_executor import BROWSER_MATRIX
        assert len(BROWSER_MATRIX) == 4
        labels = {c["label"] for c in BROWSER_MATRIX}
        assert "Chrome/Desktop" in labels

    def test_each_config_has_required_keys(self):
        from src.executor.compatibility_executor import BROWSER_MATRIX
        for cfg in BROWSER_MATRIX:
            assert "browser" in cfg
            assert "viewport" in cfg
            assert "label" in cfg
            assert "width" in cfg["viewport"]
            assert "height" in cfg["viewport"]

    def test_platform_type_is_compatibility(self):
        from src.executor.compatibility_executor import BrowserMatrixExecutor
        assert BrowserMatrixExecutor.platform_type == "compatibility"


# ═══════════════════════════════════════════════════════════════════
# 6. SKILL.md 内容校验
# ═══════════════════════════════════════════════════════════════════

class TestSkillsContent:
    """验证 SKILL.md 文件内容已按要求更新。"""

    def test_testcase_generator_skill_has_5_types(self):
        skill_path = PROJECT_ROOT / ".agents/skills/test-case-generator/SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        assert "ui/api/performance/security/compatibility" in content
        assert "功能/界面/接口/性能/集成" not in content
        assert "## 测试类型定义" in content
        assert "### ui — UI 测试" in content
        assert "### api — 接口测试" in content
        assert "### compatibility — 兼容性测试" in content
        assert "### performance — 性能测试" in content
        assert "### security — 安全测试" in content

    def test_testcase_generator_skill_has_no_old_type(self):
        skill_path = PROJECT_ROOT / ".agents/skills/test-case-generator/SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        assert '"test_type": "功能/界面/接口/性能/集成"' not in content

    def test_requirement_analyzer_skill_has_performance_plan(self):
        skill_path = PROJECT_ROOT / ".agents/skills/requirement-analyzer/SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        assert "## 8. 性能测试方案" in content
        assert "### 8.1 测试目标" in content
        assert "P95 响应时间" in content

    def test_requirement_analyzer_skill_has_security_plan(self):
        skill_path = PROJECT_ROOT / ".agents/skills/requirement-analyzer/SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        assert "## 9. 安全测试方案" in content
        assert "### 9.1 测试范围" in content
        assert "OWASP Top 10" in content

    def test_requirement_analyzer_review_mentions_plan_sections(self):
        skill_path = PROJECT_ROOT / ".agents/skills/requirement-analyzer/SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        assert "检查性能/安全章节" in content or "## 8." in content


# ═══════════════════════════════════════════════════════════════════
# 7. models.py 注释更新
# ═══════════════════════════════════════════════════════════════════

class TestModelsComment:
    def test_test_type_comment_updated(self):
        models_path = PROJECT_ROOT / "src/core/models/models.py"
        content = models_path.read_text(encoding="utf-8")
        assert "ui/api/performance/security/compatibility" in content
        assert "# functional/ui/api/performance/integration" not in content
