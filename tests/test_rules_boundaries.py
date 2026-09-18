"""Boundary and branch coverage for the deterministic rule engine.

The upstream suite covers the happy paths only. The severity ladder, the
tolerance window, currency handling and the ``unavailable`` branches are where
a silent mis-decision would hide, so they are pinned down here.

Run with:  PYTHONPATH=src python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from pricesentinel.models import PriceObservation, PricePolicy, Severity
from pricesentinel.rules import evaluate_breach

FLOOR = 100.0


def observation(**kwargs):
    values = {
        "observation_id": "obs-1",
        "product_id": "p-1",
        "package_type": "single",
        "channel": "marketplace-a",
        "merchant": "merchant-1",
        "observed_at": "2026-09-08T00:00:00+00:00",
        "listed_price": 90.0,
    }
    values.update(kwargs)
    return PriceObservation(**values)


def policy(**kwargs):
    values = {"product_id": "p-1", "package_type": "single", "floor_price": FLOOR, "effective_from": "2026-01-01"}
    values.update(kwargs)
    return PricePolicy(**values)


class SeverityLadderTests(unittest.TestCase):
    """The ladder is inclusive at each threshold: <= -20/10/3 percent."""

    def severity_for(self, effective_price):
        result = evaluate_breach(observation(listed_price=effective_price), policy())
        self.assertTrue(result.is_breach)
        return result.severity

    def test_exact_thresholds_are_inclusive(self):
        cases = [
            (80.00, Severity.CRITICAL),  # exactly -20%
            (90.00, Severity.HIGH),      # exactly -10%
            (97.00, Severity.MEDIUM),    # exactly -3%
        ]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(self.severity_for(price), expected)

    def test_just_inside_each_band_drops_one_level(self):
        cases = [
            (80.01, Severity.HIGH),
            (90.01, Severity.MEDIUM),
            (97.01, Severity.LOW),
        ]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(self.severity_for(price), expected)

    def test_deeper_than_critical_stays_critical(self):
        self.assertEqual(self.severity_for(50.0), Severity.CRITICAL)

    def test_free_listing_is_critical(self):
        self.assertEqual(self.severity_for(0.0), Severity.CRITICAL)


class DecisionBranchTests(unittest.TestCase):
    def test_net_price_takes_precedence_over_listed(self):
        result = evaluate_breach(observation(listed_price=110.0, net_price=88.0), policy())
        self.assertEqual(result.effective_price, 88.0)
        self.assertTrue(result.is_breach)
        self.assertEqual(result.severity, Severity.HIGH)

    def test_listed_price_used_when_net_is_absent(self):
        result = evaluate_breach(observation(listed_price=95.0, net_price=None), policy())
        self.assertEqual(result.effective_price, 95.0)

    def test_clear_above_floor(self):
        result = evaluate_breach(observation(listed_price=120.0), policy())
        self.assertEqual(result.decision, "clear")
        self.assertIsNone(result.severity)

    def test_at_floor_is_clear(self):
        self.assertEqual(evaluate_breach(observation(listed_price=FLOOR), policy()).decision, "clear")

    def test_missing_policy_is_unavailable_not_clear(self):
        result = evaluate_breach(observation(), None)
        self.assertEqual(result.decision, "unavailable")
        self.assertEqual(result.reason, "no effective policy")
        self.assertIsNone(result.severity)
        self.assertFalse(result.is_breach)

    def test_missing_price_is_unavailable(self):
        result = evaluate_breach(observation(listed_price=None, net_price=None), policy())
        self.assertEqual(result.decision, "unavailable")
        self.assertEqual(result.reason, "missing effective price")
        self.assertEqual(result.floor_price, FLOOR)

    def test_currency_mismatch_is_unavailable(self):
        result = evaluate_breach(observation(listed_price=80.0, currency="USD"), policy(currency="CNY"))
        self.assertEqual(result.decision, "unavailable")
        self.assertEqual(result.reason, "currency mismatch")

    def test_matching_foreign_currency_is_evaluated(self):
        result = evaluate_breach(observation(listed_price=80.0, currency="USD"), policy(currency="USD"))
        self.assertTrue(result.is_breach)


class ToleranceTests(unittest.TestCase):
    def test_price_inside_tolerance_is_clear(self):
        # floor 100, tolerance 5 -> anything at or above 95 clears.
        result = evaluate_breach(observation(listed_price=96.0), policy(), tolerance=5.0)
        self.assertEqual(result.decision, "clear")

    def test_price_at_tolerance_edge_is_clear(self):
        result = evaluate_breach(observation(listed_price=95.0), policy(), tolerance=5.0)
        self.assertEqual(result.decision, "clear")

    def test_price_below_tolerance_is_breach(self):
        result = evaluate_breach(observation(listed_price=94.99), policy(), tolerance=5.0)
        self.assertTrue(result.is_breach)

    def test_severity_still_measured_against_floor_not_tolerance(self):
        # The tolerance only widens what counts as a breach; severity stays
        # measured against the floor. 94.99 is -5.01%, the MEDIUM band.
        result = evaluate_breach(observation(listed_price=94.99), policy(), tolerance=5.0)
        self.assertEqual(result.severity, Severity.MEDIUM)


class DegenerateInputTests(unittest.TestCase):
    def test_zero_floor_does_not_divide_by_zero(self):
        result = evaluate_breach(observation(listed_price=0.0), policy(floor_price=0.0))
        self.assertEqual(result.delta_ratio, 0.0)
        self.assertEqual(result.decision, "clear")

    def test_negative_effective_price_against_zero_floor_breaches(self):
        # Unusual input, but the path must stay deterministic rather than crash.
        result = evaluate_breach(observation(listed_price=-1.0), policy(floor_price=0.0))
        self.assertTrue(result.is_breach)


class DeltaUnitTests(unittest.TestCase):
    """``delta_ratio`` is a ratio, and the field name must keep saying so.

    This once read ``delta_pct`` while holding a fraction, which produced a
    reported shortfall of 0.08% where the real figure was 7.85% -- a wrong
    number that looked perfectly plausible in a report.
    """

    def test_delta_is_a_ratio_not_a_percentage(self):
        result = evaluate_breach(observation(listed_price=92.15), policy(floor_price=100.0))
        self.assertAlmostEqual(result.delta_ratio, -0.0785, places=4)
        self.assertAlmostEqual(result.delta_ratio * 100, -7.85, places=2)

    def test_severity_thresholds_use_the_same_unit(self):
        # -7.85% falls in the MEDIUM band, which only holds if the bands are
        # expressed in the same ratio unit.
        result = evaluate_breach(observation(listed_price=92.15), policy(floor_price=100.0))
        self.assertEqual(result.severity, Severity.MEDIUM)


if __name__ == "__main__":
    unittest.main()
