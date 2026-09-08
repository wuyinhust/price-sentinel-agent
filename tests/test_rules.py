import unittest

from pricesentinel.models import PriceObservation, PricePolicy, Severity
from pricesentinel.rules import evaluate_breach


class RuleTests(unittest.TestCase):
    def observation(self, **kwargs):
        values = {
            "observation_id": "obs-1", "product_id": "p-1", "package_type": "single",
            "channel": "marketplace-a", "merchant": "merchant-1", "observed_at": "2026-09-08T00:00:00+00:00",
            "listed_price": 90.0,
        }
        values.update(kwargs)
        return PriceObservation(**values)

    def test_breach_uses_net_price_and_assigns_severity(self):
        result = evaluate_breach(self.observation(listed_price=110, net_price=88), PricePolicy("p-1", "single", 100, "2026-01-01"))
        self.assertTrue(result.is_breach)
        self.assertEqual(result.severity, Severity.HIGH)
        self.assertEqual(result.effective_price, 88.0)

    def test_clear_at_floor(self):
        result = evaluate_breach(self.observation(listed_price=100), PricePolicy("p-1", "single", 100, "2026-01-01"))
        self.assertEqual(result.decision, "clear")

    def test_missing_policy_is_explicitly_unavailable(self):
        result = evaluate_breach(self.observation(), None)
        self.assertEqual(result.decision, "unavailable")
