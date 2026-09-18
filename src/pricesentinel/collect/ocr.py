"""Read text off a screenshot using macOS's built-in Vision framework.

This is the fallback for pages that cannot be read as an accessibility tree.
Some marketplaces draw their result lists with custom rendering rather than
standard views, so nothing reaches the tree: a live Taobao search returned a
93-node dump containing a toolbar and no products at all, while the same screen
*visibly* showed a full grid of titles and prices. For those pages the only way
in is pixels: screencap, then OCR.

Two properties here are load-bearing, both learned the hard way:

* **Language correction is off.** Vision's correction "fixes" model numbers and
  prices into more plausible neighbours -- measured on this machine, a
  strikethrough ``¥288`` came back as ``¥233``. The data most needed is exactly
  the data that gets silently rewritten.
* **Coordinates are kept.** A custom-drawn page offers no card structure, so a
  title and its price can only be paired by where they sit on the page, which is
  done in :mod:`pricesentinel.collect.ocr_listings`.

Because OCR can misread a digit, prices sourced this way are marked as
low-confidence by the runner and must not be treated as evidence on their own.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_TOOL_DIR = _PROJECT_ROOT / "tools" / "ocr"
_BUILDER = _TOOL_DIR / "build.sh"
_BINARY = _TOOL_DIR / "ocr"


class OcrError(RuntimeError):
    """The OCR helper is missing, could not be built, or failed to run."""


@dataclass(frozen=True)
class OcrLine:
    """One recognised line of text, with its position on the page.

    Coordinates are normalized (0-1) with the origin at the **bottom-left**, as
    Vision reports them. ``bottom``/``top`` follow that convention, so ``top``
    is numerically larger.
    """

    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def bottom(self) -> float:
        return self.y

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    def to_pixels(self, screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
        """Convert to pixel bounds with the origin at the TOP-left, for tapping."""
        left = round(self.x * screen_width)
        right = round((self.x + self.width) * screen_width)
        top = round((1 - self.y - self.height) * screen_height)
        bottom = round((1 - self.y) * screen_height)
        return (left, top, right, bottom)


def find_ocr(*, build: bool = True) -> Path:
    """Locate the OCR binary, building it on first use if needed."""
    if _BINARY.exists():
        return _BINARY
    if not build:
        raise OcrError(f"OCR helper not built: {_BINARY}")
    if sys.platform != "darwin":
        raise OcrError("the screenshot channel needs macOS (Vision framework)")
    if not _BUILDER.exists():
        raise OcrError(f"missing build script: {_BUILDER}")
    try:
        done = subprocess.run(
            ["/bin/bash", str(_BUILDER)], capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired as error:
        raise OcrError("building the OCR helper timed out") from error
    if done.returncode != 0 or not _BINARY.exists():
        detail = (done.stderr or done.stdout).strip()[:400]
        raise OcrError(f"could not build the OCR helper: {detail}")
    return _BINARY


def run_ocr(image_path: str | Path, *, binary: Path | None = None) -> list[OcrLine]:
    """Recognise every line in an image and return them in Vision's own order."""
    image = Path(image_path)
    if not image.exists():
        raise OcrError(f"no such image: {image}")
    tool = binary or find_ocr()
    try:
        done = subprocess.run(
            [str(tool), str(image)], capture_output=True, text=True, timeout=120
        )
    except subprocess.TimeoutExpired as error:
        raise OcrError(f"OCR timed out on {image.name}") from error
    if done.returncode != 0:
        raise OcrError(f"OCR failed on {image.name}: {done.stderr.strip()[:200]}")

    lines: list[OcrLine] = []
    for row in done.stdout.splitlines():
        parts = row.split("\t")
        if len(parts) != 5:
            continue
        text = parts[0].strip()
        if not text:
            continue
        try:
            x, y, width, height = (float(value) for value in parts[1:])
        except ValueError:
            continue
        lines.append(OcrLine(text=text, x=x, y=y, width=width, height=height))
    return lines
