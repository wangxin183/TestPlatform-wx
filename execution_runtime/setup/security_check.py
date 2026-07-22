"""登录「安全检测」点选图：Vision LLM 给出坐标并 tap_xy。"""

from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import time
from pathlib import Path
from typing import Any

from execution_runtime.testdata import ExecutionTestdata, load_execution_testdata


SYSTEM_PROMPT = """你是移动端 UI 自动化助手，专门破解「安全检测」点选验证码。
常见两类：
A) 顶部汉字词组（如「老火汤」）→ 按词组从左到右点大图中对应汉字中心
B) 顶部若干箭头 → 大图彩色对象内部有同样箭头，按顶部箭头顺序点对象中心

只输出 JSON，不要其它文字。"""


USER_PROMPT_TMPL = """整屏手机截图，像素 {img_w}x{img_h}。下半屏有「安全检测」弹层。
顶部：「请在下图依次点击」+ 目标序列；中间大图待点；底部「确定」。

规则：
- 汉字：按词组顺序点大图同字中心
- 箭头：点内部箭头与顶部目标一致的彩色对象中心

坐标系（Qwen-VL 规范）：x/y 均为 0~1000 的相对坐标（相对整图宽高，不是像素）。
只输出：
{{"clicks":[[x1,y1],[x2,y2],[x3,y3]],"confirm":[cx,cy]}}

约束：clicks 数=目标数；点对象内部；不要点顶部小图标。
"""


def _png_size(png: bytes) -> tuple[int, int]:
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    w, h = struct.unpack(">II", png[16:24])
    return int(w), int(h)


def _scale_from_norm1000(
    clicks: list[tuple[int, int]],
    confirm: tuple[int, int] | None,
    *,
    img_w: int,
    img_h: int,
) -> tuple[list[tuple[int, int]], tuple[int, int] | None]:
    """Qwen-VL 常用 0~1000 相对坐标 → 像素。"""
    if img_w <= 0 or img_h <= 0:
        return clicks, confirm

    def _one(x: int, y: int) -> tuple[int, int]:
        return int(round(x / 1000.0 * img_w)), int(round(y / 1000.0 * img_h))

    mapped = [_one(x, y) for x, y in clicks]
    conf2 = _one(confirm[0], confirm[1]) if confirm else None
    return mapped, conf2


def _scale_point(
    x: int,
    y: int,
    *,
    img_w: int,
    img_h: int,
    win_w: int,
    win_h: int,
) -> tuple[int, int]:
    if img_w <= 0 or img_h <= 0 or win_w <= 0 or win_h <= 0:
        return x, y
    if img_w == win_w and img_h == win_h:
        return x, y
    sx = win_w / float(img_w)
    sy = win_h / float(img_h)
    return int(round(x * sx)), int(round(y * sy))


def _repair_vision_json_text(text: str) -> str:
    """修复模型常见非法写法: "x": 496, 752 → "x": 496, "y": 752"""
    import re

    return re.sub(
        r'"x"\s*:\s*(-?\d+)\s*,\s*(-?\d+)',
        r'"x": \1, "y": \2',
        text or "",
    )


def _parse_plan(parsed: Any) -> tuple[list[tuple[int, int]], tuple[int, int] | None]:
    if isinstance(parsed, str):
        from src.llm.providers.deepseek import DeepSeekProvider

        parsed = DeepSeekProvider._extract_json(_repair_vision_json_text(parsed))
    if not isinstance(parsed, dict):
        raise ValueError("Vision 返回非 JSON object")
    clicks_raw = parsed.get("clicks") or []
    if not isinstance(clicks_raw, list) or not clicks_raw:
        raise ValueError("Vision 未返回 clicks")
    clicks: list[tuple[int, int]] = []
    for item in clicks_raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            clicks.append((int(item[0]), int(item[1])))
        elif isinstance(item, dict) and "x" in item and "y" in item:
            clicks.append((int(item["x"]), int(item["y"])))
    if not clicks:
        raise ValueError("Vision clicks 为空或格式错误")
    confirm = None
    c = parsed.get("confirm")
    if isinstance(c, (list, tuple)) and len(c) >= 2:
        confirm = (int(c[0]), int(c[1]))
    elif isinstance(c, dict) and "x" in c and "y" in c:
        confirm = (int(c["x"]), int(c["y"]))
    return clicks, confirm


def _bounds_for_text(source: str, needle: str) -> tuple[int, int, int, int] | None:
    import re

    patterns = [
        rf'text="{re.escape(needle)}"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',
        rf'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"[^>]*text="{re.escape(needle)}"',
    ]
    for pat in patterns:
        m = re.search(pat, source or "")
        if m:
            return tuple(int(m.group(i)) for i in range(1, 5))  # type: ignore[return-value]
    return None


def _modal_crop_box(
    page_source: str,
    *,
    img_w: int,
    img_h: int,
) -> tuple[int, int, int, int]:
    """返回裁剪框 (left, top, right, bottom)。

    优先只裁「大图」区域（tip 下方到 确定 上方），减少灰边干扰。
    """
    tip = _bounds_for_text(page_source, "请在下图依次点击")
    confirm = _bounds_for_text(page_source, "确定")
    title = _bounds_for_text(page_source, "安全检测")

    if tip and confirm and confirm[1] - tip[1] > 160:
        # 含顶部目标序列，不含「确定」按钮与大量灰边
        top = max(0, tip[1] - 12)
        bottom = confirm[1] - 4
        left = max(0, min(tip[0], confirm[0]) - 36)
        right = min(img_w, max(tip[2], confirm[2]) + 36)
        return left, top, right, bottom

    if tip:
        top = max(0, tip[1] - 36)
    elif title:
        top = max(0, title[1] - 12)
    else:
        top = int(img_h * 0.40)

    if confirm:
        bottom = min(img_h, confirm[3] + 36)
    else:
        bottom = min(img_h, int(img_h * 0.92))

    if confirm and tip:
        left = max(0, min(tip[0], confirm[0]) - 48)
        right = min(img_w, max(tip[2], confirm[2]) + 48)
    elif confirm:
        left = max(0, confirm[0] - 48)
        right = min(img_w, confirm[2] + 48)
    else:
        left = int(img_w * 0.06)
        right = int(img_w * 0.94)

    if bottom - top < 200:
        top = int(img_h * 0.40)
        bottom = int(img_h * 0.92)
        left, right = int(img_w * 0.06), int(img_w * 0.94)
    return left, top, right, bottom


def _crop_is_mostly_blank(crop_png: bytes, *, threshold: float = 0.92) -> bool:
    """裁剪图近乎纯白/纯灰 → 验证码尚未加载。"""
    from PIL import Image

    im = Image.open(io.BytesIO(crop_png)).convert("L")
    # 降采样加速
    im = im.resize((max(1, im.width // 8), max(1, im.height // 8)))
    pixels = list(im.getdata())
    if not pixels:
        return True
    bright = sum(1 for p in pixels if p >= 245)
    return (bright / float(len(pixels))) >= threshold


def _wait_captcha_ready(gateway, *, timeout: float = 18.0) -> bytes:
    """等到安全检测大图出现后再截图，避免白屏送 Vision。"""
    deadline = time.time() + timeout
    last_png = b""
    while time.time() < deadline:
        last_png = _screenshot_png(gateway)
        img_w, img_h = _png_size(last_png)
        win_w, win_h = _window_size(gateway)
        obs = None
        try:
            from execution_runtime.setup.precondition import _observe

            obs = _observe(gateway)
            src = obs.source or ""
        except Exception:
            src = ""
        box = _modal_crop_box(src, img_w=img_w or win_w, img_h=img_h or win_h)
        crop_png, _, _, _, _ = _crop_png(last_png, box)
        has_tip = ("请在下图依次点击" in src) or ("确定" in src)
        if has_tip and not _crop_is_mostly_blank(crop_png):
            return last_png
        if not _crop_is_mostly_blank(crop_png, threshold=0.88):
            return last_png
        time.sleep(1.0)
    return last_png or _screenshot_png(gateway)


def _crop_png(
    png: bytes,
    box: tuple[int, int, int, int],
) -> tuple[bytes, int, int, int, int]:
    """裁剪 PNG，返回 (crop_png, crop_w, crop_h, offset_x, offset_y)。"""
    from PIL import Image

    left, top, right, bottom = box
    im = Image.open(io.BytesIO(png)).convert("RGB")
    img_w, img_h = im.size
    left = max(0, min(left, img_w - 1))
    top = max(0, min(top, img_h - 1))
    right = max(left + 1, min(right, img_w))
    bottom = max(top + 1, min(bottom, img_h))
    crop = im.crop((left, top, right, bottom))
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue(), crop.size[0], crop.size[1], left, top


def _map_crop_to_full(
    clicks: list[tuple[int, int]],
    confirm: tuple[int, int] | None,
    *,
    offset_x: int,
    offset_y: int,
    crop_w: int,
    crop_h: int,
) -> tuple[list[tuple[int, int]], tuple[int, int] | None]:
    mapped: list[tuple[int, int]] = []
    for x, y in clicks:
        # 允许轻微越界，钳制到裁剪图内再映射
        cx = max(0, min(x, max(0, crop_w - 1)))
        cy = max(0, min(y, max(0, crop_h - 1)))
        mapped.append((cx + offset_x, cy + offset_y))
    conf2 = None
    if confirm:
        cx = max(0, min(confirm[0], max(0, crop_w - 1)))
        cy = max(0, min(confirm[1], max(0, crop_h - 1)))
        conf2 = (cx + offset_x, cy + offset_y)
    return mapped, conf2


def _validate_clicks(
    clicks: list[tuple[int, int]],
    *,
    img_h: int,
    crop_top: int,
    crop_bottom: int,
) -> None:
    if not clicks:
        raise RuntimeError("Vision clicks 为空")
    bad = [c for c in clicks if c[1] < crop_top + 20 or c[1] > crop_bottom - 20]
    if bad and img_h > 0:
        # 允许一点误差；若整体跑出弹层则失败
        outside = [c for c in clicks if c[1] < crop_top - 40 or c[1] > crop_bottom + 40]
        if outside:
            raise RuntimeError(f"Vision 坐标超出弹层: clicks={clicks}")
    if len(clicks) >= 2:
        xs = [c[0] for c in clicks]
        if max(xs) - min(xs) < 60:
            raise RuntimeError(
                f"Vision 坐标疑似塌缩到同一竖线: clicks={clicks}"
            )


def _save_debug(
    *,
    attempt: int,
    crop_png: bytes,
    parsed: Any,
    clicks: list[tuple[int, int]],
    confirm: tuple[int, int] | None,
    note: str,
) -> None:
    try:
        root = Path("storage/security_check_debug")
        root.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        stem = root / f"{ts}_a{attempt}"
        stem.with_suffix(".png").write_bytes(crop_png)
        stem.with_suffix(".json").write_text(
            json.dumps(
                {
                    "note": note,
                    "parsed": parsed,
                    "clicks": clicks,
                    "confirm": confirm,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _exec_tap_xy(gateway, x: int, y: int) -> None:
    """优先 adb input tap（WebView 验证码更稳），失败再走 Appium tap_xy。"""
    import shutil
    import subprocess

    x, y = int(x), int(y)
    adb = shutil.which("adb")
    cfg = getattr(gateway, "cfg", None)
    if adb and cfg is not None:
        cmd = [adb]
        try:
            device = getattr(cfg, "device", None)
            udid = str(getattr(device, "udid", "") or "").strip()
            if udid:
                cmd.extend(["-s", udid])
        except Exception:
            pass
        cmd.extend(["shell", "input", "tap", str(x), str(y)])
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=8)
            return
        except Exception:
            pass

    from execution_runtime.dsl.models import Step

    gateway.executor.execute(
        Step.model_validate(
            {"action": "tap_xy", "description": "security_check", "x": x, "y": y}
        )
    )


def _confirm_point_from_source(page_source: str) -> tuple[int, int] | None:
    b = _bounds_for_text(page_source, "确定")
    if not b:
        return None
    return (b[0] + b[2]) // 2, (b[1] + b[3]) // 2


def _tap_confirm(gateway, confirm: tuple[int, int] | None) -> None:
    # WebView 内文案定位常失败；有坐标时优先坐标点击
    if confirm:
        _exec_tap_xy(gateway, confirm[0], confirm[1])
        return
    from execution_runtime.dsl.models import Step

    for loc in (
        {"type": "text", "value": "确定"},
        {"type": "accessibility_id", "value": "确定"},
    ):
        try:
            gateway.executor.execute(
                Step.model_validate({"action": "tap", "locator": loc})
            )
            return
        except Exception:
            continue


def _window_size(gateway) -> tuple[int, int]:
    try:
        size = gateway.driver.get_window_size()
        return int(size.get("width") or 0), int(size.get("height") or 0)
    except Exception:
        return 0, 0


def _screenshot_png(gateway) -> bytes:
    try:
        data = gateway.driver.get_screenshot_as_png()
        return data or b""
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"截图失败: {exc}") from exc


async def _ask_vision(
    png: bytes,
    *,
    img_w: int,
    img_h: int,
    model: str,
    gateway=None,
) -> dict[str, Any]:
    from src.llm.caller import llm_call
    from src.llm.types import LLMRequest

    if not os.environ.get("DASHSCOPE_API_KEY", "").strip():
        raise RuntimeError(
            "环境变量 DASHSCOPE_API_KEY 未设置，无法自动过安全检测"
        )

    stop = {"flag": False}

    def _keepalive() -> None:
        while not stop["flag"]:
            try:
                if gateway is not None:
                    gateway.driver.get_window_size()
            except Exception:
                pass
            for _ in range(20):
                if stop["flag"]:
                    return
                time.sleep(0.5)

    import threading

    keeper = threading.Thread(target=_keepalive, name="appium-keepalive", daemon=True)
    if gateway is not None:
        keeper.start()
    try:
        resp = await llm_call(
            LLMRequest(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=USER_PROMPT_TMPL.format(
                    img_w=img_w,
                    img_h=img_h,
                ),
                task_tag="execution.security_check",
                complexity="high",
                expect_json=True,
                max_tokens=4096,
                temperature=0.1,
                images=[png],
                model=model,
            ),
            max_retries=1,
        )
    finally:
        stop["flag"] = True

    parsed = resp.parsed_json
    if parsed is None:
        from src.llm.providers.deepseek import DeepSeekProvider

        parsed = DeepSeekProvider._extract_json(_repair_vision_json_text(resp.content))
    if parsed is None:
        raise RuntimeError(f"Vision JSON 解析失败: {resp.content[:300]}")
    return parsed  # type: ignore[return-value]


def _try_tap_refresh(gateway) -> bool:
    from execution_runtime.dsl.models import Step

    for loc in (
        {"type": "text", "value": "刷新"},
        {"type": "accessibility_id", "value": "刷新"},
    ):
        try:
            gateway.executor.execute(
                Step.model_validate({"action": "tap", "locator": loc})
            )
            time.sleep(1.5)
            return True
        except Exception:
            continue
    return False


def solve_security_check(
    gateway,
    testdata: ExecutionTestdata | None = None,
) -> list[str]:
    """出现安全检测时调用；成功返回 warnings，失败抛 RuntimeError。"""
    from execution_runtime.setup.precondition import (  # noqa: WPS433
        PreconditionSetupError,
        _is_logged_in,
        _is_security_check,
        _observe,
    )

    data = testdata or load_execution_testdata()
    model = data.login.security_check_model or "qwen3-vl-plus"
    max_attempts = max(1, int(data.login.security_check_max_attempts))
    warnings: list[str] = []
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        obs = _observe(gateway)
        if not _is_security_check(obs, data):
            warnings.append("安全检测已消失，跳过 Vision 求解")
            return warnings
        if _is_logged_in(obs, data):
            return warnings

        try:
            # 等大图加载完成，避免白屏送 Vision
            png = _wait_captcha_ready(gateway, timeout=18.0)
            img_w, img_h = _png_size(png)
            win_w, win_h = _window_size(gateway)
            obs_src = _observe(gateway)
            src = obs_src.source or ""
            tip = _bounds_for_text(src, "请在下图依次点击")
            confirm_b = _bounds_for_text(src, "确定")
            box = _modal_crop_box(src, img_w=img_w or win_w, img_h=img_h or win_h)

            # VL：整屏 + 0~1000 相对坐标；非 VL：裁剪图像素坐标
            use_vl = "vl" in str(model).lower()
            if use_vl:
                vision_png, vw, vh, ox, oy = png, img_w or win_w, img_h or win_h, 0, 0
            else:
                vision_png, vw, vh, ox, oy = _crop_png(png, box)

            parsed = asyncio.run(
                _ask_vision(
                    vision_png,
                    img_w=vw,
                    img_h=vh,
                    model=model,
                    gateway=gateway,
                )
            )
            clicks, confirm = _parse_plan(parsed)
            if use_vl:
                clicks, confirm = _scale_from_norm1000(
                    clicks, confirm, img_w=vw, img_h=vh
                )
                note_scale = "norm1000"
            else:
                clicks, confirm = _map_crop_to_full(
                    clicks,
                    confirm,
                    offset_x=ox,
                    offset_y=oy,
                    crop_w=vw,
                    crop_h=vh,
                )
                note_scale = "crop_px"
            src_confirm = _confirm_point_from_source(src)
            if src_confirm:
                confirm = src_confirm
            band_top = tip[1] if tip else box[1]
            band_bottom = confirm_b[3] if confirm_b else box[3]
            _validate_clicks(
                clicks,
                img_h=img_h or win_h,
                crop_top=band_top,
                crop_bottom=band_bottom,
            )
            note = (
                f"mode={note_scale} box={box} src_confirm={src_confirm} "
                f"vision={vw}x{vh}"
            )
            print(
                f"[security_check] attempt={attempt} clicks={clicks} confirm={confirm} "
                f"{note} img={img_w}x{img_h} win={win_w}x{win_h}",
                flush=True,
            )
            debug_png = vision_png if use_vl else vision_png
            try:
                if use_vl:
                    debug_png, _, _, _, _ = _crop_png(png, box)
            except Exception:
                debug_png = png
            _save_debug(
                attempt=attempt,
                crop_png=debug_png,
                parsed=parsed,
                clicks=clicks,
                confirm=confirm,
                note=note,
            )
            for x, y in clicks:
                sx, sy = _scale_point(
                    x,
                    y,
                    img_w=img_w or win_w,
                    img_h=img_h or win_h,
                    win_w=win_w,
                    win_h=win_h,
                )
                _exec_tap_xy(gateway, sx, sy)
                time.sleep(0.85)
            _tap_confirm(gateway, confirm)

            time.sleep(2.0)
            obs2 = _observe(gateway)
            if not _is_security_check(obs2, data) or _is_logged_in(obs2, data):
                warnings.append(
                    f"安全检测已由 Vision（{model}）自动通过（attempt={attempt}）"
                )
                return warnings
            last_err = RuntimeError("点击后仍停留在安全检测页")
            warnings.append(f"Vision 第 {attempt} 次点击后未通过，准备重试")
            _try_tap_refresh(gateway)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            warnings.append(f"Vision 第 {attempt} 次失败: {exc}")
            print(f"[security_check] attempt={attempt} ERROR: {exc}", flush=True)
            _try_tap_refresh(gateway)

    raise PreconditionSetupError(
        f"安全检测 Vision 求解失败（{max_attempts} 次）: {last_err}"
    )
