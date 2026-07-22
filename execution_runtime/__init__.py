"""独立测试执行运行时（execution_runtime）。

与 TestPlatform 解耦：平台导出 task.json → 本运行时子进程执行 → 产出
allure-report/ + summary.json + defects.json 供平台回读。

第一阶段：iOS 真机（XCUITest）跑通「爱奇艺叭嗒」App UI 用例。

设计文档：docs/execution_runtime_design.md ；Agent Harness：docs/superpowers/plans/2026-07-22-agent-engineering-self-heal.md
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
