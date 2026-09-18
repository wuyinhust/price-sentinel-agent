from __future__ import annotations

from decimal import Decimal

from .models import PriceObservation, PricePolicy, BreachResult, Severity


def _severity(delta_ratio: Decimal) -> Severity:
    """Severity from the ratio of the shortfall (``-0.0785`` is 7.85% below floor)."""
    if delta_ratio <= Decimal("-0.20"):
        return Severity.CRITICAL
    if delta_ratio <= Decimal("-0.10"):
        return Severity.HIGH
    if delta_ratio <= Decimal("-0.03"):
        return Severity.MEDIUM
    return Severity.LOW


def evaluate_breach(
    observation: PriceObservation,
    policy: PricePolicy | None,
    *,
    tolerance: float = 0.0,
) -> BreachResult:
    """Evaluate one observation against one effective policy.

    The decision path is deterministic and auditable. Promotions must be
    normalized into ``net_price`` by an upstream connector before evaluation.
    """
    price = observation.effective_price
    if policy is None:
        return BreachResult("unavailable", None, price, None, None, None, "no effective policy")
    if price is None:
        return BreachResult("unavailable", None, None, policy.floor_price, None, None, "missing effective price")
    if observation.currency != policy.currency:
        return BreachResult("unavailable", None, price, policy.floor_price, None, None, "currency mismatch")

    actual = Decimal(str(price))
    floor = Decimal(str(policy.floor_price))
    delta = actual - floor
    delta_ratio = (delta / floor) if floor else Decimal("0")
    breach = actual < floor - Decimal(str(tolerance))
    return BreachResult(
        "breach" if breach else "clear",
        _severity(delta_ratio) if breach else None,
        float(actual),
        float(floor),
        float(delta),
        float(delta_ratio),
        "effective price below floor" if breach else "effective price meets floor",
    )
