"""全局配置加载器。

从 execution_runtime/config/settings.yaml 读取被测 App / 设备 / 运行参数。
task.json 中的 app/device 段可覆盖全局默认值（平台侧一般直接带出全局值）。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).parent / "config" / "settings.yaml"


@dataclass
class TargetApp:
    name: str = ""
    platform: str = "ios"
    bundle_id: str = ""  # iOS bundle_id / Android appPackage
    app_path: str | None = None
    app_activity: str = ""  # Android 启动 Activity（如 .biz.cartoon.splash.ComicSplashActivity）


@dataclass
class Device:
    udid: str = ""
    device_name: str = ""
    platform_version: str = ""
    appium_url: str = "http://127.0.0.1:4723"
    automation_name: str = "XCUITest"
    wda_bundle_id: str = ""
    wda_local_port: int = 8101
    use_prebuilt_wda: bool = False
    use_preinstalled_wda: bool = True
    skip_wda_uninstall: bool = True
    show_xcode_log: bool = False
    new_command_timeout: int = 120
    no_reset: bool = True  # Android：不重置 App 数据
    auto_grant_permissions: bool = True


@dataclass
class RunConfig:
    max_concurrency: int = 1
    case_timeout_seconds: int = 120
    step_timeout_seconds: int = 15
    max_heal_attempts: int = 2
    self_heal_enabled: bool = True
    heal_budget_setup: int = 2
    heal_budget_step: int = 2
    heal_budget_case: int = 2
    ocr_enabled: bool = True
    screenshot_each_step: bool = True
    dump_source_each_step: bool = True


@dataclass
class RuntimeConfig:
    target_app: TargetApp = field(default_factory=TargetApp)
    device: Device = field(default_factory=Device)
    run: RunConfig = field(default_factory=RunConfig)
    redact_keys: list[str] = field(default_factory=list)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _filter_kwargs(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """只保留 dataclass 已声明的字段，避免未知 key 报错。"""
    valid = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return {k: v for k, v in (data or {}).items() if k in valid}


def load_config(overrides: dict[str, Any] | None = None) -> RuntimeConfig:
    """加载全局配置，overrides（来自 task.json 的 app/device/run 段）可覆盖。"""
    raw = _load_yaml(CONFIG_PATH)
    if overrides:
        raw = _deep_merge(copy.deepcopy(raw), overrides)

    return RuntimeConfig(
        target_app=TargetApp(**_filter_kwargs(TargetApp, raw.get("target_app", {}))),
        device=Device(**_filter_kwargs(Device, raw.get("device", {}))),
        run=RunConfig(**_filter_kwargs(RunConfig, raw.get("run", {}))),
        redact_keys=list(raw.get("redact_keys", []) or []),
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, val in (override or {}).items():
        if val is None:
            continue
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(base[key], val)
        else:
            base[key] = val
    return base
