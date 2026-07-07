"""Web/H5 executor — Playwright-based browser automation."""

from __future__ import annotations

import os
import time
from pathlib import Path

from src.core.config import settings
from src.executor.base import AbstractExecutor
from src.executor.registry import ExecutorRegistry
from src.executor.types import ExecutorConfig, StepAction, StepResult
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

SCREENSHOT_DIR = Path(settings.storage.screenshots_dir)


class PlaywrightExecutor(AbstractExecutor):
    platform_type = "web"

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._config: ExecutorConfig | None = None

    async def setup(self, config: ExecutorConfig) -> None:
        self._config = config
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._pw = await async_playwright().start()

        browser_type = config.capabilities.get("browser", "chromium") if config.capabilities else "chromium"
        headless = config.capabilities.get("headless", True) if config.capabilities else True

        launch_options = {"headless": headless}
        if browser_type == "chromium":
            self._browser = await self._pw.chromium.launch(**launch_options)
        elif browser_type == "firefox":
            self._browser = await self._pw.firefox.launch(**launch_options)
        elif browser_type == "webkit":
            self._browser = await self._pw.webkit.launch(**launch_options)

        viewport = {"width": 1920, "height": 1080}
        if config.capabilities and "viewport_width" in config.capabilities:
            viewport = {
                "width": config.capabilities.get("viewport_width", 1920),
                "height": config.capabilities.get("viewport_height", 1080),
            }

        is_mobile = config.capabilities.get("is_mobile", False) if config.capabilities else False
        if is_mobile:
            viewport = {"width": 375, "height": 812}

        self._context = await self._browser.new_context(viewport=viewport)
        self._page = await self._context.new_page()
        logger.info("playwright_setup_complete", platform=self.platform_type)

    async def execute_step(self, action: StepAction) -> StepResult:
        if not self._page:
            return StepResult(
                step_number=action.step_number,
                status="error",
                error_message="Browser not initialized",
            )

        t0 = time.monotonic()
        try:
            if action.action_type == "navigate":
                await self._page.goto(action.target, timeout=action.timeout_ms)
                actual = f"Navigated to {action.target}"

            elif action.action_type == "click":
                await self._page.click(action.target, timeout=action.timeout_ms)
                actual = f"Clicked {action.target}"

            elif action.action_type == "input":
                await self._page.fill(action.target, action.value or "", timeout=action.timeout_ms)
                actual = f"Input '{action.value}' into {action.target}"

            elif action.action_type == "assert":
                await self._page.wait_for_selector(action.target, timeout=action.timeout_ms)
                text = await self._page.text_content(action.target)
                if action.value and action.value != text:
                    raise AssertionError(f"Expected '{action.value}', got '{text}'")
                actual = f"Element {action.target} found with text: {text}"

            elif action.action_type == "wait":
                await self._page.wait_for_timeout(action.timeout_ms or 3000)
                actual = f"Waited {action.timeout_ms}ms"

            elif action.action_type == "scroll":
                await self._page.evaluate(f"window.scrollBy(0, {action.value or 500})")
                actual = f"Scrolled by {action.value or 500}px"

            elif action.action_type == "screenshot":
                path = await self.screenshot()
                actual = f"Screenshot saved to {path}"

            else:
                actual = f"Unknown action type: {action.action_type}"

            return StepResult(
                step_number=action.step_number,
                status="passed",
                duration_ms=(time.monotonic() - t0) * 1000,
                actual_result=actual,
            )

        except Exception as exc:
            screenshot_path = None
            try:
                screenshot_path = await self.screenshot()
            except Exception:
                pass

            return StepResult(
                step_number=action.step_number,
                status="failed",
                duration_ms=(time.monotonic() - t0) * 1000,
                error_message=str(exc),
                screenshot_path=screenshot_path,
            )

    async def execute_steps(self, actions: list[StepAction]) -> list[StepResult]:
        results = []
        for action in actions:
            result = await self.execute_step(action)
            results.append(result)
        return results

    async def screenshot(self) -> str:
        if not self._page:
            return ""
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"screenshot_{int(time.time() * 1000)}.png"
        path = SCREENSHOT_DIR / filename
        await self._page.screenshot(path=str(path))
        return str(path)

    async def teardown(self) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if hasattr(self, "_pw"):
            await self._pw.stop()
        logger.info("playwright_teardown_complete")

    async def health_check(self) -> dict:
        try:
            from playwright.async_api import async_playwright
            return {"connected": True, "details": "Playwright installed"}
        except ImportError:
            return {"connected": False, "details": "Playwright not installed"}


# Register
ExecutorRegistry.register("web", PlaywrightExecutor)
ExecutorRegistry.register("h5", PlaywrightExecutor)
