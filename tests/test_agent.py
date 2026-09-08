import unittest

from pricesentinel.agent import PriceSentinelAgent
from pricesentinel.models import CaseStatus, PriceObservation, PricePolicy, Product
from pricesentinel.store import SQLiteStore


class AgentTests(unittest.TestCase):
    def test_repeated_breach_updates_one_open_case(self):
        store = SQLiteStore()
        store.upsert_product(Product("p-1", "Demo", "Demo product"))
        store.upsert_policy(PricePolicy("p-1", "single", 100, "2026-01-01"))
        agent = PriceSentinelAgent(store)
        first = PriceObservation("obs-1", "p-1", "single", "marketplace-a", "shop", "2026-09-08T00:00:00+00:00", 80)
        second = PriceObservation("obs-2", "p-1", "single", "marketplace-a", "shop", "2026-09-09T00:00:00+00:00", 70)
        agent.process([first, second])
        cases = store.list_cases(CaseStatus.OPEN)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].latest_observation_id, "obs-2")
