"""页面恢复：回退探测 + 可选杀进程重拉。

供 ToolGateway（Agent/DSL）与 precondition Setup 共用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


ExistsFn = Callable[[dict[str, str], int], bool]
ExecFn = Callable[..., Any]


@dataclass
class RecoverResult:
    ok: bool
    via: str = ""  # already_visible | back | relaunch | backs_only
    backs: int = 0
    relaunched: bool = False
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def recover_page(
    *,
    exists: ExistsFn,
    execute: ExecFn,
    until: dict[str, str] | None = None,
    max_backs: int = 3,
    relaunch: bool | None = None,
    probe_timeout: int = 2,
    settle_seconds: float = 2.5,
) -> RecoverResult:
    """找不到目标页/元素时回退；仍失败可 terminate + launch。

    - until 有值：回退/重拉后直到 until 可见才算成功
    - until 为空：仅执行最多 max_backs 次回退；relaunch=True 时再杀进程重拉
    - relaunch 默认：有 until 时 True，无 until 时 False
    """
    max_backs = max(0, int(max_backs))
    probe_timeout = max(1, int(probe_timeout))
    if relaunch is None:
        relaunch = until is not None
    warnings: list[str] = []

    if until and exists(until, probe_timeout):
        return RecoverResult(
            ok=True,
            via="already_visible",
            message="目标已可见，无需回退",
            warnings=warnings,
        )

    backs = 0
    for i in range(1, max_backs + 1):
        warnings.append(f"执行回退 ({i}/{max_backs})")
        try:
            execute("back")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"回退失败: {exc}")
            break
        backs = i
        time.sleep(0.4)
        if until and exists(until, probe_timeout):
            return RecoverResult(
                ok=True,
                via="back",
                backs=backs,
                message=f"回退 {backs} 次后定位到目标",
                warnings=warnings,
            )

    if until is None:
        if relaunch:
            _relaunch(execute, warnings, settle_seconds=settle_seconds)
            return RecoverResult(
                ok=True,
                via="relaunch",
                backs=backs,
                relaunched=True,
                message=f"已回退 {backs} 次并重拉 App",
                warnings=warnings,
            )
        return RecoverResult(
            ok=True,
            via="backs_only",
            backs=backs,
            message=f"已回退 {backs} 次",
            warnings=warnings,
        )

    if not relaunch:
        return RecoverResult(
            ok=False,
            via="back",
            backs=backs,
            message=f"回退 {backs} 次后仍未出现目标",
            warnings=warnings,
        )

    _relaunch(execute, warnings, settle_seconds=settle_seconds)
    if exists(until, max(probe_timeout, 5)):
        return RecoverResult(
            ok=True,
            via="relaunch",
            backs=backs,
            relaunched=True,
            message="重拉 App 后定位到目标",
            warnings=warnings,
        )
    return RecoverResult(
        ok=False,
        via="relaunch",
        backs=backs,
        relaunched=True,
        message=f"回退 {backs} 次并重拉 App 后仍未出现目标",
        warnings=warnings,
    )


def _relaunch(
    execute: ExecFn,
    warnings: list[str],
    *,
    settle_seconds: float,
) -> None:
    warnings.append("回退仍无法定位目标，杀掉 App 并重新拉起")
    try:
        execute("terminate_app")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"terminate_app 失败: {exc}")
    time.sleep(1.0)
    execute("launch_app")
    time.sleep(max(1.0, float(settle_seconds)))
