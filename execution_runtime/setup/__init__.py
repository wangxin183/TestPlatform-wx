"""前置条件 Setup 包。"""

from execution_runtime.setup.precondition import (
    PreconditionSetupError,
    SetupResult,
    ensure_app_launched,
    ensure_entry_context,
    ensure_login_state,
    run_entry_setup,
    run_login_setup,
    run_precondition_setup,
)
from execution_runtime.setup.security_check import solve_security_check

__all__ = [
    "PreconditionSetupError",
    "SetupResult",
    "ensure_app_launched",
    "ensure_entry_context",
    "ensure_login_state",
    "run_entry_setup",
    "run_login_setup",
    "run_precondition_setup",
    "solve_security_check",
]
