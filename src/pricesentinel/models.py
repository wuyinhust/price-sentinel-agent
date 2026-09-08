from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CaseStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    IN_REMEDIATION = "in_remediation"
    RESOLVED = "resolved"
    CLOSED = "closed"
    DISMISSED = "dismissed"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class Product:
    product_id: str
    brand: str
    name: str
    sku: str | None = None
    vintage: str | None = None
    unit_volume_ml: int | None = None
    units_per_pack: int = 1


@dataclass(frozen=True, slots=True)
class PricePolicy:
    product_id: str
    package_type: str
    floor_price: float
    effective_from: str
    effective_to: str | None = None
    currency: str = "CNY"
    channel_scope: str = "*"


@dataclass(frozen=True, slots=True)
class PriceObservation:
    observation_id: str
    product_id: str
    package_type: str
    channel: str
    merchant: str
    observed_at: str
    listed_price: float | None
    net_price: float | None = None
    currency: str = "CNY"
    listing_url: str | None = None
    evidence_url: str | None = None
    source: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def effective_price(self) -> float | None:
        return self.net_price if self.net_price is not None else self.listed_price


@dataclass(frozen=True, slots=True)
class BreachResult:
    decision: str
    severity: Severity | None
    effective_price: float | None
    floor_price: float | None
    delta: float | None
    delta_pct: float | None
    reason: str

    @property
    def is_breach(self) -> bool:
        return self.decision == "breach"


@dataclass(frozen=True, slots=True)
class BreachCase:
    case_id: str
    product_id: str
    package_type: str
    channel: str
    merchant: str
    first_detected_at: str
    last_detected_at: str
    latest_observation_id: str
    severity: Severity
    status: CaseStatus = CaseStatus.OPEN
    assigned_to: str | None = None
    sla_due_at: str | None = None
    resolution_reason: str | None = None

