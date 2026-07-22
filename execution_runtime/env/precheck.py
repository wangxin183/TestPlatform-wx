"""环境/设备预检 gate（iOS 真机 / Android 模拟器·真机）。

运行时启动即自动执行，不需要平台前端手动触发。任一 blocking 项失败 →
返回 ok=False，runner 拒绝执行并打印可执行修复指引。
"""

from __future__ import annotations

import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field

from execution_runtime.config import RuntimeConfig


@dataclass
class EnvItem:
    key: str
    name: str
    ok: bool
    blocking: bool
    detail: str = ""
    fix_hint: str = ""


@dataclass
class EnvCheckResult:
    ok: bool
    items: list[EnvItem] = field(default_factory=list)

    def blocking_failures(self) -> list[EnvItem]:
        return [i for i in self.items if i.blocking and not i.ok]

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "items": [i.__dict__ for i in self.items],
        }


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _check_python_pkg(module: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(module) is not None


def _appium_server_reachable(appium_url: str) -> bool:
    url = appium_url.rstrip("/") + "/status"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _try_start_appium(appium_url: str) -> bool:
    if shutil.which("appium") is None:
        return False
    try:
        subprocess.Popen(
            ["appium", "--relaxed-security"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False
    for _ in range(20):
        time.sleep(1)
        if _appium_server_reachable(appium_url):
            return True
    return False


def _common_items(cfg: RuntimeConfig, auto_repair: bool) -> list[EnvItem]:
    items: list[EnvItem] = []
    dev = cfg.device

    has_appium_py = _check_python_pkg("appium")
    items.append(EnvItem(
        key="appium_python_client",
        name="Appium-Python-Client",
        ok=has_appium_py,
        blocking=True,
        detail="已安装" if has_appium_py else "未安装",
        fix_hint="pip install Appium-Python-Client",
    ))

    appium_cli = shutil.which("appium")
    items.append(EnvItem(
        key="appium_cli",
        name="Appium CLI",
        ok=appium_cli is not None,
        blocking=True,
        detail=appium_cli or "未找到 appium 命令",
        fix_hint="npm i -g appium",
    ))

    reachable = _appium_server_reachable(dev.appium_url)
    if not reachable and auto_repair:
        reachable = _try_start_appium(dev.appium_url)
    items.append(EnvItem(
        key="appium_server",
        name="Appium Server",
        ok=reachable,
        blocking=True,
        detail=f"{dev.appium_url} {'可达' if reachable else '不可达'}",
        fix_hint=f"appium --relaxed-security（监听 {dev.appium_url}）",
    ))

    has_ocr = _check_python_pkg("paddleocr")
    items.append(EnvItem(
        key="paddleocr",
        name="PaddleOCR（OCR 兜底）",
        ok=has_ocr,
        blocking=False,
        detail="已安装" if has_ocr else "未安装（OCR 兜底不可用，其余流程不受影响）",
        fix_hint="pip install paddleocr paddlepaddle",
    ))
    return items


def _run_precheck_ios(cfg: RuntimeConfig, auto_repair: bool) -> EnvCheckResult:
    items = _common_items(cfg, auto_repair)
    dev = cfg.device
    app = cfg.target_app
    appium_cli = shutil.which("appium")

    if appium_cli:
        code, out = _run(["appium", "driver", "list", "--installed"], timeout=30)
        has_driver = "xcuitest" in out.lower()
    else:
        has_driver = False
        out = "appium CLI 缺失"
    items.insert(3, EnvItem(
        key="xcuitest_driver",
        name="Appium xcuitest driver",
        ok=has_driver,
        blocking=True,
        detail="已安装" if has_driver else out[:200],
        fix_hint="appium driver install xcuitest",
    ))

    idevice_id = shutil.which("idevice_id")
    device_online = False
    dev_detail = ""
    if idevice_id:
        code, out = _run(["idevice_id", "-l"], timeout=15)
        udids = [x.strip() for x in out.splitlines() if x.strip()]
        device_online = dev.udid in udids
        dev_detail = f"在线设备: {udids or '无'}"
    else:
        dev_detail = "libimobiledevice 未安装（idevice_id 缺失）"
    items.append(EnvItem(
        key="ios_device",
        name=f"iOS 设备 {dev.device_name}",
        ok=device_online,
        blocking=True,
        detail=dev_detail,
        fix_hint="确认手机已连接并信任本机；brew install libimobiledevice",
    ))

    app_installed = False
    app_detail = ""
    if device_online and shutil.which("ideviceinstaller"):
        code, out = _run(["ideviceinstaller", "-u", dev.udid, "list"], timeout=40)
        app_installed = app.bundle_id in out
        app_detail = f"{app.bundle_id} {'已安装' if app_installed else '未找到'}"
    elif not shutil.which("ideviceinstaller"):
        app_detail = "ideviceinstaller 缺失，跳过 App 校验（非阻断降级）"
    else:
        app_detail = "设备离线，无法校验 App"
    items.append(EnvItem(
        key="target_app",
        name=f"被测 App {app.name}",
        ok=app_installed or (not shutil.which("ideviceinstaller")),
        blocking=True,
        detail=app_detail,
        fix_hint=f"在真机安装 {app.name}（{app.bundle_id}）",
    ))

    wda_ok = True
    wda_detail = "跳过校验"
    if dev.wda_bundle_id and device_online and shutil.which("ideviceinstaller"):
        code, out = _run(["ideviceinstaller", "-u", dev.udid, "list"], timeout=40)
        wda_ok = dev.wda_bundle_id in out
        wda_detail = f"{dev.wda_bundle_id} {'已装' if wda_ok else '未装（首次运行 Appium 会自动构建）'}"
    items.append(EnvItem(
        key="wda",
        name="WebDriverAgent",
        ok=wda_ok,
        blocking=False,
        detail=wda_detail,
        fix_hint="首次运行由 Appium 自动构建注入，或预装 WDA",
    ))

    ok = all(i.ok for i in items if i.blocking)
    return EnvCheckResult(ok=ok, items=items)


def _adb_devices() -> list[str]:
    adb = shutil.which("adb")
    if not adb:
        return []
    code, out = _run([adb, "devices"], timeout=15)
    devices: list[str] = []
    for line in out.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def _android_app_installed(package: str, udid: str) -> bool:
    adb = shutil.which("adb")
    if not adb:
        return False
    cmd = [adb]
    if udid:
        cmd.extend(["-s", udid])
    cmd.extend(["shell", "pm", "list", "packages", package])
    code, out = _run(cmd, timeout=20)
    return package in out


def _run_precheck_android(cfg: RuntimeConfig, auto_repair: bool) -> EnvCheckResult:
    items = _common_items(cfg, auto_repair)
    dev = cfg.device
    app = cfg.target_app
    appium_cli = shutil.which("appium")

    if appium_cli:
        code, out = _run(["appium", "driver", "list", "--installed"], timeout=30)
        has_driver = "uiautomator2" in out.lower()
    else:
        has_driver = False
        out = "appium CLI 缺失"
    items.insert(3, EnvItem(
        key="uiautomator2_driver",
        name="Appium uiautomator2 driver",
        ok=has_driver,
        blocking=True,
        detail="已安装" if has_driver else out[:200],
        fix_hint="appium driver install uiautomator2",
    ))

    adb_cli = shutil.which("adb")
    items.append(EnvItem(
        key="adb_cli",
        name="Android adb",
        ok=adb_cli is not None,
        blocking=True,
        detail=adb_cli or "未找到 adb",
        fix_hint="安装 Android SDK platform-tools 并配置 PATH",
    ))

    online = dev.udid in _adb_devices() if adb_cli else False
    all_devs = _adb_devices() if adb_cli else []
    items.append(EnvItem(
        key="android_device",
        name=f"Android 设备 {dev.device_name}",
        ok=online,
        blocking=True,
        detail=f"在线设备: {all_devs or '无'}",
        fix_hint="启动模拟器或连接真机；adb devices 应显示 device",
    ))

    app_installed = _android_app_installed(app.bundle_id, dev.udid) if online else False
    items.append(EnvItem(
        key="target_app",
        name=f"被测 App {app.name}",
        ok=app_installed,
        blocking=True,
        detail=f"{app.bundle_id} {'已安装' if app_installed else '未找到'}",
        fix_hint=f"adb install 安装 {app.name}（{app.bundle_id}）",
    ))

    ok = all(i.ok for i in items if i.blocking)
    return EnvCheckResult(ok=ok, items=items)


def run_precheck(
    cfg: RuntimeConfig,
    *,
    auto_repair: bool = True,
) -> EnvCheckResult:
    """按 platform 执行预检（ios / android）。"""
    platform = (cfg.target_app.platform or "ios").lower()
    if platform == "android":
        return _run_precheck_android(cfg, auto_repair=auto_repair)
    return _run_precheck_ios(cfg, auto_repair=auto_repair)


def format_report(result: EnvCheckResult) -> str:
    lines = ["环境/设备预检结果:"]
    for i in result.items:
        mark = "✅" if i.ok else ("❌" if i.blocking else "⚠️")
        lines.append(f"  {mark} [{i.name}] {i.detail}")
        if not i.ok:
            lines.append(f"       修复: {i.fix_hint}")
    lines.append(f"总结论: {'通过' if result.ok else '未通过（存在阻断项）'}")
    return "\n".join(lines)
