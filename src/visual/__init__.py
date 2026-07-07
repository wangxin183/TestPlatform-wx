"""Visual testing package — OCR-based text recognition + screenshot comparison.

Provides visual assertion capabilities for UI test executors (Playwright, Appium).
Gracefully degrades if Tesseract OCR is not installed.
"""

from src.visual.ocr import OCRReader
from src.visual.comparator import ScreenshotComparator
from src.visual.asserter import VisualAsserter

__all__ = ["OCRReader", "ScreenshotComparator", "VisualAsserter"]
