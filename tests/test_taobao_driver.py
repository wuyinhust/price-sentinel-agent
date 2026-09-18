"""Tests for the UI driver that reaches Taobao's search results.

Taobao cannot be searched by deep link and its results list cannot be read from
the accessibility tree, so the whole channel is a sequence of taps at
coordinates read off a screenshot. That makes it the one channel whose failure
mode is *silence*: a tap that lands in a gap does nothing, the phone stays on
the search door, and a monitor that assumes "I tapped, therefore I searched"
records zero listings while reporting perfect health.

These tests pin the behaviour that fixes it -- the driver only reports success
for a page it has seen prices on -- using a fake phone that reproduces the gap,
so the regression cannot come back unnoticed.

Coordinates are the ones measured on the reference device (1080x2400) in
``test_ocr_listings``; the door layout is copied from a live 淘宝 search screen.
"""

from __future__ import annotations

import unittest
from unittest import mock

from pricesentinel.collect import channels
from pricesentinel.collect.channels import open_search, taobao_search
from pricesentinel.collect.device import DeviceError
from pricesentinel.collect.ocr import OcrLine
from pricesentinel.collect.runner import MonitorTarget, collect_target

SCREEN = (1080, 2400)

# The search door as it really looked after typing "joyinbag": the field echoing
# the term, then the suggestion rows, each about 121px apart.
DOOR_ECHO_Y = 122
DOOR_ROWS = (
    ("Q joyinbag咖啡", 250),
    ("Q joyinbag冷萃美式咖啡浓缩", 371),
    ("Q joyinbag兜饮", 492),
)


def line(text: str, left: int, top: int, width: int, height: int) -> OcrLine:
    """Build an OcrLine from pixel bounds, converting to Vision's coordinates."""
    screen_width, screen_height = SCREEN
    return OcrLine(
        text=text,
        x=left / screen_width,
        y=1 - (top + height) / screen_height,
        width=width / screen_width,
        height=height / screen_height,
    )


def door_lines(rows=DOOR_ROWS) -> list[OcrLine]:
    return [line("joyinbag", 195, DOOR_ECHO_Y, 162, 44)] + [
        line(text, 32, top, 530, 46) for text, top in rows
    ]


def result_lines(*, priced: bool = True) -> list[OcrLine]:
    lines = [line("天猫 中秋季 JOYINBAG B8冷萃美式 氮气咖啡液 4罐240g 送杯子", 30, 1000, 500, 44)]
    if priced:
        lines.append(line("¥85.14首单价已售1万+件", 30, 1070, 420, 44))
    return lines


class FakeTaobao:
    """A phone answering the way Taobao does -- including the way it fails.

    ``dead_rows`` are suggestion texts whose tap does nothing at all, which is
    what a tap landing in a re-laid-out gap looks like from this side.
    """

    serial = "STUB"
    DOOR = "com.taobao.taobao/com.taobao.search.searchdoor.SearchDoorActivity"
    RESULTS = "com.taobao.taobao/com.taobao.search.sf.MainSearchResultActivity"
    START = "com.taobao.taobao/com.taobao.main.MainActivity"

    def __init__(self, *, door=None, results=None, dead_rows=(), settled=True):
        self.focus = self.START
        self.door = door_lines() if door is None else door
        self.results = result_lines() if results is None else results
        self.dead_rows = set(dead_rows)
        # False means the results page opens but never draws a price, the way a
        # suggestion that leads to an empty result set behaves.
        self.settled = settled
        self.taps: list[tuple[int, int]] = []
        self.keys: list[str] = []
        self.shells: list[tuple[str, ...]] = []
        self.opened: list[tuple[str, str | None]] = []
        self.screenshots: list[str] = []

    # ---- plumbing the driver uses ---------------------------------------

    def shell(self, *args: str, timeout: float = 30.0) -> str:
        self.shells.append(args)
        return ""

    def open_uri(self, uri: str, package: str | None = None) -> None:
        self.opened.append((uri, package))

    def key(self, keycode) -> None:
        self.keys.append(str(keycode))
        if str(keycode) == "4":
            self.focus = self.DOOR

    def screen_size(self) -> tuple[int, int]:
        return SCREEN

    def screen_state(self):
        return mock.Mock(focus=self.focus)

    def screenshot(self, destination):
        name = str(destination).rsplit("/", 1)[-1]
        self.screenshots.append(name)
        destination = mock.Mock()
        destination.__str__ = lambda _self: name
        return name

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))
        row = self._row_at(y)
        if row is None or row in self.dead_rows:
            return  # the tap landed in a gap and Taobao ignored it
        self.focus = self.RESULTS

    def _row_at(self, y: int):
        for text, top in DOOR_ROWS:
            if top <= y <= top + 60:
                return text
        return None


def fake_ocr(device: FakeTaobao):
    """Answer ``run_ocr`` with whichever page the screenshot belonged to."""

    def run(image_path, **kwargs):
        if "suggest" in str(image_path):
            return device.door
        return device.results if device.settled else device.results[:1]

    return run


class SuggestPickerTests(unittest.TestCase):
    def pick(self, lines, term="joyinbag", needles=("joyinbag", "兜饮", "b8")):
        return channels._suggestions(lines, term, needles, *SCREEN)

    def test_the_field_echoing_the_term_is_not_a_candidate(self):
        # The echo sits directly above the list, reads as a suggestion, and
        # matches the term -- but tapping it re-runs the search door rather than
        # searching, so it must never be chosen.
        picked = self.pick(door_lines())
        self.assertNotIn("joyinbag", [item.text for item in picked])

    def test_candidates_come_back_in_the_app_s_own_order(self):
        # Taobao ranks its suggestions; the first row is the best query to run,
        # so the driver must not reorder them on a hunch.
        picked = self.pick(door_lines())
        self.assertEqual([item.text for item in picked], [text for text, _ in DOOR_ROWS])

    def test_a_tap_point_sits_inside_the_row_it_names(self):
        picked = self.pick(door_lines())
        self.assertEqual(picked[0].y, (250 + 296) // 2)

    def test_rows_without_a_match_are_left_alone(self):
        picked = self.pick(door_lines(), needles=("suntory",))
        self.assertEqual(picked, [])


class TaobaoDriverTests(unittest.TestCase):
    NO_SLEEP = staticmethod(lambda _seconds: None)

    def drive(self, device: FakeTaobao, term="joyinbag"):
        with mock.patch.object(channels, "run_ocr", fake_ocr(device)):
            return taobao_search(
                device, term, ("joyinbag", "兜饮", "b8"), sleep=self.NO_SLEEP
            )

    def test_a_tap_that_lands_in_a_gap_falls_through_to_the_next_suggestion(self):
        # The bug this driver was rewritten for. Row 1 is read correctly and
        # tapped correctly, and Taobao ignores it; without verification the
        # caller photographed the search door and recorded zero listings.
        device = FakeTaobao(dead_rows={"Q joyinbag咖啡"})
        page = self.drive(device)
        self.assertIsNotNone(page)
        self.assertEqual(len(device.taps), 2)
        self.assertEqual(device.taps[1][1], (371 + 417) // 2)
        self.assertEqual(device.focus, FakeTaobao.RESULTS)

    def test_a_page_that_never_draws_a_price_is_not_reported_as_success(self):
        # Landing on the results activity is not enough: a suggestion that leads
        # to an empty result set gets there too, and reporting it would look
        # like a healthy zero.
        device = FakeTaobao(settled=False)
        with mock.patch.object(channels, "run_ocr", fake_ocr(device)):
            with self.assertRaises(DeviceError) as caught:
                taobao_search(device, "joyinbag", ("joyinbag",), sleep=self.NO_SLEEP)
        self.assertIn("none of them opened a search results page showing a price",
                      str(caught.exception))
        # Every distinct suggestion was given a turn before giving up.
        self.assertEqual(len(device.taps), channels._TAOBAO_SUGGESTION_ATTEMPTS)
    def test_a_dead_first_row_is_not_retried_on_the_second_pass(self):
        device = FakeTaobao(dead_rows={"Q joyinbag咖啡"})
        self.drive(device)
        self.assertEqual(len(set(device.taps)), len(device.taps))

    def test_the_retry_returns_to_the_door_and_retypes_the_term(self):
        # A failed attempt can leave the phone on a results page, where there is
        # no suggestion list to read; the query has to be put back in the box.
        device = FakeTaobao(dead_rows={"Q joyinbag咖啡"})
        self.drive(device)
        retyped = [a for a in device.shells if a[:2] == ("input", "text")]
        self.assertEqual(retyped, [("input", "text", "joyinbag")] * 2)

    def test_the_page_it_returns_is_the_one_it_validated(self):
        device = FakeTaobao()
        page = self.drive(device)
        self.assertEqual(page, device.results)

    def test_the_door_is_photographed_once_per_suggestion_tried(self):
        device = FakeTaobao(dead_rows={"Q joyinbag咖啡"})
        self.drive(device)
        self.assertEqual(device.screenshots.count("suggest.png"), 2)


class OpenSearchTests(unittest.TestCase):
    NO_SLEEP = staticmethod(lambda _seconds: None)

    def test_a_screenshot_channel_hands_back_the_page_its_driver_read(self):
        device = FakeTaobao()
        with mock.patch.object(channels, "run_ocr", fake_ocr(device)):
            lines = open_search(
                device,
                channels.get_channel("taobao"),
                "JOYINBAG B8冷萃美式",
                term="joyinbag",
                needles=("joyinbag",),
                sleep=self.NO_SLEEP,
            )
        self.assertEqual(lines, device.results)

    def test_a_tree_channel_returns_nothing_to_reuse(self):
        device = FakeTaobao()
        lines = open_search(
            device, channels.get_channel("jd"), "吉饮 once", sleep=self.NO_SLEEP
        )
        self.assertIsNone(lines)


class CollectTargetTests(unittest.TestCase):
    NO_SLEEP = staticmethod(lambda _seconds: None)

    def test_a_page_the_driver_already_read_is_not_photographed_again(self):
        # The driver had to screenshot the page to know it had prices; paying
        # for the same screenshot twice only adds a chance of catching the list
        # mid-scroll the second time.
        device = FakeTaobao()
        target = MonitorTarget(
            product_id="joyinbag-b8",
            package_type="single",
            channel="taobao",
            keyword="JOYINBAG B8冷萃美式",
            lookup="joyinbag",
            match_any=("joyinbag", "b8"),
        )
        with mock.patch.object(channels, "run_ocr", fake_ocr(device)):
            outcome = collect_target(device, target, sleep=self.NO_SLEEP)
        self.assertIsNone(outcome.error)
        # One door screenshot per candidate tried, and exactly one of the
        # results page -- the one the driver took to check for prices. A second
        # results screenshot would mean the runner ignored what it was handed.
        self.assertEqual(device.screenshots, ["suggest.png", "results.png"])
        self.assertEqual(len(outcome.matched), 1)
        self.assertEqual(outcome.observations[0].listed_price, 85.14)
        self.assertEqual(outcome.observations[0].metadata["extraction"], "screenshot")


if __name__ == "__main__":
    unittest.main()
