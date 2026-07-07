"""iOS and Android executors — Appium-based mobile automation."""

from __future__ import annotations

import time
from pathlib import Path

from src.core.config import settings
from src.executor.base import AbstractExecutor
from src.executor.registry import ExecutorRegistry
from src.executor.types import ExecutorConfig, StepAction, StepResult
from src.utils.logging_config import get_logger

logger = get_logger(__name__)

SCREENSHOT_DIR = Path(settings.storage.screenshots_dir)


class AppiumBaseExecutor(AbstractExecutor):
    """Shared base for iOS and Android executors using Appium."""

    platform_type = "mobile"

    def __init__(self):
        self._driver = None
        self._config: ExecutorConfig | None = None

    async def setup(self, config: ExecutorConfig) -> None:
        self._config = config
        try:
            from appium.webdriver.appium_service import AppiumService
            from appium import webdriver
        except ImportError:
            raise RuntimeError(
                "Appium not installed. Run: pip install appium-python-client"
            )

        capabilities = config.capabilities or {}
        appium_url = capabilities.pop("appium_url", "http://localhost:4723/wd/hub")

        # Appium WebDriver is synchronous, so we run it in a thread
        import asyncio
        loop = asyncio.get_event_loop()
        self._driver = await loop.run_in_executor(
            None, lambda: webdriver.Remote(appium_url, capabilities)
        )
        logger.info("appium_setup_complete", platform=self.platform_type)

    async def execute_step(self, action: StepAction) -> StepResult:
        if not self._driver:
            return StepResult(
                step_number=action.step_number,
                status="error",
                error_message="Appium driver not initialized",
            )

        import asyncio
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()

        try:
            driver = self._driver
            if action.action_type == "click":
                el = await loop.run_in_executor(
                    None, lambda: driver.find_element("xpath", action.target)
                )
                await loop.run_in_executor(None, el.click)
                actual = f"Clicked {action.target}"

            elif action.action_type == "input":
                el = await loop.run_in_executor(
                    None, lambda: driver.find_element("xpath", action.target)
                )
                await loop.run_in_executor(None, lambda: el.send_keys(action.value or ""))
                actual = f"Input '{action.value}' into {action.target}"

            elif action.action_type == "assert":
                el = await loop.run_in_executor(
                    None, lambda: driver.find_element("xpath", action.target)
                )
                text = await loop.run_in_executor(None, lambda: el.text)
                if action.value and action.value not in text:
                    raise AssertionError(f"Expected '{action.value}' in '{text}'")
                actual = f"Element {action.target} found with text: {text}"

            elif action.action_type == "swipe":
                # action.value = "up/down/left/right" or "x1,y1,x2,y2"
                size = await loop.run_in_executor(None, driver.get_window_size)
                w, h = size["width"], size["height"]
                if action.value == "up":
                    driver.swipe(w // 2, int(h * 0.8), w // 2, int(h * 0.2), 500)
                elif action.value == "down":
                    driver.swipe(w // 2, int(h * 0.2), w // 2, int(h * 0.8), 500)
                actual = f"Swiped {action.value}"

            elif action.action_type == "wait":
                await asyncio.sleep((action.timeout_ms or 3000) / 1000)
                actual = f"Waited {action.timeout_ms}ms"

            elif action.action_type == "screenshot":
                path = await loop.run_in_executor(None, self.screenshot_sync)
                actual = f"Screenshot saved to {path}"

            else:
                actual = f"Unknown action: {action.action_type}"

            return StepResult(
                step_number=action.step_number,
                status="passed",
                duration_ms=(time.monotonic() - t0) * 1000,
                actual_result=actual,
            )

        except Exception as exc:
            return StepResult(
                step_number=action.step_number,
                status="failed",
                duration_ms=(time.monotonic() - t0) * 1000,
                error_message=str(exc),
            )

    async def execute_steps(self, actions: list[StepAction]) -> list[StepResult]:
        results = []
        for action in actions:
            result = await self.execute_step(action)
            results.append(result)
        return results

    def screenshot_sync(self) -> str:
        """Synchronous screenshot (called in executor thread)."""
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"appium_{int(time.time() * 1000)}.png"
        path = SCREENSHOT_DIR / filename
        self._driver.save_screenshot(str(path))
        return str(path)

    async def screenshot(self) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.screenshot_sync)

    async def teardown(self) -> None:
        if self._driver:
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._driver.quit)
        logger.info("appium_teardown_complete")

    async def health_check(self) -> dict:
        try:
            from appium import webdriver
            return {"connected": True, "details": "Appium installed"}
        except ImportError:
            return {"connected": False, "details": "Appium not installed"}


class IOSExecutor(AppiumBaseExecutor):
    platform_type = "ios"


class AndroidExecutor(AppiumBaseExecutor):
    platform_type = "android"


ExecutorRegistry.register("ios", IOSExecutor)
ExecutorRegistry.register("android", AndroidExecutor)
