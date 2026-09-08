from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from .models import BreachCase, BreachResult, PriceObservation
from .rules import evaluate_breach
from .store import SQLiteStore


class Notifier(Protocol):
    def __call__(self, case: BreachCase, result: BreachResult, observation: PriceObservation) -> None: ...


class PriceSentinelAgent:
    """Orchestrates observation persistence, rule evaluation and case creation."""

    def __init__(self, store: SQLiteStore, notifier: Notifier | None = None, *, tolerance: float = 0.0) -> None:
        self.store = store
        self.notifier = notifier
        self.tolerance = tolerance

    def process(self, observations: Iterable[PriceObservation]) -> list[tuple[PriceObservation, BreachResult, BreachCase | None]]:
        decisions: list[tuple[PriceObservation, BreachResult, BreachCase | None]] = []
        for observation in observations:
            self.store.save_observation(observation)
            policy = self.store.active_policy(observation.product_id, observation.package_type, observation.observed_at, observation.channel)
            result = evaluate_breach(observation, policy, tolerance=self.tolerance)
            case = None
            if result.is_breach and result.severity is not None:
                case = self.store.record_breach(observation, result.severity)
                if self.notifier:
                    self.notifier(case, result, observation)
            decisions.append((observation, result, case))
        return decisions
