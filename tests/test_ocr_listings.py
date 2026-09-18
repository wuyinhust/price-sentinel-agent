"""Tests for the screenshot channel's layout parser.

Every coordinate here is copied from a real Taobao results page captured on the
reference device (1080x2400), so the fixtures encode the layout the parser was
actually tuned against rather than an idealised one. That matters because the
hard cases are the messy ones: a title that is not the longest string, promo
copy drawn on top of the product image, and a price line that also carries a
sales count.
"""

from __future__ import annotations

import unittest

from pricesentinel.collect.ocr import OcrLine
from pricesentinel.collect.ocr_listings import listings_from_ocr, priced_lines
from pricesentinel.collect.runner import MonitorConfig

SCREEN = (1080, 2400)


def line(text: str, left: int, top: int, width: int, height: int) -> OcrLine:
    """Build an OcrLine from pixel bounds, converting to Vision's coordinates.

    Vision reports normalized values with the origin at the bottom-left; callers
    think in pixels from the top-left. Doing the conversion here keeps the
    fixtures readable and pins the conversion itself.
    """
    screen_width, screen_height = SCREEN
    return OcrLine(
        text=text,
        x=left / screen_width,
        y=1 - (top + height) / screen_height,
        width=width / screen_width,
        height=height / screen_height,
    )


class PriceLineTests(unittest.TestCase):
    def test_a_price_line_carrying_a_sales_count_is_still_a_price_line(self):
        # The regression that bit: keying "is this a price line?" off "does it
        # contain a tag word" threw this away on 已售, losing a whole product.
        lines = [line("¥26.73首单价已售1万+件", 566, 1892, 417, 45)]
        self.assertEqual(len(priced_lines(lines)), 1)

    def test_a_promotional_reference_is_not_a_shelf_price(self):
        # The number trails its label here, so the line does not lead with ¥.
        lines = [line("优惠¥16.3", 30, 1900, 200, 40)]
        self.assertEqual(priced_lines(lines), [])

    def test_the_lower_of_two_prices_wins(self):
        # A card may print the sale price beside a struck-through original in
        # either order, and OCR carries no styling to tell them apart.
        for text in ("¥113.38 ¥233", "¥233 ¥113.38"):
            with self.subTest(text=text):
                priced = priced_lines([line(text, 536, 515, 300, 44)])
                self.assertEqual(priced[0].price, 113.38)


class TitleTests(unittest.TestCase):
    def test_the_title_is_the_line_above_the_price(self):
        lines = [
            line("天猫中秋季【6罐组合】」", 32, 1769, 456, 44),
            line(")品牌新客补贴，当日有效", 52, 1823, 392, 35),
            line("¥113.38首单价全店售9万+件", 30, 1893, 475, 44),
        ]
        listings = listings_from_ocr(lines)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].title, "天猫中秋季【6罐组合】」")

    def test_promo_copy_drawn_on_the_image_is_not_a_title(self):
        # This copy sits directly above the next card's title with almost no
        # gap, so distance alone cannot exclude it -- only the size floor can.
        lines = [
            line("露营差旅，一支搞定", 50, 1702, 135, 18),
            line("一罐八享，随心调配", 54, 1678, 130, 18),
            line("天猫中秋季【6罐组合】」", 32, 1769, 456, 44),
            line("¥113.38首单价全店售9万+件", 30, 1893, 475, 44),
        ]
        self.assertEqual(listings_from_ocr(lines)[0].title, "天猫中秋季【6罐组合】」")

    def test_columns_do_not_borrow_each_others_titles(self):
        # Two cards side by side: each price must take its own column's title.
        lines = [
            line("左列商品名", 32, 1769, 456, 44),
            line("右列商品名", 564, 1771, 489, 40),
            line("¥113.38首单价", 30, 1893, 475, 44),
            line("¥26.73首单价", 566, 1892, 417, 45),
        ]
        by_price = {item.listed_price: item.title for item in listings_from_ocr(lines)}
        self.assertEqual(by_price[113.38], "左列商品名")
        self.assertEqual(by_price[26.73], "右列商品名")

    def test_a_price_with_no_readable_title_falls_back_to_its_own_text(self):
        # Surfacing a suspicious row beats dropping the price without a word.
        lines = [line("¥113.38 ¥233", 536, 515, 300, 44)]
        self.assertEqual(listings_from_ocr(lines)[0].title, "¥113.38 ¥233")


class ScreenshotChannelConfigTests(unittest.TestCase):
    def make(self, **target_overrides) -> MonitorConfig:
        target = {
            "product_id": "p-1",
            "channel": "taobao",
            "keyword": "吉饮咖啡",
            "match_all": ["吉饮"],
        }
        target.update(target_overrides)
        return MonitorConfig.from_dict(
            {
                "monitor_id": "m",
                "products": [
                    {
                        "product_id": "p-1",
                        "brand": "吉饮",
                        "name": "吉饮咖啡",
                        "sku": None,
                        "units_per_pack": 1,
                    }
                ],
                "targets": [target],
            }
        )

    def test_a_cjk_term_is_rejected_before_it_reaches_the_phone(self):
        # input text raises NullPointerException on CJK, so catching it here
        # saves a whole collection run -- including the minute Taobao takes to
        # cold start -- from failing at the last step.
        problems = self.make().validate()
        self.assertTrue(any("lookup" in problem for problem in problems), problems)

    def test_an_ascii_lookup_clears_the_same_config(self):
        self.assertEqual(self.make(lookup="jiyin").validate(), [])

    def test_a_tree_channel_is_unaffected(self):
        # Only UI-driven channels type anything, so a Chinese keyword is still
        # perfectly fine where the keyword travels inside a URL.
        self.assertEqual(self.make(channel="jd").validate(), [])


if __name__ == "__main__":
    unittest.main()
