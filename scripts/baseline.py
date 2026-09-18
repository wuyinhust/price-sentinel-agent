"""Show the price range each watched product has actually quoted so far.

Usage: python scripts/baseline.py [watch.sqlite3]

This exists because floors are set *from* data, not before it. A monitor with
invented floor prices raises cases that are not real, and the fastest way to
lose trust in a price watch is to have it cry wolf on day one. So policies start
empty (baseline mode) and the floors get filled in once a few days of real
quotes show what "normal" looks like.

Two things the summary deliberately keeps apart:

* **Tree vs OCR prices.** A JD price is read from the accessibility tree; a
  Taobao price went through OCR, where a digit can be misread. They are not the
  same kind of evidence and are summarised separately, so an OCR outlier cannot
  quietly widen the range a floor is derived from.
* **Spread, not just min/max.** A min and a max say nothing about whether the
  middle is stable, and a floor has to survive ordinary noise. The quartiles are
  what tell you whether a floor can be set at all yet.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict


def _quantile(values: list[float], fraction: float) -> float:
    """Linear-interpolated quantile of a sorted list."""
    if len(values) == 1:
        return values[0]
    position = fraction * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else "watch.sqlite3"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row

    rows = list(
        connection.execute(
            """SELECT product_id, channel, package_type, merchant, listed_price,
                      net_price, observed_at, metadata_json
               FROM observations
               WHERE listed_price IS NOT NULL
               ORDER BY product_id, channel, listed_price"""
        )
    )
    if not rows:
        print(f"{path}: no observations yet -- run `make collect` first")
        return 1

    days = sorted({row["observed_at"][:10] for row in rows})
    print(f"database: {path}")
    print(f"observations: {len(rows)}   days covered: {len(days)}  ({days[0]} .. {days[-1]})")

    if len(days) < 3:
        print()
        print("!! Fewer than three days of quotes: enough to sanity-check the")
        print("   collector, not enough to set a floor from. Anything set now is")
        print("   still a guess wearing a number.")

    grouped: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[(row["product_id"], row["channel"])].append(row)

    for (product_id, channel), items in sorted(grouped.items()):
        by_source: dict[str, list[float]] = defaultdict(list)
        for row in items:
            metadata = json.loads(row["metadata_json"] or "{}")
            by_source[metadata.get("extraction") or "unknown"].append(row["listed_price"])

        print(f"\n== {product_id} / {channel} ==")
        print(f"   offers seen: {len(items)} across {len({i['merchant'] for i in items})} shop(s)")
        for source, prices in sorted(by_source.items()):
            prices = sorted(prices)
            # 可信度不同,分开看: tree 是读出的事实, screenshot 是 OCR 的线索。
            # "unknown" 是加 extraction 字段之前入库的老行,既不该当 OCR 贬低,
            # 也不该当 tree 抬高 —— 说清楚是什么就行,别猜。
            if source == "screenshot":
                caveat = "   <- OCR: a digit can be misread, confirm before trusting"
            elif source == "unknown":
                caveat = "   <- no extraction recorded (row predates the field)"
            else:
                caveat = ""
            print(
                f"   [{source}] n={len(prices)}  "
                f"min ¥{prices[0]:.2f}  q1 ¥{_quantile(prices, 0.25):.2f}  "
                f"median ¥{_quantile(prices, 0.5):.2f}  q3 ¥{_quantile(prices, 0.75):.2f}  "
                f"max ¥{prices[-1]:.2f}{caveat}"
            )

        cheapest = min(items, key=lambda row: row["listed_price"])
        print(f"   lowest so far: ¥{cheapest['listed_price']:.2f}  ({cheapest['merchant']})")

    print()
    print("A floor has to sit below the honest low end, not at the minimum ever")
    print("seen: a single promotional dip would otherwise make every later")
    print("ordinary price look like a breach.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
