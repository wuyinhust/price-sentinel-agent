from __future__ import annotations

import argparse
import json
import sys

from .agent import PriceSentinelAgent
from .models import PriceObservation, PricePolicy, Product
from .store import SQLiteStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="price-sentinel")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db", help="create the database schema")
    init.add_argument("path")
    ingest = sub.add_parser("ingest", help="ingest a JSON document with products, policies and observations")
    ingest.add_argument("path")
    ingest.add_argument("input", help="JSON file path, or - for stdin")
    cases = sub.add_parser("cases", help="list breach cases")
    cases.add_argument("path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init-db":
        SQLiteStore(args.path).connection.close()
        print(f"initialized {args.path}")
        return 0
    if args.command == "cases":
        store = SQLiteStore(args.path)
        for case in store.list_cases():
            print(json.dumps({"case_id": case.case_id, "product_id": case.product_id, "merchant": case.merchant, "status": case.status.value, "severity": case.severity.value}, ensure_ascii=False))
        return 0

    store = SQLiteStore(args.path)
    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    payload = json.loads(raw)
    for item in payload.get("products", []):
        store.upsert_product(Product(**item))
    for item in payload.get("policies", []):
        store.upsert_policy(PricePolicy(**item))
    observations = [PriceObservation(**item) for item in payload.get("observations", [])]
    decisions = PriceSentinelAgent(store).process(observations)
    print(json.dumps([{"observation_id": o.observation_id, "decision": r.decision, "severity": r.severity.value if r.severity else None, "case_id": c.case_id if c else None} for o, r, c in decisions], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
