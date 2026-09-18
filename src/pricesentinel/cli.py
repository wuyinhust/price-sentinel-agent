from __future__ import annotations

import argparse
import json
import sys

from .agent import PriceSentinelAgent
from .collect.channels import CHANNELS
from .collect.device import AndroidDevice, DeviceError
from .collect.runner import MonitorConfig, run_monitor
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

    check = sub.add_parser(
        "check-config",
        help="validate a monitor config without touching a device",
    )
    check.add_argument("config")

    collect = sub.add_parser(
        "collect",
        help="drive a phone over adb, read prices, and open cases for breaches",
    )
    collect.add_argument("path", help="database path")
    collect.add_argument("config", help="monitor config JSON")
    collect.add_argument(
        "--channel",
        action="append",
        choices=sorted(CHANNELS),
        help="restrict to one channel (repeatable); default is every target",
    )
    collect.add_argument(
        "--dry-run",
        action="store_true",
        help="collect and decide, but write nothing to the database",
    )
    collect.add_argument("--adb", help="path to the adb binary")
    collect.add_argument("--serial", help="device serial, when several are attached")

    return parser


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _run_check_config(config_path: str) -> int:
    config = MonitorConfig.load(config_path)
    problems = config.validate()
    print(f"monitor  {config.monitor_id}")
    print(f"targets  {len(config.targets)}")
    print(f"products {len(config.products)}   policies {len(config.policies)}")
    for target in config.targets:
        channel = CHANNELS.get(target.channel)
        mark = " " if (channel and channel.verified) else "!"
        label = channel.label if channel else target.channel
        print(
            f"  {mark} {label:<6} {target.product_id:<20} keyword={target.keyword!r} "
            f"match={target.merge_terms()}"
        )
    if problems:
        print("\nproblems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nconfig ok")
    return 0


def _run_collect(args) -> int:
    config = MonitorConfig.load(args.config)
    problems = config.validate()
    if problems:
        # Refuse rather than run: a monitor missing its floor policies would
        # collect perfect evidence and then never raise a case, which reads as
        # "all clear" while nothing is actually being enforced.
        print("refusing to run, the config has problems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    try:
        device = AndroidDevice(adb_path=args.adb, serial=args.serial)
    except DeviceError as error:
        print(f"device unavailable: {error}", file=sys.stderr)
        return 2
    print(f"device  {device.serial}  (adb {device.adb})")
    print(f"monitor {config.monitor_id}  targets={len(config.targets)}")

    run = run_monitor(config, device, channels=tuple(args.channel) if args.channel else None)
    print()
    print(run.describe())
    print()

    observations = run.observations
    print(f"observations collected: {len(observations)}")
    for observation in observations:
        title = (observation.metadata or {}).get("title", "")
        price = observation.effective_price
        print(f"  ¥{price} {observation.merchant} :: {title[:52]}")

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    with SQLiteStore(args.path) as store:
        for product in config.products:
            store.upsert_product(product)
        for policy in config.policies:
            store.upsert_policy(policy)
        decisions = PriceSentinelAgent(store).process(observations) if observations else []

    print()
    breaches = 0
    for observation, result, case in decisions:
        if result.decision == "breach":
            breaches += 1
            print(
                f"  BREACH {result.severity.value:<8} "
                f"¥{result.effective_price} vs floor ¥{result.floor_price} "
                f"({result.delta_ratio * 100:+.2f}%)  case={case.case_id if case else '-'} "
                f"{observation.merchant}"
            )
        else:
            print(f"  {result.decision:<12} ¥{result.effective_price} :: {result.reason}")
    print(f"\n{breaches} breach(es) of {len(decisions)} observation(s)")

    # A run that collected nothing at all is a failure of the watch, not a
    # clean bill of health, so it exits non-zero for cron and CI to notice.
    if not observations:
        print("WARNING: no observations collected; the monitor is not actually watching", file=sys.stderr)
        return 3
    return 1 if breaches else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "init-db":
        with SQLiteStore(args.path):
            print(f"initialized {args.path}")
        return 0

    if args.command == "cases":
        with SQLiteStore(args.path) as store:
            cases = store.list_cases()
        for case in cases:
            print(json.dumps({"case_id": case.case_id, "product_id": case.product_id, "merchant": case.merchant, "status": case.status.value, "severity": case.severity.value}, ensure_ascii=False))
        return 0

    if args.command == "check-config":
        return _run_check_config(args.config)

    if args.command == "collect":
        return _run_collect(args)

    payload = json.loads(_read_input(args.input))
    with SQLiteStore(args.path) as store:
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
