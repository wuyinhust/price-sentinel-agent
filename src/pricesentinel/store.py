from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from .models import BreachCase, CaseStatus, PriceObservation, PricePolicy, Product, Severity, utc_now


_ACTIVE_STATUSES = tuple(status.value for status in (CaseStatus.OPEN, CaseStatus.ACKNOWLEDGED, CaseStatus.IN_REMEDIATION))


class SQLiteStore:
    """Small reference store; production deployments can replace it with Postgres."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    def init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                product_id TEXT PRIMARY KEY,
                brand TEXT NOT NULL,
                name TEXT NOT NULL,
                sku TEXT,
                vintage TEXT,
                unit_volume_ml INTEGER,
                units_per_pack INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS policies (
                product_id TEXT NOT NULL,
                package_type TEXT NOT NULL,
                floor_price REAL NOT NULL,
                effective_from TEXT NOT NULL,
                effective_to TEXT,
                currency TEXT NOT NULL,
                channel_scope TEXT NOT NULL,
                PRIMARY KEY (product_id, package_type, effective_from),
                FOREIGN KEY (product_id) REFERENCES products(product_id)
            );
            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                package_type TEXT NOT NULL,
                channel TEXT NOT NULL,
                merchant TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                listed_price REAL,
                net_price REAL,
                currency TEXT NOT NULL,
                listing_url TEXT,
                evidence_url TEXT,
                source TEXT,
                metadata_json TEXT,
                FOREIGN KEY (product_id) REFERENCES products(product_id)
            );
            CREATE INDEX IF NOT EXISTS idx_observations_lookup
                ON observations(product_id, package_type, channel, merchant, observed_at);
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                package_type TEXT NOT NULL,
                channel TEXT NOT NULL,
                merchant TEXT NOT NULL,
                first_detected_at TEXT NOT NULL,
                last_detected_at TEXT NOT NULL,
                latest_observation_id TEXT NOT NULL,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_to TEXT,
                sla_due_at TEXT,
                resolution_reason TEXT,
                UNIQUE(product_id, package_type, channel, merchant, status),
                FOREIGN KEY (product_id) REFERENCES products(product_id),
                FOREIGN KEY (latest_observation_id) REFERENCES observations(observation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status, severity, last_detected_at);
            CREATE TABLE IF NOT EXISTS case_events (
                event_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                actor TEXT,
                payload_json TEXT,
                FOREIGN KEY (case_id) REFERENCES cases(case_id)
            );
            """
        )
        self.connection.commit()

    def upsert_product(self, product: Product) -> None:
        self.connection.execute(
            """INSERT INTO products(product_id, brand, name, sku, vintage, unit_volume_ml, units_per_pack)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id) DO UPDATE SET brand=excluded.brand, name=excluded.name,
               sku=excluded.sku, vintage=excluded.vintage, unit_volume_ml=excluded.unit_volume_ml,
               units_per_pack=excluded.units_per_pack""",
            (product.product_id, product.brand, product.name, product.sku, product.vintage, product.unit_volume_ml, product.units_per_pack),
        )
        self.connection.commit()

    def upsert_policy(self, policy: PricePolicy) -> None:
        self.connection.execute(
            """INSERT INTO policies(product_id, package_type, floor_price, effective_from, effective_to, currency, channel_scope)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(product_id, package_type, effective_from) DO UPDATE SET floor_price=excluded.floor_price,
               effective_to=excluded.effective_to, currency=excluded.currency, channel_scope=excluded.channel_scope""",
            (policy.product_id, policy.package_type, policy.floor_price, policy.effective_from, policy.effective_to, policy.currency, policy.channel_scope),
        )
        self.connection.commit()

    def active_policy(self, product_id: str, package_type: str, observed_at: str, channel: str = "*") -> PricePolicy | None:
        row = self.connection.execute(
            """SELECT * FROM policies WHERE product_id=? AND package_type=?
               AND effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)
               AND (channel_scope='*' OR channel_scope=?) ORDER BY effective_from DESC LIMIT 1""",
            (product_id, package_type, observed_at, observed_at, channel),
        ).fetchone()
        if row is None:
            return None
        return PricePolicy(**dict(row))

    def save_observation(self, observation: PriceObservation) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation.observation_id, observation.product_id, observation.package_type,
                observation.channel, observation.merchant, observation.observed_at,
                observation.listed_price, observation.net_price, observation.currency,
                observation.listing_url, observation.evidence_url, observation.source,
                json.dumps(observation.metadata or {}, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def record_breach(self, observation: PriceObservation, severity: Severity) -> BreachCase:
        row = self.connection.execute(
            """SELECT * FROM cases WHERE product_id=? AND package_type=? AND channel=? AND merchant=?
               AND status IN (?, ?, ?) ORDER BY last_detected_at DESC LIMIT 1""",
            (observation.product_id, observation.package_type, observation.channel, observation.merchant, *_ACTIVE_STATUSES),
        ).fetchone()
        if row:
            case_id = row["case_id"]
            self.connection.execute(
                "UPDATE cases SET last_detected_at=?, latest_observation_id=?, severity=? WHERE case_id=?",
                (observation.observed_at, observation.observation_id, _max_severity(row["severity"], severity.value), case_id),
            )
        else:
            case_id = str(uuid.uuid4())
            self.connection.execute(
                """INSERT INTO cases(case_id, product_id, package_type, channel, merchant, first_detected_at,
                   last_detected_at, latest_observation_id, severity, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (case_id, observation.product_id, observation.package_type, observation.channel, observation.merchant,
                 observation.observed_at, observation.observed_at, observation.observation_id, severity.value, CaseStatus.OPEN.value),
            )
        self.connection.execute(
            "INSERT INTO case_events VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), case_id, "breach_detected", utc_now(), "agent", json.dumps({"observation_id": observation.observation_id})),
        )
        self.connection.commit()
        return self.get_case(case_id)

    def get_case(self, case_id: str) -> BreachCase:
        row = self.connection.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(case_id)
        return BreachCase(
            case_id=row["case_id"], product_id=row["product_id"], package_type=row["package_type"],
            channel=row["channel"], merchant=row["merchant"], first_detected_at=row["first_detected_at"],
            last_detected_at=row["last_detected_at"], latest_observation_id=row["latest_observation_id"],
            severity=Severity(row["severity"]), status=CaseStatus(row["status"]), assigned_to=row["assigned_to"],
            sla_due_at=row["sla_due_at"], resolution_reason=row["resolution_reason"],
        )

    def list_cases(self, status: CaseStatus | None = None) -> list[BreachCase]:
        query = "SELECT case_id FROM cases"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status=?"
            params = (status.value,)
        query += " ORDER BY last_detected_at DESC"
        return [self.get_case(row["case_id"]) for row in self.connection.execute(query, params)]


def _max_severity(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    return right if order[right] > order[left] else left
