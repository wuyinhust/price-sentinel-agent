"""Pair prices with titles on a page that only exists as pixels.

A custom-drawn result list gives no card structure, so the usual anchor -- walk
up from a price to its clickable ancestor -- has nothing to walk. All that is
left is geometry: a price sits directly beneath the title of the card it belongs
to, so a title is whatever text sits immediately above a price *in the same
column*.

That makes this module the least reliable part of the pipeline, and it is
written to fail toward silence rather than toward invented data:

* A price line with no readable text above it is still reported, but titled with
  its own text, so it shows up as suspicious instead of silently vanishing.
* Tag lines ("退货宝 包邮", "已售1万+件", "品类补贴") are dropped from titles
  rather than glued in, because they appear below the price of the *previous*
  card as often as above their own.
* Two-column grids are split at the midline. Everything here assumes a
  marketplace's two-column search layout; a one-column page still works, since
  every line then lands in the same column.

Confidence: OCR misreads digits (measured: a strikethrough ``¥288`` came back as
``¥233``), so titles and prices recovered this way are marked low-confidence by
the caller and must not be treated as evidence on their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .channels import Listing, normalize_text
from .ocr import OcrLine

_CURRENCY = "\u00a5\uffe5"
_PRICE_IN_TEXT = re.compile(rf"[{_CURRENCY}]\s*(\d+(?:[.,]\d+)?)")
# A real shelf price *leads* the line; a promotional reference trails its label.
_PRICE_LEADING = re.compile(rf"^[{_CURRENCY}]\s*\d")

# Promotion and after-sales tags. These live below a price on the card they
# belong to, which is precisely where the *next* card's title search looks, so
# they must not be mistaken for titles.
_TAG_MARKERS = (
    "退货宝",
    "包邮",
    "破损包退",
    "已售",
    "回头客",
    "补贴",
    "优惠",
    "立减",
    "直降",
    "满减",
    "券后",
    "运费险",
    "人评价",
    "下单",
    "礼遇",
    "秒杀",
    "领券",
    "官方立减",
    "消费券",
)

_MAX_TITLE_LINES = 3
# Normalized y. Measured on a 2400px-tall screen: a title sits about 80px above
# the price it belongs to (0.033) when the card has no promo tag between them,
# and promo copy drawn on the product image can sit as little as 70px above the
# *next* card's title -- so vertical distance alone cannot separate the two, and
# this threshold is only here to stop the search crossing a whole product image.
# Keeping those apart is _MIN_TITLE_SCALE's job. A too-tight value here silently
# yields an empty title on any card without a promo tag, which is exactly the
# bug this number was raised to fix.
_TITLE_GAP = 0.040
# A title renders at the same size as the price it belongs to; promotional copy
# drawn on the product image renders far smaller (44px vs 18-26px measured).
_MIN_TITLE_SCALE = 0.85
_COLUMN_SPLIT = 0.5


@dataclass(frozen=True)
class PricedLine:
    """A line of text containing at least one price."""

    line: OcrLine
    text: str
    prices: tuple[float, ...]

    @property
    def price(self) -> float:
        """The lowest price on the line.

        A card can print the sale price beside a struck-through original, in
        either order, and OCR gives no styling to tell them apart. The lower
        value is the one a floor-price rule cares about, and it is the sale
        price in every layout observed so far.
        """
        return min(self.prices)


def _column_of(line: OcrLine) -> int:
    return 0 if line.center_x < _COLUMN_SPLIT else 1


def _prices_in(text: str) -> tuple[float, ...]:
    values: list[float] = []
    for raw in _PRICE_IN_TEXT.findall(text):
        try:
            values.append(float(raw.replace(",", ".")))
        except ValueError:
            continue
    return tuple(values)


def _is_tag(text: str) -> bool:
    return any(marker in text for marker in _TAG_MARKERS)


def _is_noise(text: str) -> bool:
    if len(text) < 2 or _is_tag(text):
        return True
    # Nothing but punctuation/digits carries no title information.
    return not re.search(r"[\u4e00-\u9fffA-Za-z]", text)


def priced_lines(lines: list[OcrLine]) -> list[PricedLine]:
    """Every line that leads with a price, i.e. a real shelf price.

    Leading position is what separates the shelf price from a promotional
    reference: the real one reads ``¥113.38首单价已售1万+件`` while a coupon reads
    ``优惠¥16.3`` with the number trailing its label.

    The obvious rule -- "drop lines containing tag words" -- is wrong here, and
    measurably so: it threw away ``¥26.73首单价已售1万+件`` because the line also
    carries a sales count, losing an entire product. On Taobao most price lines
    carry a sales count.
    """
    found: list[PricedLine] = []
    for line in lines:
        text = normalize_text(line.text)
        if not _PRICE_LEADING.match(text):
            continue
        prices = _prices_in(text)
        if not prices:
            continue
        found.append(PricedLine(line=line, text=text, prices=prices))
    return found


def title_above(item: PricedLine, lines: list[OcrLine]) -> str:
    """Collect the title lines sitting directly above a price, same column.

    Font size is the discriminator that actually works here. Measured on a live
    Taobao page: the title and the price render at the same size (44px on a
    1080-wide screen), while the promotional copy baked into the product *image*
    -- "更有趣 / MORE INTERESTING / 露营差旅，一支搞定" -- renders at 18-26px. That
    copy sits directly above the next card's title with no gap to speak of, so
    vertical distance alone cannot separate them; a size floor can.

    Horizontal alignment was tried first and abandoned: the image copy is
    indented ~20px past the price, but so is one banner title elsewhere on the
    same page, so no threshold separates the two cases.
    """
    column = _column_of(item.line)
    price_height = item.line.height
    above = sorted(
        (line for line in lines if _column_of(line) == column and line.y >= item.line.top - 1e-9),
        key=lambda line: line.y,
    )
    collected: list[str] = []
    cursor = item.line.top
    for line in above:
        if line.y - cursor > _TITLE_GAP:
            break  # the product image: above this is the previous card
        cursor = line.top
        if line.height < price_height * _MIN_TITLE_SCALE:
            continue  # promotional copy drawn on top of the product image
        text = normalize_text(line.text)
        if _PRICE_IN_TEXT.search(text):
            break  # another card's price: stop, do not cross the boundary
        if _is_noise(text):
            continue
        collected.append(text)
        if len(collected) >= _MAX_TITLE_LINES:
            break
    # Reading order is top-to-bottom, i.e. the reverse of collection order.
    return " ".join(reversed(collected)).strip()


def listings_from_ocr(lines: list[OcrLine], *, limit: int | None = None) -> list[Listing]:
    """Turn recognised lines into listings, one per price found on the page."""
    listings: list[Listing] = []
    for item in priced_lines(lines):
        listings.append(
            Listing(
                title=title_above(item, lines) or item.text,
                listed_price=item.price,
                # A custom-drawn page does not label which price is which, so no
                # member/coupon price is inferred: net_price stays unknown rather
                # than guessing a number the rule engine would then judge.
                net_price=None,
                merchant=None,
                prices_seen=tuple(f"{value:g}" for value in item.prices),
                position=len(listings),
            )
        )
        if limit is not None and len(listings) >= limit:
            break
    return listings
