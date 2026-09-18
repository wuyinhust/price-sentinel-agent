"""Inspect a PriceSentinel SQLite database directly.

Usage: python scripts/show.py [prices.sqlite3]
"""

from __future__ import annotations

import sqlite3
import sys


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else "prices.sqlite3"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    print(f"database: {path}\n")

    print("== 行数 ==")
    for table in ("products", "policies", "observations", "cases", "case_events"):
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:<14} {count}")

    print("\n== 案件 ==")
    for row in connection.execute(
        "SELECT case_id, product_id, package_type, channel, merchant, severity, status,"
        " first_detected_at, last_detected_at FROM cases ORDER BY last_detected_at DESC"
    ):
        print(
            f"  {row['case_id'][:8]}  {row['product_id']:<5} {row['package_type']:<7} "
            f"{row['channel']:<14} {row['merchant']:<8} {row['severity']:<9} {row['status']:<7} "
            f"{row['first_detected_at']} -> {row['last_detected_at']}"
        )

    print("\n== 审计事件 ==")
    for row in connection.execute(
        "SELECT case_id, event_type, occurred_at, actor, payload_json FROM case_events ORDER BY occurred_at"
    ):
        print(f"  {row['case_id'][:8]}  {row['event_type']:<16} {row['occurred_at']}  {row['actor']}  {row['payload_json']}")

    connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
