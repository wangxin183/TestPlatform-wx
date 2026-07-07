"""High-level visual assertions for UI test executors.

Integrates OCRReader and ScreenshotComparator into assertion methods
that PlaywrightExecutor and AppiumExecutor can call during test execution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from src.visual.ocr import OCRMatch, OCRReader, OCRResult
from src.visual.comparator import CompareResult, ScreenshotComparator
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class VisualAssertionResult:
    """Result of a single visual assertion."""
    passed: bool
    assertion_type: str            # text_visible / element_visible / layout_match / no_error
    expected_text: str | None = None
    actual_text: str | None = None
    similarity: float | None = None
    diff_image_path: str | None = None
    ocr_confidence: float | None = None
    error_message: str | None = None


class VisualAsserter:
    """High-level visual assertion methods for UI test executors.

    Usage::

        asserter = VisualAsserter()
        result = await asserter.assert_text_visible("screenshot.png", "登录成功")
        if not result.passed:
            print(f"Text not found: {result.error_message}")
    """

    def __init__(
        self,
        ocr_lang: str = "chi_sim+eng",
        comparison_threshold: float = 0.95,
        diff_dir: str = "storage/screenshots/diffs",
    ):
        self._ocr = OCRReader(lang=ocr_lang)
        self._comparator = ScreenshotComparator(
            threshold=comparison_threshold,
            diff_dir=diff_dir,
        )
        self._ocr_lang = ocr_lang
        self._comparison_threshold = comparison_threshold

    @property
    def ocr_available(self) -> bool:
        return self._ocr.is_available

    # ═══════════════════════════════════════════════════════════════
    # Text assertions
    # ═══════════════════════════════════════════════════════════════

    async def assert_text_visible(
        self,
        screenshot_path: str,
        expected_text: str,
    ) -> VisualAssertionResult:
        """Verify that expected_text is visible in the screenshot via OCR."""
        if not self._ocr.is_available:
            return VisualAssertionResult(
                passed=True,
                assertion_type="text_visible",
                expected_text=expected_text,
                error_message="OCR 未安装（pytesseract），跳过视觉断言",
            )

        match: OCRMatch = await self._ocr.find_text(screenshot_path, expected_text)

        if match.found:
            return VisualAssertionResult(
                passed=True,
                assertion_type="text_visible",
                expected_text=expected_text,
                actual_text=match.text,
                ocr_confidence=match.confidence,
            )
        else:
            # Try full text extraction for debugging
            result: OCRResult = await self._ocr.extract_text(screenshot_path)
            return VisualAssertionResult(
                passed=False,
                assertion_type="text_visible",
                expected_text=expected_text,
                actual_text=result.full_text[:500] if result.full_text else "(无文字识别结果)",
                ocr_confidence=0.0,
                error_message=f"未在截图中找到文字「{expected_text}」",
            )

    async def assert_element_visible(
        self,
        screenshot_path: str,
        element_description: str,
    ) -> VisualAssertionResult:
        """Verify that a described element is visible in the screenshot via OCR.

        This is a heuristic: it OCRs the full screenshot and checks if any
        recognized text matches the element description keywords.
        """
        if not self._ocr.is_available:
            return VisualAssertionResult(
                passed=True,
                assertion_type="element_visible",
                expected_text=element_description,
                error_message="OCR 未安装，跳过视觉断言",
            )

        result: OCRResult = await self._ocr.extract_text(screenshot_path)
        full_text = result.full_text.lower()
        element_lower = element_description.lower()

        # Check for partial keyword match
        keywords = element_lower.split()
        matched = sum(1 for kw in keywords if kw in full_text)
        passed = matched >= max(1, len(keywords) // 2)

        return VisualAssertionResult(
            passed=passed,
            assertion_type="element_visible",
            expected_text=element_description,
            actual_text=full_text[:300] if full_text else "(无文字识别结果)",
            error_message=(
                None if passed
                else f"未在截图中找到元素「{element_description}」的匹配文字"
            ),
        )

    # ═══════════════════════════════════════════════════════════════
    # Layout assertions
    # ═══════════════════════════════════════════════════════════════

    async def assert_layout_matches(
        self,
        current_path: str,
        baseline_path: str,
        threshold: float | None = None,
    ) -> VisualAssertionResult:
        """Compare current screenshot to a known-good baseline."""
        result: CompareResult = await self._comparator.compare(
            baseline_path, current_path, threshold
        )

        return VisualAssertionResult(
            passed=result.match,
            assertion_type="layout_match",
            similarity=result.similarity,
            diff_image_path=result.diff_path,
            error_message=result.error_message or (
                None if result.match
                else f"截图相似度 {result.similarity:.1%} 低于阈值"
            ),
        )

    async def assert_no_error_dialog(self, screenshot_path: str) -> VisualAssertionResult:
        """Check that no error/alert dialog is visible in the screenshot.

        Searches for common Chinese/English error patterns via OCR.
        """
        if not self._ocr.is_available:
            return VisualAssertionResult(
                passed=True,
                assertion_type="no_error",
                error_message="OCR 未安装，跳过视觉断言",
            )

        result: OCRResult = await self._ocr.extract_text(screenshot_path)
        full_text = result.full_text

        error_patterns = [
            "错误", "异常", "失败", "error", "Error",
            "弹窗", "提示", "警告", "warning", "Warning",
            "无法", "不能", "不允许",
        ]

        found_patterns = [p for p in error_patterns if p in full_text]

        if found_patterns:
            return VisualAssertionResult(
                passed=False,
                assertion_type="no_error",
                actual_text=full_text[:500],
                error_message=f"截图中发现疑似错误提示: {', '.join(found_patterns)}",
            )

        return VisualAssertionResult(
            passed=True,
            assertion_type="no_error",
            actual_text=full_text[:200] if full_text else "",
        )
