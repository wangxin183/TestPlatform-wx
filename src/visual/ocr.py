"""OCR text extraction from screenshots using Tesseract.

Gracefully degrades: returns skip results if pytesseract or tesseract is not installed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ── Lazy import — pytesseract is optional ──
_TESSERACT_AVAILABLE = False
try:
    import pytesseract
    from PIL import Image
    _TESSERACT_AVAILABLE = True
except ImportError:
    pass


@dataclass
class OCRMatch:
    """Result of a text search within a screenshot."""
    text: str
    confidence: float       # 0.0 – 1.0, Tesseract confidence
    bbox: tuple[int, int, int, int] | None = None  # (x, y, w, h)
    found: bool = False


@dataclass
class OCRResult:
    """Full OCR extraction result for a screenshot."""
    full_text: str
    language: str
    matches: list[OCRMatch] = None

    def __post_init__(self):
        if self.matches is None:
            self.matches = []


class OCRReader:
    """Extract text from screenshots and search for expected content.

    Usage::

        reader = OCRReader(lang="chi_sim+eng")
        text = await reader.extract_text("screenshot.png")
        match = await reader.find_text("screenshot.png", "登录成功")
    """

    def __init__(self, lang: str = "chi_sim+eng", tesseract_cmd: str = "tesseract"):
        self._lang = lang
        self._available = _TESSERACT_AVAILABLE
        if self._available:
            try:
                pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
                # Quick health check
                pytesseract.get_tesseract_version()
            except Exception:
                self._available = False
                logger.warning(
                    "ocr_tesseract_unavailable",
                    message="Tesseract OCR 未安装或不在 PATH 中，视觉断言将跳过",
                )

    @property
    def is_available(self) -> bool:
        return self._available

    async def extract_text(self, image_path: str, lang: str | None = None) -> OCRResult:
        """Extract all recognized text from a screenshot."""
        if not self._available:
            return OCRResult(
                full_text="",
                language=lang or self._lang,
                matches=[],
            )

        path = Path(image_path)
        if not path.exists():
            logger.error("ocr_image_not_found", path=image_path)
            return OCRResult(full_text="", language=lang or self._lang)

        try:
            img = Image.open(image_path)
            text = await asyncio.to_thread(
                pytesseract.image_to_string,
                img,
                lang=lang or self._lang,
            )
            return OCRResult(
                full_text=text.strip(),
                language=lang or self._lang,
            )
        except Exception as exc:
            logger.error("ocr_extract_failed", path=image_path, error=str(exc))
            return OCRResult(full_text="", language=lang or self._lang)

    async def find_text(
        self,
        image_path: str,
        expected_text: str,
        lang: str | None = None,
    ) -> OCRMatch:
        """Search for specific text in a screenshot. Returns OCRMatch with found=True if matched."""
        if not self._available:
            return OCRMatch(text="", confidence=0.0, found=False)

        path = Path(image_path)
        if not path.exists():
            return OCRMatch(text="", confidence=0.0, found=False)

        try:
            img = Image.open(image_path)
            data = await asyncio.to_thread(
                pytesseract.image_to_data,
                img,
                lang=lang or self._lang,
                output_type=pytesseract.Output.DICT,
            )

            # Search for expected_text in recognized text
            for i, word in enumerate(data.get("text", [])):
                word_clean = word.strip()
                if not word_clean:
                    continue
                # Substring match (case-insensitive for ASCII, exact for CJK)
                if expected_text.lower() in word_clean.lower() or expected_text in word_clean:
                    conf = int(data["conf"][i]) / 100.0 if data["conf"][i] != "-1" else 0.5
                    x = int(data["left"][i])
                    y = int(data["top"][i])
                    w = int(data["width"][i])
                    h = int(data["height"][i])
                    return OCRMatch(
                        text=word_clean,
                        confidence=conf,
                        bbox=(x, y, w, h),
                        found=True,
                    )

            # Fallback: search in full recognized text
            full_text = await asyncio.to_thread(
                pytesseract.image_to_string,
                img,
                lang=lang or self._lang,
            )
            if expected_text.lower() in full_text.lower():
                return OCRMatch(
                    text=expected_text,
                    confidence=0.4,
                    found=True,
                )

            return OCRMatch(text="", confidence=0.0, found=False)

        except Exception as exc:
            logger.error("ocr_find_text_failed", path=image_path, error=str(exc))
            return OCRMatch(text="", confidence=0.0, found=False)

    async def extract_region(
        self,
        image_path: str,
        region: tuple[int, int, int, int],
        lang: str | None = None,
    ) -> OCRResult:
        """OCR on a cropped region of the screenshot. Region: (x, y, width, height)."""
        if not self._available:
            return OCRResult(full_text="", language=lang or self._lang)

        path = Path(image_path)
        if not path.exists():
            return OCRResult(full_text="", language=lang or self._lang)

        try:
            img = Image.open(image_path)
            x, y, w, h = region
            cropped = img.crop((x, y, x + w, y + h))
            text = await asyncio.to_thread(
                pytesseract.image_to_string,
                cropped,
                lang=lang or self._lang,
            )
            return OCRResult(full_text=text.strip(), language=lang or self._lang)
        except Exception as exc:
            logger.error("ocr_region_failed", path=image_path, error=str(exc))
            return OCRResult(full_text="", language=lang or self._lang)
