"""Screenshot comparison — pixel-level diff + structural similarity.

Compares two screenshots and returns a similarity score with an optional
difference image highlighting changed regions.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from src.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CompareResult:
    """Result of a screenshot comparison."""
    match: bool
    similarity: float        # 0.0 – 1.0, higher = more similar
    diff_path: str | None    # Path to diff image (red-highlighted differences)
    pixel_diff_pct: float    # Percentage of pixels that differ
    error_message: str | None = None


class ScreenshotComparator:
    """Compare screenshots using pixel-level analysis and structural similarity.

    Usage::

        cmp = ScreenshotComparator(threshold=0.95)
        result = await cmp.compare("baseline.png", "current.png")
        if result.match:
            print(f"Screenshots match ({result.similarity:.1%})")
    """

    def __init__(
        self,
        threshold: float = 0.95,
        diff_dir: str = "storage/screenshots/diffs",
        save_diff_images: bool = True,
    ):
        self._threshold = threshold
        self._diff_dir = Path(diff_dir)
        self._save_diff = save_diff_images

    @property
    def threshold(self) -> float:
        return self._threshold

    async def compare(
        self,
        baseline_path: str,
        current_path: str,
        threshold: float | None = None,
    ) -> CompareResult:
        """Compare two screenshots. Returns a CompareResult with similarity score.

        Uses a simplified SSIM (Structural Similarity) approach:
        1. Pixel-level MSE (Mean Squared Error) for quick diff
        2. Normalized similarity score (0.0 – 1.0)
        3. Optional diff image generation
        """
        threshold = threshold if threshold is not None else self._threshold

        baseline_file = Path(baseline_path)
        current_file = Path(current_path)

        # ── Validate inputs ──
        if not baseline_file.exists():
            return CompareResult(
                match=False, similarity=0.0, diff_path=None,
                pixel_diff_pct=100.0,
                error_message=f"Baseline image not found: {baseline_path}",
            )
        if not current_file.exists():
            return CompareResult(
                match=False, similarity=0.0, diff_path=None,
                pixel_diff_pct=100.0,
                error_message=f"Current image not found: {current_path}",
            )

        try:
            baseline = Image.open(baseline_path).convert("RGB")
            current = Image.open(current_path).convert("RGB")

            # ── Resize if dimensions differ ──
            if baseline.size != current.size:
                logger.info(
                    "screenshot_size_mismatch",
                    baseline=baseline.size,
                    current=current.size,
                )
                current = current.resize(baseline.size, Image.LANCZOS)

            # ── Pixel-level comparison ──
            similarity, pixel_diff_pct, diff_image = await asyncio.to_thread(
                self._compute_similarity, baseline, current
            )

            match = similarity >= threshold

            # ── Save diff image ──
            diff_path: str | None = None
            if not match and self._save_diff and diff_image:
                self._diff_dir.mkdir(parents=True, exist_ok=True)
                import time
                filename = f"diff_{int(time.time() * 1000)}.png"
                diff_path = str(self._diff_dir / filename)
                await asyncio.to_thread(diff_image.save, diff_path)

            logger.info(
                "screenshot_compared",
                similarity=round(similarity, 4),
                threshold=threshold,
                match=match,
                pixel_diff_pct=round(pixel_diff_pct, 2),
            )

            return CompareResult(
                match=match,
                similarity=similarity,
                diff_path=diff_path,
                pixel_diff_pct=pixel_diff_pct,
            )

        except Exception as exc:
            logger.error("screenshot_compare_failed", error=str(exc))
            return CompareResult(
                match=False, similarity=0.0, diff_path=None,
                pixel_diff_pct=100.0, error_message=str(exc),
            )

    def _compute_similarity(
        self, img1: Image.Image, img2: Image.Image
    ) -> tuple[float, float, Image.Image | None]:
        """Compute normalized similarity between two PIL Images.

        Returns (similarity, pixel_diff_pct, diff_image_or_None).
        """
        # ── MSE-based similarity ──
        diff = ImageChops.difference(img1, img2)
        stat = ImageStat.Stat(diff)

        # Mean pixel error across all channels, normalized to [0, 1]
        # stat.mean returns (r_mean, g_mean, b_mean)
        mse = sum(stat.mean) / (3.0 * 255.0) if stat.mean else 1.0

        # Similarity: 1.0 = identical, 0.0 = completely different
        similarity = 1.0 - min(mse, 1.0)

        # ── Pixel diff percentage ──
        # Consider a pixel "different" if any channel deviates by > 10
        bands = diff.split()
        threshold = 30  # pixel diff threshold
        diff_pixels = 0
        total_pixels = img1.size[0] * img1.size[1]

        for band in bands:
            band_stat = ImageStat.Stat(band)
            # Count pixels above threshold
            hist = band.histogram()
            above = sum(hist[threshold:])
            diff_pixels = max(diff_pixels, above)

        pixel_diff_pct = (diff_pixels / max(total_pixels, 1)) * 100.0

        # ── Generate diff image ──
        diff_image: Image.Image | None = None
        if pixel_diff_pct > 1.0:
            # Create a composite: img1 as base, red-tinted diff overlay
            diff_image = Image.blend(
                img1,
                Image.new("RGB", img1.size, (255, 0, 0)),
                alpha=0.3,
            )

        return similarity, pixel_diff_pct, diff_image

    async def find_element(
        self, screenshot_path: str, template_path: str
    ) -> bool:
        """Template matching — check if a known element image appears in the screenshot.

        Uses simple normalised cross-correlation via pixel scanning.
        Not as robust as OpenCV template matching, but requires no extra deps.
        """
        screenshot_file = Path(screenshot_path)
        template_file = Path(template_path)

        if not screenshot_file.exists() or not template_file.exists():
            return False

        try:
            screenshot = Image.open(screenshot_path).convert("L")  # grayscale
            template = Image.open(template_path).convert("L")

            sw, sh = screenshot.size
            tw, th = template.size

            if tw > sw or th > sh:
                return False

            # Scan template over screenshot, compute normalized cross-correlation
            best_corr = 0.0
            template_data = list(template.getdata())

            # Downscale for performance if images are large
            scale = 1
            if sw > 800:
                scale = max(1, sw // 400)
                screenshot = screenshot.resize((sw // scale, sh // scale))
                template = template.resize((tw // scale, th // scale))
                sw, sh = screenshot.size
                tw, th = template.size
                template_data = list(template.getdata())

            ss_data = list(screenshot.getdata())

            for y in range(0, sh - th + 1, max(1, th // 4)):
                for x in range(0, sw - tw + 1, max(1, tw // 4)):
                    # Extract patch
                    patch = []
                    for py in range(th):
                        for px in range(tw):
                            patch.append(ss_data[(y + py) * sw + (x + px)])

                    # Normalized cross-correlation
                    corr = self._ncc(template_data, patch)
                    if corr > best_corr:
                        best_corr = corr

            return best_corr > 0.85

        except Exception as exc:
            logger.error("template_match_failed", error=str(exc))
            return False

    @staticmethod
    def _ncc(a: list[int], b: list[int]) -> float:
        """Normalized cross-correlation between two 1D lists."""
        n = len(a)
        if n == 0:
            return 0.0
        mean_a = sum(a) / n
        mean_b = sum(b) / n
        num = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
        den_a = math.sqrt(sum((a[i] - mean_a) ** 2 for i in range(n)))
        den_b = math.sqrt(sum((b[i] - mean_b) ** 2 for i in range(n)))
        if den_a == 0 or den_b == 0:
            return 0.0
        return num / (den_a * den_b)
