"""Appium 驱动构建（iOS XCUITest / Android UiAutomator2）。"""

from __future__ import annotations

from typing import Any

from execution_runtime.config import RuntimeConfig


def _platform(cfg: RuntimeConfig) -> str:
    return (cfg.target_app.platform or "ios").lower()


def build_ios_capabilities(cfg: RuntimeConfig) -> dict[str, Any]:
    dev = cfg.device
    app = cfg.target_app
    caps: dict[str, Any] = {
        "platformName": "iOS",
        "appium:automationName": dev.automation_name or "XCUITest",
        "appium:udid": dev.udid,
        "appium:deviceName": dev.device_name,
        "appium:platformVersion": dev.platform_version,
        "appium:bundleId": app.bundle_id,
        "appium:newCommandTimeout": dev.new_command_timeout,
        "appium:autoAcceptAlerts": False,
        "appium:waitForQuiescence": False,
    }
    if dev.wda_bundle_id:
        caps["appium:updatedWDABundleId"] = dev.wda_bundle_id
    if dev.wda_local_port:
        caps["appium:wdaLocalPort"] = dev.wda_local_port
    if dev.use_preinstalled_wda:
        caps["appium:usePreinstalledWDA"] = True
    if dev.skip_wda_uninstall:
        caps["appium:skipUninstall"] = True
    if dev.use_prebuilt_wda:
        caps["appium:usePrebuiltWDA"] = True
        caps["appium:useNewWDA"] = False
    else:
        caps["appium:useNewWDA"] = False
    if dev.show_xcode_log:
        caps["appium:showXcodeLog"] = True
    caps["appium:wdaLaunchTimeout"] = 120000
    if app.app_path:
        caps["appium:app"] = app.app_path
    return caps


def build_android_capabilities(cfg: RuntimeConfig) -> dict[str, Any]:
    dev = cfg.device
    app = cfg.target_app
    caps: dict[str, Any] = {
        "platformName": "Android",
        "appium:automationName": dev.automation_name or "UiAutomator2",
        "appium:udid": dev.udid,
        "appium:deviceName": dev.device_name,
        "appium:platformVersion": dev.platform_version,
        "appium:appPackage": app.bundle_id,
        "appium:newCommandTimeout": dev.new_command_timeout,
        "appium:noReset": dev.no_reset,
        "appium:autoGrantPermissions": dev.auto_grant_permissions,
        "appium:ignoreHiddenApiPolicyError": True,
    }
    if app.app_activity:
        caps["appium:appActivity"] = app.app_activity
    if app.app_path:
        caps["appium:app"] = app.app_path
    return caps


def build_capabilities(cfg: RuntimeConfig) -> dict[str, Any]:
    if _platform(cfg) == "android":
        return build_android_capabilities(cfg)
    return build_ios_capabilities(cfg)


def build_ios_driver(cfg: RuntimeConfig):
    """创建 Appium XCUITest driver（iOS 真机/模拟器）。"""
    from appium import webdriver
    from appium.options.ios import XCUITestOptions

    caps = build_ios_capabilities(cfg)
    options = XCUITestOptions()
    options.load_capabilities({k: v for k, v in caps.items() if v not in (None, "")})
    return webdriver.Remote(
        command_executor=cfg.device.appium_url,
        options=options,
    )


def build_android_driver(cfg: RuntimeConfig):
    """创建 Appium UiAutomator2 driver（Android 真机/模拟器）。"""
    from appium import webdriver
    from appium.options.android import UiAutomator2Options

    caps = build_android_capabilities(cfg)
    options = UiAutomator2Options()
    options.load_capabilities({k: v for k, v in caps.items() if v not in (None, "")})
    return webdriver.Remote(
        command_executor=cfg.device.appium_url,
        options=options,
    )


def build_driver(cfg: RuntimeConfig):
    """按 target_app.platform 创建 Appium driver。"""
    if _platform(cfg) == "android":
        return build_android_driver(cfg)
    return build_ios_driver(cfg)
