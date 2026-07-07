"""Browser matrix executor — cross-browser/viewport compatibility testing.

Loops each test case through multiple browser + viewport combinations
and records per-config results.
"""

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

BROWSER_MATRIX = [
    {"browser": "chromium", "viewport": {"width": 1920, "height": 1080}, "label": "Chrome/Desktop"},
    {"browser": "chromium", "viewport": {"width": 375, "height": 812},  "label": "Chrome/Mobile"},
    {"browser": "firefox",  "viewport": {"width": 1920, "height": 1080}, "label": "Firefox/Desktop"},
    {"browser": "webkit",   "viewport": {"width": 375, "height": 812},  "label": "Safari/Mobile"},
]


class BrowserMatrixExecutor(AbstractExecutor):
    """Cross-browser / cross-viewport compatibility executor.

    Creates a fresh browser context for each matrix configuration and
    replays the same steps across all configs.
    """

    platform_type = "compatibility"

    def __init__(self):
        self._pw = None
        self._browser = None
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
        logger.info("browser_matrix_setup_complete")

    async def execute_step(self, action: StepAction) -> StepResult:
        return StepResult(
            step_number=action.step_number,
            status="error",
            error_message="Use execute_steps() for matrix execution",
        )

    async def execute_steps(self, actions: list[StepAction]) -> list[StepResult]:
        results: list[StepResult] = []

        for config in BROWSER_MATRIX:
            context = None
            page = None
            try:
                browser_type = config["browser"]
                if browser_type == "chromium":
                    browser = await self._pw.chromium.launch(headless=True)
                elif browser_type == "firefox":
                    browser = await self._pw.firefox.launch(headless=True)
                else:
                    browser = await self._pw.webkit.launch(headless=True)

                context = await browser.new_context(viewport=config["viewport"])
                page = await context.new_page()

                for action in actions:
                    t0 = time.monotonic()
                    try:
                        actual = await self._run_action(page, action)
                        results.append(StepResult(
                            step_number=action.step_number,
                            status="passed",
                            duration_ms=(time.monotonic() - t0) * 1000,
                            actual_result=actual,
                            browser_config=config["label"],
                        ))
                    except Exception as exc:
                        screenshot_path = None
                        try:
                            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
                            filename = f"compat_{int(time.time() * 1000)}.png"
                            spath = SCREENSHOT_DIR / filename
                            await page.screenshot(path=str(spath))
                            screenshot_path = str(spath)
                        except Exception:
                            pass
                        results.append(StepResult(
                            step_number=action.step_number,
                            status="failed",
                            duration_ms=(time.monotonic() - t0) * 1000,
                            error_message=str(exc),
                            screenshot_path=screenshot_path,
                            browser_config=config["label"],
                        ))
            except Exception as exc:
                results.append(StepResult(
                    step_number=0,
                    status="error",
                    error_message=f"Browser init failed for {config['label']}: {exc}",
                    browser_config=config["label"],
                ))
            finally:
                if context:
                    await context.close()
                if browser:
                    await browser.close()

        return results

    async def _run_action(self, page, action: StepAction) -> str:
        if action.action_type == "navigate":
            await page.goto(action.target, timeout=action.timeout_ms)
            return f"Navigated to {action.target}"

        elif action.action_type == "click":
            await page.click(action.target, timeout=action.timeout_ms)
            return f"Clicked {action.target}"

        elif action.action_type == "input":
            await page.fill(action.target, action.value or "", timeout=action.timeout_ms)
            return f"Input '{action.value}' into {action.target}"

        elif action.action_type == "assert":
            await page.wait_for_selector(action.target, timeout=action.timeout_ms)
            text = await page.text_content(action.target)
            if action.value and action.value != text:
                raise AssertionError(f"Expected '{action.value}', got '{text}'")
            return f"Element {action.target} found with text: {text}"

        elif action.action_type == "wait":
            await page.wait_for_timeout(action.timeout_ms or 3000)
            return f"Waited {action.timeout_ms}ms"

        elif action.action_type == "scroll":
            await page.evaluate(f"window.scrollBy(0, {action.value or 500})")
            return f"Scrolled by {action.value or 500}px"

        elif action.action_type == "screenshot":
            SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            filename = f"compat_screenshot_{int(time.time() * 1000)}.png"
            spath = SCREENSHOT_DIR / filename
            await page.screenshot(path=str(spath))
            return f"Screenshot saved to {spath}"

        return f"Unknown action type: {action.action_type}"

    async def screenshot(self) -> str:
        return ""

    async def teardown(self) -> None:
        if hasattr(self, "_pw") and self._pw:
            await self._pw.stop()
        logger.info("browser_matrix_teardown_complete")

    async def health_check(self) -> dict:
        try:
            from playwright.async_api import async_playwright
            return {"connected": True, "details": "Playwright installed"}
        except ImportError:
            return {"connected": False, "details": "Playwright not installed"}


ExecutorRegistry.register("compatibility", BrowserMatrixExecutor)
