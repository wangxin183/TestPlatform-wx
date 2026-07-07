"""Executor type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VisualAssertion:
    """A visual assertion to run after a step action (screenshot + OCR/compare)."""
    assertion_type: str               # "text_visible", "element_visible", "layout_match", "no_error"
    expected_text: str | None = None  # Text to find via OCR
    baseline_path: str | None = None  # Baseline screenshot for layout comparison
    threshold: float = 0.95           # Similarity threshold for layout_match


@dataclass
class VisualAssertionResult:
    """Result of a visual assertion."""
    passed: bool
    assertion_type: str = ""
    actual_text: str | None = None
    similarity: float | None = None
    diff_image_path: str | None = None
    ocr_confidence: float | None = None
    error_message: str | None = None


@dataclass
class StepAction:
    step_number: int
    action_type: str           # click/input/navigate/assert/wait/scroll/api_call/screenshot
    target: str | None = None  # CSS selector, XPath, element ID, API endpoint
    value: str | None = None   # input text, assertion expected value, request body
    timeout_ms: int = 30000
    visual_assertions: list[VisualAssertion] | None = None  # Post-step visual checks


@dataclass
class StepResult:
    step_number: int
    status: str               # passed/failed/error/skipped/generated
    duration_ms: float = 0.0
    action: StepAction | None = None
    actual_result: str | None = None
    error_message: str | None = None
    screenshot_path: str | None = None
    browser_config: str | None = None  # e.g. "Chrome/Desktop" for matrix results
    visual_results: list[VisualAssertionResult] | None = None


@dataclass
class ExecutorConfig:
    platform_type: str
    target_url: str | None = None          # Web/MiniProgram URL
    app_path: str | None = None           # .app / .apk path for mobile
    device_config: dict | None = None     # {platformVersion, deviceName, udid, ...}
    capabilities: dict | None = None      # Appium desired capabilities
    api_base_url: str | None = None       # API testing base URL
    auth_headers: dict | None = None
    timeout_seconds: int = 60
    max_retries: int = 3
