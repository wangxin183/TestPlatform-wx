"""pytest fixtures：真机 Appium 会话（session 级复用）+ 执行上下文。"""

from __future__ import annotations

import pytest

from execution_runtime.config import load_config
from execution_runtime.engine.appium_driver import build_driver
from execution_runtime.pytest_exec.context import ExecContext
from execution_runtime.session import ModuleSessionCoordinator


@pytest.fixture(scope="session")
def exec_context() -> ExecContext:
    return ExecContext.load()


@pytest.fixture(scope="session")
def runtime_config(exec_context: ExecContext):
    return load_config(exec_context.config_overrides)


@pytest.fixture(scope="session")
def module_session() -> ModuleSessionCoordinator:
    return ModuleSessionCoordinator()


@pytest.fixture(scope="session")
def precondition_setup_state() -> dict:
    """登录指纹与入口指纹分开缓存，避免搜索前重复进「我的」。"""
    return {"login_fp": None, "entry_fp": None, "fingerprint": None, "warnings": []}


@pytest.fixture(scope="session")
def driver(runtime_config):
    """单真机会话，session 级复用，跑完统一 quit。"""
    drv = build_driver(runtime_config)
    try:
        yield drv
    finally:
        try:
            drv.quit()
        except Exception:
            pass
