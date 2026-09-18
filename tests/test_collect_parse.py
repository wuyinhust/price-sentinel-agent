"""Parser tests for the phone-driven collector.

The fixture is a trimmed copy of a real JD search-results dump, keeping the four
structures that actually occur and that each break a naive parser:

1. a zero-width-padded title whose clean form lives only in ``content-desc``
2. a bundle price (``2件单价约``) sitting beside the shelf price
3. a member price ahead of the regular price in the same card
4. a currency symbol split from its digits into a sibling node

Every one of these fails *silently* if mishandled -- the monitor simply reports
fewer offers, or prices them against the wrong number -- so each gets a test.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from pricesentinel.collect.channels import (
    CHANNELS,
    TreeError,
    extract_listings,
    get_channel,
    normalize_text,
)
from pricesentinel.collect.runner import MonitorTarget, collect_target

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "jd_search_probe.xml"


def dump() -> str:
    return FIXTURE.read_text(encoding="utf-8")


class NormalizeTextTests(unittest.TestCase):
    def test_zero_width_characters_are_removed(self):
        padded = "吉\u200b饮\u200b@\u200bo\u200bn\u200bc\u200be"
        self.assertEqual(normalize_text(padded), "吉饮@once")

    def test_full_width_characters_fold_to_ascii(self):
        self.assertEqual(normalize_text("２５０ｇ"), "250g")

    def test_whitespace_is_squeezed(self):
        self.assertEqual(normalize_text("  a \n  b  "), "a b")


class ExtractListingsTests(unittest.TestCase):
    def setUp(self):
        self.listings = extract_listings(dump())

    def test_one_listing_per_card(self):
        # Four cards in the fixture. Emitting one listing per *price* would
        # yield more, double-counting every offer that shows two prices.
        self.assertEqual(len(self.listings), 4)

    def test_zero_width_padding_does_not_hide_the_title(self):
        titles = [listing.title for listing in self.listings]
        self.assertIn("Manner小法棍意式咖啡豆250g", titles)

    def test_marketing_line_is_not_mistaken_for_the_title(self):
        # '27/6到期|接受者可拍|...' is longer than the real title, so picking the
        # longest string would grab it.
        manner = self.listings[0]
        self.assertEqual(manner.title, "Manner小法棍意式咖啡豆250g")
        self.assertNotIn("|", manner.title)

    def test_bundle_price_is_not_taken_as_the_shelf_price(self):
        manner = self.listings[0]
        self.assertEqual(manner.listed_price, 50.2)
        self.assertEqual(manner.prices_seen, ("¥50.2",))

    def test_member_price_becomes_net_price(self):
        xiaoman = self.listings[1]
        self.assertEqual(xiaoman.listed_price, 24.9)
        self.assertEqual(xiaoman.net_price, 23.66)
        # The rule engine judges the most aggressive visible price.
        self.assertEqual(xiaoman.price, 23.66)

    def test_shop_prefix_is_stripped_from_the_title(self):
        self.assertEqual(
            self.listings[1].title,
            "咖啡豆 小满咖啡云南精品咖啡豆中浅度烘焙100g",
        )

    def test_shop_name_beats_a_membership_badge(self):
        # '年度五星店铺' is a badge that also reads like a shop name.
        self.assertEqual(self.listings[0].merchant, "We are Manner官方旗舰店")

    def test_split_currency_is_composed_when_no_full_price_exists(self):
        # Third card publishes '¥' and '27.9' as siblings.
        self.assertEqual(self.listings[2].listed_price, 27.9)
        self.assertEqual(self.listings[2].net_price, None)

    def test_missing_merchant_is_reported_as_none(self):
        # A promo banner can occupy the slot where the shop name normally sits.
        # Guessing here would attribute a price to the wrong seller.
        self.assertIsNone(self.listings[3].merchant)
        self.assertEqual(self.listings[3].title, "吉饮@once浓缩拿铁3盒18支")

    def test_fingerprint_is_stable_and_free_of_zero_width(self):
        # The fingerprint identifies an offer, so it folds in the shop: the same
        # product sold by two sellers is two offers that must not collide.
        manner = self.listings[0]
        self.assertTrue(manner.fingerprint.startswith("manner小法棍意式咖啡豆250g"))
        self.assertIn("wearemanner", manner.fingerprint)
        self.assertNotIn("\u200b", manner.fingerprint)
        self.assertEqual(manner.fingerprint, extract_listings(dump())[0].fingerprint)

    def test_missing_hierarchy_is_rejected(self):
        with self.assertRaises(TreeError):
            extract_listings("uiautomator: permission denied")

    def test_limit_stops_early(self):
        self.assertEqual(len(extract_listings(dump(), limit=2)), 2)


class ChannelTests(unittest.TestCase):
    def test_deep_link_channels_encode_the_keyword(self):
        # Only tree channels are reached by URI. A screenshot channel is driven
        # through the app's UI instead and its template carries no keyword.
        for key, channel in CHANNELS.items():
            if channel.extraction != "tree":
                continue
            uri = channel.search_uri("吉饮 once")
            with self.subTest(channel=key):
                self.assertNotIn("吉饮", uri, "keyword must be percent-encoded")
                self.assertIn("%E5%90%89", uri)

    def test_screenshot_channels_carry_a_ui_driver(self):
        # A screenshot channel has no deep link to fall back on, so without a
        # driver it would fail at runtime on the phone instead of here.
        for key, channel in CHANNELS.items():
            if channel.extraction == "screenshot":
                with self.subTest(channel=key):
                    self.assertIsNotNone(channel.driver)

    def test_unknown_channel_names_the_known_ones(self):
        with self.assertRaises(KeyError) as caught:
            get_channel("taobao-x")
        self.assertIn("jd", str(caught.exception))


class TargetMatchingTests(unittest.TestCase):
    def make(self, **overrides) -> MonitorTarget:
        fields = {
            "product_id": "p-1",
            "package_type": "bundle",
            "channel": "jd",
            "keyword": "吉饮 once",
            "match_all": ("once",),
            "match_any": ("吉饮", "拿铁"),
        }
        fields.update(overrides)
        return MonitorTarget(**fields)

    def test_zero_width_in_a_title_still_matches(self):
        # The whole point of stripping zero-width characters: without it this
        # listing would look like a non-match and the breach would be missed.
        listing = extract_listings(dump())[0]
        self.assertTrue(self.make(match_all=("manner",), match_any=()).matches(listing))

    def test_match_any_still_filters_after_match_all(self):
        # match_all alone is not enough: a competitor carrying the same brand
        # token must still be filtered out by the match_any terms.
        listing = extract_listings(dump())[0]
        self.assertFalse(self.make(match_any=("拿铁",)).matches(listing))

    def test_lookalike_is_rejected(self):
        listing = extract_listings(dump())[1]
        self.assertFalse(self.make().matches(listing))

    def test_classify_reads_the_packaging_from_the_title(self):
        # Card D is '吉饮@once浓缩拿铁3盒18支'.
        target = self.make(
            package_types={"bundle": ("3盒", "18支"), "single": ("1盒", "6支")}
        )
        self.assertEqual(target.classify(extract_listings(dump())[3]), "bundle")

    def test_unclassifiable_packaging_returns_none_rather_than_guessing(self):
        # Guessing would price a single box against a bundle floor and invent a
        # breach, so the target reports it as unclassified instead.
        target = self.make(package_types={"bundle": ("5盒",)})
        self.assertIsNone(target.classify(extract_listings(dump())[3]))

    def test_without_rules_every_listing_takes_the_default_packaging(self):
        target = self.make()
        self.assertEqual(target.classify(extract_listings(dump())[3]), "bundle")


class StubDeviceTests(unittest.TestCase):
    """Exercise the runner's plumbing with a device stub, no phone needed."""

    # The real page-settle delays are 14s per target; tests must not wait them out.
    NO_SLEEP = staticmethod(lambda _seconds: None)

    class StubDevice:
        serial = "STUB"

        def __init__(self, xml: str):
            self.xml = xml
            self.opened: list[tuple[str, str | None]] = []
            self.keys: list[str] = []

        def open_uri(self, uri: str, package: str | None = None) -> None:
            self.opened.append((uri, package))

        def key(self, keycode) -> None:
            self.keys.append(str(keycode))

        def read_tree(self) -> str:
            return self.xml

    def test_collect_target_produces_observations(self):
        device = self.StubDevice(dump())
        target = MonitorTarget(
            product_id="once-latte",
            package_type="single",
            channel="jd",
            keyword="吉饮 once",
            match_all=("once",),
            package_types={"bundle": ("3盒", "18支"), "single": ("1盒",)},
        )
        outcome = collect_target(
            device,
            target,
            observed_at="2026-09-16T12:00:00+00:00",
            sleep=self.NO_SLEEP,
        )
        self.assertIsNone(outcome.error)
        self.assertEqual(len(outcome.matched), 1)
        self.assertEqual(len(outcome.observations), 1)
        observation = outcome.observations[0]
        self.assertEqual(observation.package_type, "bundle")
        self.assertEqual(observation.effective_price, 72.8)
        self.assertEqual(observation.channel, "jd")
        self.assertEqual(observation.observed_at, "2026-09-16T12:00:00+00:00")
        # The deep link is submitted, not merely opened.
        self.assertEqual(device.keys, ["66"])

    def test_rerunning_the_same_day_reuses_the_observation_id(self):
        """Idempotency is what keeps a daily monitor from ballooning the table."""
        device = self.StubDevice(dump())
        target = MonitorTarget(
            product_id="once-latte",
            package_type="bundle",
            channel="jd",
            keyword="吉饮 once",
            match_all=("once",),
        )
        first = collect_target(device, target, sleep=self.NO_SLEEP, observed_at="2026-09-16T08:00:00+00:00")
        second = collect_target(device, target, sleep=self.NO_SLEEP, observed_at="2026-09-16T20:00:00+00:00")
        self.assertEqual(
            [o.observation_id for o in first.observations],
            [o.observation_id for o in second.observations],
        )

    def test_a_different_day_is_a_new_observation(self):
        device = self.StubDevice(dump())
        target = MonitorTarget(
            product_id="once-latte",
            package_type="bundle",
            channel="jd",
            keyword="吉饮 once",
            match_all=("once",),
        )
        first = collect_target(device, target, sleep=self.NO_SLEEP, observed_at="2026-09-16T08:00:00+00:00")
        second = collect_target(device, target, sleep=self.NO_SLEEP, observed_at="2026-09-17T08:00:00+00:00")
        self.assertNotEqual(
            first.observations[0].observation_id,
            second.observations[0].observation_id,
        )

    def test_a_listing_with_unreadable_title_is_reported_not_dropped(self):
        device = self.StubDevice("<hierarchy rotation='0'></hierarchy>")
        target = MonitorTarget(
            product_id="p",
            package_type="single",
            channel="jd",
            keyword="x",
            match_all=("once",),
        )
        outcome = collect_target(device, target, sleep=self.NO_SLEEP)
        self.assertIsNone(outcome.error)
        self.assertEqual(outcome.listings, [])
        self.assertIn("0 listings", outcome.summary())


if __name__ == "__main__":
    unittest.main()
