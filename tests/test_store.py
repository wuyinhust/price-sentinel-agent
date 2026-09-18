"""Coverage for SQLiteStore: policy resolution, case de-duplication, audit trail.

These pin down the "repeated breach updates one open case" contract and the
policy effective-window / channel-scope lookup, which the upstream suite only
touches indirectly.
"""

from __future__ import annotations

import unittest

from pricesentinel.agent import PriceSentinelAgent
from pricesentinel.models import (
    CaseStatus,
    PriceObservation,
    PricePolicy,
    Product,
    Severity,
)
from pricesentinel.store import SQLiteStore


def observation(observation_id, listed_price, *, observed_at="2026-09-08T00:00:00+00:00", merchant="shop-1", channel="marketplace-a", net_price=None):
    return PriceObservation(
        observation_id=observation_id,
        product_id="p-1",
        package_type="single",
        channel=channel,
        merchant=merchant,
        observed_at=observed_at,
        listed_price=listed_price,
        net_price=net_price,
    )


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStore()
        self.addCleanup(self.store.close)
        self.store.upsert_product(Product("p-1", "Demo", "Demo product"))

    def policy(self, floor_price, effective_from="2026-01-01", **kwargs):
        self.store.upsert_policy(PricePolicy("p-1", "single", floor_price, effective_from, **kwargs))
        return floor_price


class PolicyResolutionTests(StoreFixture):
    def test_missing_policy_returns_none(self):
        self.assertIsNone(self.store.active_policy("p-1", "single", "2026-09-01"))

    def test_policy_applies_inside_effective_window(self):
        self.policy(100, effective_from="2026-01-01", effective_to="2026-06-30")
        self.assertIsNotNone(self.store.active_policy("p-1", "single", "2026-03-01"))

    def test_policy_expired_after_window(self):
        self.policy(100, effective_from="2026-01-01", effective_to="2026-06-30")
        self.assertIsNone(self.store.active_policy("p-1", "single", "2026-07-01"))

    def test_policy_not_yet_effective(self):
        self.policy(100, effective_from="2026-06-01")
        self.assertIsNone(self.store.active_policy("p-1", "single", "2026-03-01"))

    def test_latest_effective_from_wins(self):
        self.policy(100, effective_from="2026-01-01")
        self.policy(120, effective_from="2026-06-01")
        resolved = self.store.active_policy("p-1", "single", "2026-09-01")
        self.assertEqual(resolved.floor_price, 120)

    def test_wildcard_scope_matches_any_channel(self):
        self.policy(100, channel_scope="*")
        self.assertIsNotNone(self.store.active_policy("p-1", "single", "2026-09-01", "marketplace-z"))

    def test_specific_scope_does_not_leak_to_other_channels(self):
        self.policy(100, channel_scope="marketplace-a")
        self.assertIsNotNone(self.store.active_policy("p-1", "single", "2026-09-01", "marketplace-a"))
        self.assertIsNone(self.store.active_policy("p-1", "single", "2026-09-01", "marketplace-b"))


class UpsertIdempotencyTests(StoreFixture):
    def test_product_upsert_updates_in_place(self):
        self.store.upsert_product(Product("p-1", "Demo", "Renamed product"))
        rows = self.store.connection.execute("SELECT name FROM products").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Renamed product")

    def test_policy_upsert_updates_in_place(self):
        self.policy(100)
        self.policy(150)
        rows = self.store.connection.execute("SELECT floor_price FROM policies").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["floor_price"], 150)

    def test_observation_upsert_is_idempotent(self):
        self.store.save_observation(observation("obs-1", 90.0))
        self.store.save_observation(observation("obs-1", 85.0))
        rows = self.store.connection.execute("SELECT listed_price FROM observations").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["listed_price"], 85.0)


class CaseLifecycleTests(StoreFixture):
    def setUp(self):
        super().setUp()
        self.policy(100)
        self.agent = PriceSentinelAgent(self.store)

    def test_repeated_breach_reuses_one_open_case(self):
        self.agent.process([
            observation("obs-1", 95.0, observed_at="2026-09-08T00:00:00+00:00"),
            observation("obs-2", 90.0, observed_at="2026-09-09T00:00:00+00:00"),
        ])
        cases = self.store.list_cases(CaseStatus.OPEN)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].first_detected_at, "2026-09-08T00:00:00+00:00")
        self.assertEqual(cases[0].last_detected_at, "2026-09-09T00:00:00+00:00")
        self.assertEqual(cases[0].latest_observation_id, "obs-2")

    def test_severity_escalates_within_a_case(self):
        self.agent.process([
            observation("obs-1", 95.0),   # -5%  -> medium
            observation("obs-2", 75.0),   # -25% -> critical
        ])
        self.assertEqual(self.store.list_cases()[0].severity, Severity.CRITICAL)

    def test_severity_does_not_de_escalate(self):
        self.agent.process([
            observation("obs-1", 75.0),   # critical
            observation("obs-2", 98.0),   # -2% -> low, must not lower the case
        ])
        self.assertEqual(self.store.list_cases()[0].severity, Severity.CRITICAL)

    def test_distinct_merchants_get_distinct_cases(self):
        self.agent.process([
            observation("obs-1", 90.0, merchant="shop-1"),
            observation("obs-2", 90.0, merchant="shop-2"),
        ])
        self.assertEqual(len(self.store.list_cases()), 2)

    def test_distinct_channels_get_distinct_cases(self):
        self.agent.process([
            observation("obs-1", 90.0, channel="marketplace-a"),
            observation("obs-2", 90.0, channel="marketplace-b"),
        ])
        self.assertEqual(len(self.store.list_cases()), 2)

    def test_clear_observation_creates_no_case(self):
        self.agent.process([observation("obs-1", 120.0)])
        self.assertEqual(self.store.list_cases(), [])

    def test_audit_event_per_breach(self):
        self.agent.process([
            observation("obs-1", 95.0),
            observation("obs-2", 90.0),
        ])
        events = self.store.connection.execute("SELECT case_id, event_type, actor FROM case_events").fetchall()
        self.assertEqual(len(events), 2)
        self.assertEqual({event["event_type"] for event in events}, {"breach_detected"})
        self.assertEqual({event["actor"] for event in events}, {"agent"})
        self.assertEqual(len({event["case_id"] for event in events}), 1)

    def test_resolved_case_is_not_reopened(self):
        self.agent.process([observation("obs-1", 90.0)])
        first_case_id = self.store.list_cases()[0].case_id
        self.store.connection.execute("UPDATE cases SET status=? WHERE case_id=?", (CaseStatus.RESOLVED.value, first_case_id))
        self.store.connection.commit()

        self.agent.process([observation("obs-2", 85.0)])
        open_cases = self.store.list_cases(CaseStatus.OPEN)
        self.assertEqual(len(open_cases), 1)
        self.assertNotEqual(open_cases[0].case_id, first_case_id)

    def test_list_cases_filters_by_status(self):
        self.agent.process([observation("obs-1", 90.0)])
        self.assertEqual(len(self.store.list_cases(CaseStatus.OPEN)), 1)
        self.assertEqual(len(self.store.list_cases(CaseStatus.CLOSED)), 0)

    def test_decision_is_recorded_for_every_observation(self):
        decisions = self.agent.process([
            observation("obs-1", 90.0),    # breach
            observation("obs-2", 130.0),   # clear
            observation("obs-3", 90.0, merchant="shop-9"),
        ])
        self.assertEqual([result.decision for _, result, _ in decisions], ["breach", "clear", "breach"])


if __name__ == "__main__":
    unittest.main()
