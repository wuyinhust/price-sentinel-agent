"""Command-line entry point.

Mirrors the ergonomics the previous harness had — notably a `run` mode that
executes a Python script with the full API pre-imported, so a whole sub-task
batches into one invocation instead of one call per step (each tree dump costs
seconds, so batching matters).

    android-harness run <<'PY'
    s = connect()
    s.wake()
    print(s.labels())
    PY
"""

from __future__ import annotations

import argparse
import json
import sys

from . import mirror
from .adb import Adb, AdbError, AdbNotFound, find_adb, find_scrcpy, find_scrcpy_icons, find_scrcpy_server
from .input import (
    InputBlocked,
    Unsupported,
    current_focus,
    launched_packages,
    launch,
    probe_input,
    probe_settings_write,
)
from .screen import display_state, screenshot, sleep, wake
from .tree import UiDumpError, all_labels, dump_tree, summarize, tap_label
from .verify import diff, wait_stable


def _session(args):
    from . import connect

    return connect(getattr(args, "serial", None))


def _cmd_doctor(args) -> int:
    print("== binaries ==")
    try:
        adb_path = find_adb()
        print(f"  adb      {adb_path}")
        print(f"           {Adb(path=adb_path).version()}")
    except AdbNotFound as error:
        print(f"  adb      MISSING — {error}")

    scrcpy_path = find_scrcpy()
    if scrcpy_path:
        print(f"  scrcpy   {scrcpy_path}")
        print(f"           {mirror.version(scrcpy_path)}")
        server = find_scrcpy_server(scrcpy_path)
        print(f"  server   {server}" if server else "  server   MISSING — scrcpy cannot start")
        icons = find_scrcpy_icons(scrcpy_path)
        if icons:
            print(f"  icons    {icons}")
    else:
        print("  scrcpy   MISSING (mirroring unavailable; adb path still works)")

    print("\n== devices ==")
    try:
        adb = Adb()
    except AdbNotFound as error:
        print(f"  {error}")
        return 1
    devices = adb.devices()
    if not devices:
        print("  none attached")
        print("  USB: Developer options -> USB debugging, then tap Allow on the phone")
        print("  Wi-Fi: Developer options -> Wireless debugging -> pair")
        return 1
    for device in devices:
        print(f"  {device.describe()}")

    usable = [device for device in devices if device.usable]
    if not usable:
        states = {device.state for device in devices}
        if "unauthorized" in states:
            print("\n  fix: unlock the phone and tap Allow on the USB debugging prompt")
        elif "offline" in states:
            print("\n  fix: replug the cable, or restart the adb server")
        return 1

    print("\n== selected device ==")
    resolved = adb.resolve()
    session_adb = resolved
    identify = {
        "serial": session_adb.serial,
        "model": session_adb.prop("ro.product.model"),
        "android": session_adb.prop("ro.build.version.release"),
        "sdk": session_adb.prop("ro.build.version.sdk"),
        "abi": session_adb.prop("ro.product.cpu.abi"),
    }
    for key, value in identify.items():
        print(f"  {key:9} {value}")

    width, height = session_adb.display_size()
    print(f"  display   {width}x{height} @ {session_adb.display_density()}dpi")

    print("\n== screen ==")
    state = display_state(session_adb)
    print(f"  {state.describe()}")
    if state.locked:
        print("  note: locked — taps and the tree will not work until the user unlocks")
    if not state.screen_on:
        print("  note: screen off — run `android-harness wake` (a sleeping device still answers adb)")

    print("\n== input injection ==")
    allowed, reason = probe_input(session_adb)
    if allowed:
        print("  ok — taps, swipes, keys and ASCII typing are available")
    else:
        print(f"  BLOCKED — {reason}")

    print("\n== setting writes ==")
    writable, why = probe_settings_write(session_adb)
    if writable:
        print("  ok — `settings put` is accepted (probed by writing a value back to itself)")
    else:
        print(f"  BLOCKED — {why}")
        print("  note: changing screen timeout, rotation lock etc. from here will not work.")

    print("\n== accessibility tree ==")
    try:
        tree = dump_tree(session_adb, attempts=2)
        clickable = [node for node in tree.walk() if node.visible and node.clickable and node.label]
        print(f"  readable, {len(clickable)} clickable labelled nodes")
        for line in summarize(tree, clickable_only=True, limit=5):
            print(f"    {line}")
        if not clickable and (state.locked or not state.screen_on):
            print("  (empty because the screen is off or locked, not because reads are broken)")
    except UiDumpError as error:
        print(f"  NOT readable — {error}")
        return 1

    print("\ndoctor: ok")
    return 0


def _cmd_devices(args) -> int:
    try:
        adb = Adb()
    except AdbNotFound as error:
        print(error, file=sys.stderr)
        return 1
    devices = adb.devices()
    if not devices:
        print("no devices attached")
        return 1
    for device in devices:
        print(device.describe())
    return 0


def _cmd_tree(args) -> int:
    session = _session(args)
    tree = dump_tree(session.adb)
    lines = summarize(tree, clickable_only=args.clickable, limit=args.limit)
    if args.json:
        payload = [
            {
                "text": node.text,
                "resource_id": node.resource_id,
                "content_desc": node.content_desc,
                "class": node.class_name,
                "package": node.package,
                "clickable": node.clickable,
                "bounds": list(node.bounds),
                "center": list(node.center),
            }
            for node in tree.walk()
            if node.visible and (node.text or node.content_desc or node.resource_id)
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for line in lines:
        print(line)
    return 0


def _cmd_labels(args) -> int:
    session = _session(args)
    for label in all_labels(session.tree(), clickable_only=not args.all):
        print(label)
    return 0


def _cmd_state(args) -> int:
    session = _session(args)
    print(display_state(session.adb).describe())
    focus = current_focus(session.adb)
    if focus:
        print(f"focus: {focus[0]}/{focus[1]}")
    return 0


def _cmd_wake(args) -> int:
    session = _session(args)
    print(wake(session.adb).describe())
    return 0


def _cmd_sleep(args) -> int:
    session = _session(args)
    sleep(session.adb)
    print("screen off")
    return 0


def _cmd_tap(args) -> int:
    session = _session(args)
    session.tap(args.x, args.y)
    print(f"tapped {args.x},{args.y}")
    return 0


def _cmd_tap_text(args) -> int:
    session = _session(args)
    node = tap_label(session.adb, args.label, exact=args.exact, timeout=args.timeout)
    print(f"tapped {node.label!r} at {node.tap_target().center}")
    return 0


def _cmd_key(args) -> int:
    session = _session(args)
    session.key(args.name)
    print(f"key {args.name}")
    return 0


def _cmd_text(args) -> int:
    session = _session(args)
    session.type_text(args.value)
    print(f"typed {args.value!r}")
    return 0


def _cmd_open(args) -> int:
    session = _session(args)
    package = launch(session.adb, args.package)
    print(f"launched {package}")
    return 0


def _cmd_packages(args) -> int:
    session = _session(args)
    packages = sorted(launched_packages(session.adb))
    if args.match:
        packages = [package for package in packages if args.match in package]
    for package in packages:
        print(package)
    return 0


def _cmd_shot(args) -> int:
    session = _session(args)
    path = screenshot(session.adb, args.out)
    print(f"{path} ({path.stat().st_size} bytes)")
    return 0


def _resolve_input_mode(args, adb: Adb) -> tuple[str | None, str | None, list[str]]:
    """Pick scrcpy's input backend, probing when the caller did not say.

    `sdk` is scrcpy's default and gives the better mouse (absolute positioning),
    but a device that refuses injected input refuses it too — the window renders
    and the mouse silently does nothing. One no-op keyevent settles which kind of
    device this is, which is far cheaper than debugging a dead-looking window.

    Returns the mouse backend, the keyboard backend, and any lines worth telling
    the user. Returning rather than printing keeps this testable.
    """
    if args.input != "auto":
        return args.input, args.input, []
    allowed, _reason = probe_input(adb)
    if allowed:
        return None, None, []
    return "uhid", "uhid", [
        "note: this device refuses injected input, so scrcpy's default (sdk) input would be dead.",
        "      falling back to UHID: a virtual HID mouse/keyboard the framework check never sees.",
        "      the mouse is relative — LAlt (or Cmd) releases it back to the computer.",
    ]


def _cmd_mirror(args) -> int:
    from . import connect

    session = connect(getattr(args, "serial", None))
    mouse, keyboard, notes = _resolve_input_mode(args, session.adb)

    # `--stay-awake` writes a global setting, so it depends on the settings-write
    # capability rather than on the input backend. Those two happen to share an
    # OEM toggle on MIUI, but gating on the backend would be a proxy; this asks
    # the actual question. A refused write surfaces as a Java stack trace on
    # every launch, which is noise, not information.
    stay_awake = not args.no_stay_awake
    if stay_awake:
        writable, _why = probe_settings_write(session.adb)
        if not writable:
            stay_awake = False
            notes = [*notes, "note: --stay-awake skipped — this device refuses setting writes."]
    for line in notes:
        print(line)

    options = mirror.MirrorOptions(
        max_size=args.max_size,
        no_audio=not args.audio,
        stay_awake=stay_awake,
        turn_screen_off=args.turn_screen_off,
        window_title=args.title,
        always_on_top=args.on_top,
        mouse=mouse,
        keyboard=keyboard,
    )
    process = mirror.start(session.adb, options, background=not args.foreground)
    if process is None:
        return 0
    print(f"mirror started (pid {process.pid}); ctrl-c to stop")
    try:
        process.wait()
    except KeyboardInterrupt:
        mirror.terminate(process)
    return 0


def _cmd_run(args) -> int:
    """Execute a Python script with the harness API pre-imported."""
    source = sys.stdin.read() if args.script in (None, "-") else open(args.script, encoding="utf-8").read()
    from . import connect

    session = connect(getattr(args, "serial", None))
    namespace: dict[str, object] = {"__name__": "__main__", "s": session, "phone": session}
    namespace.update(vars(__import__("androidharness", fromlist=["*"])))
    exec(compile(source, "<android-harness run>", "exec"), namespace)
    return 0


def _cmd_wait(args) -> int:
    session = _session(args)
    before = session.tree()
    after = wait_stable(session.adb, timeout=args.timeout)
    changes = diff(before, after)
    print(f"appeared:    {changes['appeared'][:15]}")
    print(f"disappeared: {changes['disappeared'][:15]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="android-harness",
        description="Control an Android device over adb; mirror it with scrcpy.",
    )
    parser.add_argument("--serial", help="target device serial (default: the only attached device)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check binaries, device, screen and tree readability").set_defaults(func=_cmd_doctor)
    sub.add_parser("devices", help="list attached devices").set_defaults(func=_cmd_devices)
    sub.add_parser("state", help="screen / lock / focus state").set_defaults(func=_cmd_state)
    sub.add_parser("wake", help="turn the screen on (never unlocks)").set_defaults(func=_cmd_wake)
    sub.add_parser("sleep", help="turn the screen off").set_defaults(func=_cmd_sleep)
    sub.add_parser("wait", help="wait for the screen to settle and report what changed").set_defaults(func=_cmd_wait)

    tree = sub.add_parser("tree", help="print the accessibility tree")
    tree.add_argument("--clickable", action="store_true", help="only clickable nodes")
    tree.add_argument("--limit", type=int, default=None)
    tree.add_argument("--json", action="store_true")
    tree.set_defaults(func=_cmd_tree)

    labels = sub.add_parser("labels", help="list labels you can act on")
    labels.add_argument("--all", action="store_true", help="include non-clickable nodes")
    labels.set_defaults(func=_cmd_labels)

    tap = sub.add_parser("tap", help="tap coordinates")
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    tap.set_defaults(func=_cmd_tap)

    tap_text = sub.add_parser("tap-text", help="tap an element by its label")
    tap_text.add_argument("label")
    tap_text.add_argument("--exact", action="store_true")
    tap_text.add_argument("--timeout", type=float, default=0.0)
    tap_text.set_defaults(func=_cmd_tap_text)

    key = sub.add_parser("key", help="send a key (back/home/enter/...)")
    key.add_argument("name")
    key.set_defaults(func=_cmd_key)

    text = sub.add_parser("text", help="type ASCII text into the focused field")
    text.add_argument("value")
    text.set_defaults(func=_cmd_text)

    open_app = sub.add_parser("open", help="launch an app by name or package")
    open_app.add_argument("package")
    open_app.set_defaults(func=_cmd_open)

    packages = sub.add_parser("packages", help="list installed packages")
    packages.add_argument("--match", help="substring filter")
    packages.set_defaults(func=_cmd_packages)

    shot = sub.add_parser("shot", help="capture a screenshot")
    shot.add_argument("--out", default="screenshot.png")
    shot.set_defaults(func=_cmd_shot)

    mirror_parser = sub.add_parser("mirror", help="open the scrcpy mirror window (for humans)")
    mirror_parser.add_argument("--max-size", type=int, default=1024)
    mirror_parser.add_argument("--audio", action="store_true", help="forward device audio")
    mirror_parser.add_argument("--no-stay-awake", action="store_true")
    mirror_parser.add_argument("--turn-screen-off", action="store_true")
    mirror_parser.add_argument("--title", default=None)
    mirror_parser.add_argument("--on-top", action="store_true")
    mirror_parser.add_argument("--foreground", action="store_true")
    mirror_parser.add_argument(
        "--input",
        choices=("auto", "sdk", "uhid", "aoa"),
        default="auto",
        help="input backend; 'auto' probes the device and falls back to uhid when "
        "injected input is refused (MIUI without 'USB debugging (Security settings)')",
    )
    mirror_parser.set_defaults(func=_cmd_mirror)

    run = sub.add_parser("run", help="run a Python script with the API pre-imported")
    run.add_argument("script", nargs="?", default="-", help="path, or - for stdin")
    run.set_defaults(func=_cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (AdbError, AdbNotFound, UiDumpError, Unsupported, FileNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
