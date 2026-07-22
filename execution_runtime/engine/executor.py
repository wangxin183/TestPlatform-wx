"""DSL 步骤执行器：把 DSL Step 确定性映射为 Appium/XCUITest 操作。

执行阶段不调 LLM。定位失败/断言不通过抛 StepExecError，由上层决定是否自愈。
"""

from __future__ import annotations

import time
from typing import Any, Optional

from execution_runtime.config import RuntimeConfig
from execution_runtime.dsl.models import Locator, Step


class StepExecError(RuntimeError):
    """步骤执行失败（区分断言失败与执行异常由 kind 标记）。"""

    def __init__(self, message: str, kind: str = "broken") -> None:
        super().__init__(message)
        self.kind = kind  # "failed"（断言不通过）/ "broken"（定位/执行异常）


def _appium_by(locator_type: str, platform: str):
    from appium.webdriver.common.appiumby import AppiumBy

    if platform == "android":
        mapping = {
            "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
            "name": AppiumBy.ANDROID_UIAUTOMATOR,
            "id": AppiumBy.ID,
            "text": AppiumBy.ANDROID_UIAUTOMATOR,
            "uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
            "xpath": AppiumBy.XPATH,
            "class_chain": AppiumBy.XPATH,
        }
        return mapping.get(locator_type)

    mapping = {
        "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
        "name": AppiumBy.NAME,
        "predicate": AppiumBy.IOS_PREDICATE,
        "class_chain": AppiumBy.IOS_CLASS_CHAIN,
        "xpath": AppiumBy.XPATH,
    }
    return mapping.get(locator_type)


def _locator_query(locator: Locator, platform: str) -> str:
    """把 DSL locator 转为 Appium 查询值（Android uiautomator 需包装）。"""
    val = locator.value
    if platform != "android":
        return val
    if locator.type in ("name", "text"):
        if locator.type == "text":
            return f'new UiSelector().text("{val}")'
        return f'new UiSelector().textContains("{val}")'
    if locator.type == "uiautomator":
        return val if val.startswith("new UiSelector") else f'new UiSelector().{val}'
    if locator.type == "class_chain":
        return f'//android.widget.EditText[1]'
    return val


class StepExecutor:
    def __init__(self, driver, cfg: RuntimeConfig, ocr=None) -> None:
        self.driver = driver
        self.cfg = cfg
        self.ocr = ocr
        self.bundle_id = cfg.target_app.bundle_id
        self.platform = (cfg.target_app.platform or "ios").lower()
        self.default_timeout = cfg.run.step_timeout_seconds

    # ---- 定位 ----

    def find(self, locator: Locator, timeout: Optional[int] = None) -> tuple[Any, str]:
        """按 locator 查找元素，返回 (element, matched_by)。找不到抛 StepExecError。"""
        elements, matched_by = self.find_all(locator, timeout=timeout)
        return elements[0], matched_by

    def find_all(
        self,
        locator: Locator,
        timeout: Optional[int] = None,
    ) -> tuple[list[Any], str]:
        """按 locator 查找全部元素，供 Agent 工具模式校验目标唯一性。"""
        timeout = timeout or self.default_timeout
        if locator.type == "ocr_text":
            element, matched_by = self._find_by_ocr(locator.value, timeout)
            return [element], matched_by

        by = _appium_by(locator.type, self.platform)
        if by is None:
            raise StepExecError(f"不支持的定位类型: {locator.type}", kind="broken")

        query = _locator_query(locator, self.platform)
        deadline = time.time() + timeout
        last_exc: Exception | None = None
        while time.time() < deadline:
            try:
                els = self.driver.find_elements(by, query)
                if els:
                    return list(els), locator.type
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
            time.sleep(0.5)
        raise StepExecError(
            f"定位不到元素 [{locator.type}={locator.value}] (timeout={timeout}s)"
            + (f"; {last_exc}" if last_exc else ""),
            kind="broken",
        )

    def _find_by_ocr(self, text: str, timeout: int) -> tuple[Any, str]:
        if self.ocr is None:
            raise StepExecError("OCR 未启用，无法用 ocr_text 定位", kind="broken")
        # OCR 兜底命中返回坐标（P2 完整实现），当前占位为不支持点击元素句柄。
        raise StepExecError("OCR 定位当前仅支持文本断言，暂不支持点击（P2）", kind="broken")

    def _exists(self, locator: Locator, timeout: int) -> bool:
        try:
            self.find(locator, timeout=timeout)
            return True
        except StepExecError:
            return False

    # ---- 动作分发 ----

    def execute(self, step: Step) -> str:
        """执行一个步骤，返回实际命中的定位策略（matched_by），失败抛 StepExecError。"""
        action = step.action
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            raise StepExecError(f"未实现的动作: {action}", kind="broken")
        return handler(step) or ""

    def _do_launch_app(self, step: Step) -> str:
        self.driver.activate_app(self.bundle_id)
        return ""

    def _do_terminate_app(self, step: Step) -> str:
        self.driver.terminate_app(self.bundle_id)
        return ""

    def _do_tap(self, step: Step) -> str:
        el, matched = self.find(step.locator, step.timeout)
        el.click()
        return matched

    def _do_tap_xy(self, step: Step) -> str:
        x = int(step.x if step.x is not None else -1)
        y = int(step.y if step.y is not None else -1)
        if x < 0 or y < 0:
            raise StepExecError("tap_xy 需要非负 x/y", kind="broken")
        # Appium 通用：TouchAction / tap 列表；再退回 mobile: clickGesture / W3C
        try:
            self.driver.execute_script("mobile: clickGesture", {"x": x, "y": y})
            return "tap_xy:clickGesture"
        except Exception:
            pass
        try:
            self.driver.tap([(x, y)], 80)
            return "tap_xy:tap"
        except Exception:
            pass
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        from selenium.webdriver.common.actions.pointer_input import PointerInput

        finger = PointerInput(PointerInput.TOUCH, "finger")
        actions = ActionBuilder(self.driver, mouse=finger)
        actions.pointer_action.move_to_location(x, y)
        actions.pointer_action.pointer_down()
        actions.pointer_action.pause(0.08)
        actions.pointer_action.pointer_up()
        actions.perform()
        return "tap_xy:w3c"

    def _do_input(self, step: Step) -> str:
        el, matched = self.find(step.locator, step.timeout)
        el.click()
        el.send_keys(step.value or "")
        return matched

    def _do_clear(self, step: Step) -> str:
        el, matched = self.find(step.locator, step.timeout)
        el.clear()
        return matched

    def _do_back(self, step: Step) -> str:
        if self.platform == "android":
            self.driver.back()
            return ""
        try:
            self.driver.back()
        except Exception:
            self._edge_swipe_back()
        return ""

    def _do_wait(self, step: Step) -> str:
        timeout = step.timeout or self.default_timeout
        if step.until is not None:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self._exists(step.until, timeout=1):
                    return step.until.type
                time.sleep(0.5)
            raise StepExecError(
                f"等待元素超时 [{step.until.type}={step.until.value}]", kind="broken"
            )
        time.sleep(timeout)
        return ""

    def _do_swipe(self, step: Step) -> str:
        direction = step.direction or "up"
        times = max(1, int(step.times or 1))
        for _ in range(times):
            self._swipe_once(direction, step.ratio)
            if step.until is not None and self._exists(step.until, timeout=1):
                return step.until.type
            time.sleep(0.4)
        if step.until is not None and not self._exists(step.until, timeout=1):
            raise StepExecError(
                f"滑动 {times} 次仍未出现 [{step.until.type}={step.until.value}]",
                kind="failed",
            )
        return ""

    def _do_scroll(self, step: Step) -> str:
        return self._do_swipe(step)

    def _do_assert_visible(self, step: Step) -> str:
        el, matched = self.find(step.locator, step.timeout)
        if not el.is_displayed():
            raise StepExecError(
                f"元素存在但不可见 [{step.locator.type}={step.locator.value}]",
                kind="failed",
            )
        return matched

    def _do_assert_text(self, step: Step) -> str:
        expected = step.value or ""
        # 有 locator 时校验该元素文本；否则全屏文本匹配
        if step.locator is not None:
            el, matched = self.find(step.locator, step.timeout)
            actual = (
                el.text
                or el.get_attribute("text")
                or el.get_attribute("content-desc")
                or el.get_attribute("value")
                or el.get_attribute("label")
                or ""
            )
            if expected not in actual:
                raise StepExecError(
                    f"文本断言失败: 期望包含「{expected}」，实际「{actual}」", kind="failed"
                )
            return matched
        # 全屏：page_source 里找
        deadline = time.time() + (step.timeout or self.default_timeout)
        while time.time() < deadline:
            src = self._safe_source()
            if expected in src:
                return "page_source"
            time.sleep(0.5)
        raise StepExecError(
            f"文本断言失败: 全屏未找到「{expected}」", kind="failed"
        )

    def _do_assert_text_absent(self, step: Step) -> str:
        forbidden = step.value or ""
        if step.locator is not None:
            el, matched = self.find(step.locator, step.timeout)
            actual = (
                el.text
                or el.get_attribute("text")
                or el.get_attribute("content-desc")
                or el.get_attribute("value")
                or el.get_attribute("label")
                or ""
            )
            if forbidden in actual:
                raise StepExecError(
                    f"负向文本断言失败: 不应出现「{forbidden}」，实际「{actual}」",
                    kind="failed",
                )
            return matched
        src = self._safe_source()
        if forbidden in src:
            raise StepExecError(
                f"负向文本断言失败: 全屏仍可见「{forbidden}」",
                kind="failed",
            )
        return "page_source_absent"

    def _do_screenshot(self, step: Step) -> str:
        # 截图由 recorder 统一采集，这里仅占位（留证步骤）
        return ""

    # ---- 手势底层 ----

    def _swipe_once(self, direction: str, ratio: float) -> None:
        size = self.driver.get_window_size()
        w, h = size["width"], size["height"]
        cx, cy = w // 2, h // 2
        dy = int(h * ratio / 2)
        dx = int(w * ratio / 2)
        if direction == "up":
            start, end = (cx, cy + dy), (cx, cy - dy)
        elif direction == "down":
            start, end = (cx, cy - dy), (cx, cy + dy)
        elif direction == "left":
            start, end = (cx + dx, cy), (cx - dx, cy)
        else:  # right
            start, end = (cx - dx, cy), (cx + dx, cy)
        self.driver.swipe(start[0], start[1], end[0], end[1], 400)

    def _edge_swipe_back(self) -> None:
        size = self.driver.get_window_size()
        h = size["height"]
        self.driver.swipe(2, h // 2, int(size["width"] * 0.6), h // 2, 300)

    def _safe_source(self) -> str:
        try:
            return self.driver.page_source or ""
        except Exception:
            return ""
