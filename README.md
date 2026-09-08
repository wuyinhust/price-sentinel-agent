# PriceSentinel Agent

PriceSentinel is an auditable, enterprise-oriented agent for cross-channel price monitoring. It turns price observations into deterministic breach decisions and trackable cases.

The product boundary is intentional:

- **Agent** orchestrates ingestion, policy lookup, evaluation, case creation and notifications.
- **Skills/adapters** provide platform-specific collection, promotion normalization, reporting and ticket integrations.
- **Database** is the source of truth for products, policies, observations, cases and audit events.

The breach decision does not depend on an LLM. An upstream connector should normalize coupons, membership discounts and bundle allocation into `net_price`; the rule engine then compares the effective price with the policy floor.

## Quick start

```bash
cd price-sentinel-agent
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m pricesentinel.cli init-db prices.sqlite3
```

Ingest a JSON payload:

```json
{
  "products": [
    {"product_id": "p-1", "brand": "Demo", "name": "Demo product", "sku": "SKU-1"}
  ],
  "policies": [
    {"product_id": "p-1", "package_type": "single", "floor_price": 100, "effective_from": "2026-01-01"}
  ],
  "observations": [
    {"observation_id": "obs-1", "product_id": "p-1", "package_type": "single", "channel": "marketplace-a", "merchant": "shop-1", "observed_at": "2026-09-08T00:00:00+00:00", "listed_price": 110, "net_price": 90, "listing_url": "https://example.com/item"}
  ]
}
```

```bash
PYTHONPATH=src python -m pricesentinel.cli ingest prices.sqlite3 payload.json
PYTHONPATH=src python -m pricesentinel.cli cases prices.sqlite3
```

## Core data model

`products` and `policies` define the controlled master data. `observations` is append-only monitoring evidence. `cases` is the operational queue, and `case_events` preserves the audit trail.

An open case is keyed by product, package, channel and merchant. Repeated breaches update the same active case instead of creating duplicate tickets.

## Next integrations

1. Add source adapters for approved APIs, browser collection or customer-uploaded files.
2. Add promotion and bundle normalization with explicit evidence and exceptions.
3. Replace SQLite with Postgres for multi-tenant deployments.
4. Add approval-aware notification and ticket connectors.
5. Add dashboards for breach rate, remediation SLA, recurrence and price dispersion.

## Status

This repository is an executable reference core, not a production crawler. Platform access, credentials, customer-specific policies and notification destinations must be configured by each deployment.
