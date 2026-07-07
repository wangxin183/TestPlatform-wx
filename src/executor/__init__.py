"""Executor package — imports all executor modules to populate ExecutorRegistry."""

from src.executor.web_executor import PlaywrightExecutor  # noqa: F401
from src.executor.api_executor import APIExecutor  # noqa: F401
from src.executor.mobile_executor import IOSExecutor, AndroidExecutor  # noqa: F401
from src.executor.miniprogram_executor import MiniProgramExecutor  # noqa: F401
from src.executor.compatibility_executor import BrowserMatrixExecutor  # noqa: F401
from src.executor.performance_executor import PerformanceExecutor  # noqa: F401
