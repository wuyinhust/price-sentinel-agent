"""Input injection over `adb shell input`.

Two things this module refuses to do silently, because both produce failures
that look like a broken UI rather than a broken call:

1. Typing text that `input text` cannot represent. The command is ASCII-only;
   non-ASCII (Chinese, emoji) is silently dropped by the device. We raise
   instead, and point at the standard workaround.
2. Targeting a device whose screen is off, where taps land nowhere.

`adb` reports nothing about the outcome of an input event — a tap on empty
space "succeeds" — so verification belongs in verify.py, not here.
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path

from .adb import Adb, AdbError

# Human-readable key names mapped to Android KEYCODE_* values.
KEYCODES: dict[str, int] = {
    "home": 3,
    "back": 4,
    "call": 5,
    "end_call": 6,
    "up": 19,
    "down": 20,
    "left": 21,
    "right": 22,
    "center": 23,
    "enter": 23,
    "volume_up": 24,
    "volume_down": 25,
    "power": 26,
    "camera": 27,
    "clear": 28,
    "tab": 61,
    "space": 62,
    "escape": 111,
    "page_up": 92,
    "page_down": 93,
    "forward_del": 112,
    "move_home": 122,
    "move_end": 123,
    "delete": 67,
    "backspace": 67,
    "menu": 82,
    "app_switch": 187,
    "recents": 187,
    "wakeup": 224,
    "sleep": 223,
    "play_pause": 85,
    "media_next": 87,
    "media_previous": 88,
    "notification": 83,
}

# Characters the device-side shell would interpret. Anything outside this set is
# passed through untouched.
_SHELL_METACHARACTERS = re.compile(r"[^\w@%+=:,./-]")

_FOCUS_PATTERNS = (
    re.compile(r"mCurrentFocus=Window\{[^}]*?\s+(\S+)/(\S+)\}"),
    re.compile(r"mFocusedApp=.*?\s+(\S+)/(\S+)\s"),
    re.compile(r"(?:topResumedActivity|mResumedActivity)[^}]*?\s+(\S+)/(\S+)\b"),
)


class Unsupported(RuntimeError):
    """The device cannot perform the requested action as specified."""


class InputBlocked(Unsupported):
    """The device refuses shell-injected input events."""


# MIUI (and a few other OEM builds) gate `input` behind an extra developer
# toggle. The denial surfaces as a Java SecurityException, not an adb failure,
# so it looks like a broken harness rather than a phone setting.
_INJECTION_DENIED_MARKERS = (
    "INJECT_EVENTS",
    "Injecting input events requires",
)

MIUI_INPUT_HINT = (
    "this device refuses adb input injection. On MIUI/HyperOS, enable "
    "Developer options -> 'USB debugging (Security settings)' "
    "(USB调试(安全设置)); the plain 'USB debugging' toggle is not enough. "
    "MIUI may require a signed-in Xiaomi account and an inserted SIM for that "
    "toggle. Reads (tree, screenshot, dumpsys) work without it — only taps, "
    "swipes, keys and typing are blocked."
)

# Setting writes fail on the same OEM builds, and it is tempting to read that as
# a second, unrelated restriction. It is not: flipping the input toggle restores
# both at once. Worth probing separately anyway, because the failure is silent
# from the caller's perspective -- a refused `settings put` leaves the old value
# in place and returns before the script notices.
_SETTINGS_DENIED_MARKERS = (
    "WRITE_SETTINGS",
    "WRITE_SECURE_SETTINGS",
    "not granted this permission",
)

MIUI_SETTINGS_HINT = (
    "this device refuses setting writes from the shell. On MIUI/HyperOS this is "
    "the SAME gate as input injection, not a second one: enable Developer "
    "options -> 'USB debugging (Security settings)' (USB调试(安全设置)) and both "
    "recover together. There is nothing else to turn on."
)

# Read-back key for the probe. `screen_off_timeout` is writable everywhere and
# its current value is a safe thing to write straight back.
_SETTINGS_PROBE_TABLE = "system"
_SETTINGS_PROBE_KEY = "screen_off_timeout"


def probe_settings_write(adb: Adb) -> tuple[bool, str]:
    """Test whether the shell may write settings, without changing any.

    Reads a harmless key and writes its current value back, so the probe has no
    observable effect on the device: an accepted write changes nothing, and a
    refused one leaves the value untouched.

    This is a distinct capability from `probe_input` even though the same OEM
    toggle governs both — "taps work but settings are silently ignored" is a
    state worth being able to detect on its own.
    """
    try:
        current = adb.shell("settings", "get", _SETTINGS_PROBE_TABLE, _SETTINGS_PROBE_KEY, timeout=15.0).strip()
    except AdbError as error:
        return False, str(error)
    if current in ("", "null"):
        return False, f"{_SETTINGS_PROBE_TABLE}.{_SETTINGS_PROBE_KEY} is unset; not writing a guess"
    try:
        adb.shell("settings", "put", _SETTINGS_PROBE_TABLE, _SETTINGS_PROBE_KEY, current, timeout=15.0)
    except AdbError as error:
        if any(marker in str(error) for marker in _SETTINGS_DENIED_MARKERS):
            return False, MIUI_SETTINGS_HINT
        return False, str(error)
    return True, ""


def _injection_denied(error: Exception) -> bool:
    message = str(error)
    return any(marker in message for marker in _INJECTION_DENIED_MARKERS)


def _inject(adb: Adb, *args: str) -> None:
    """Run an `input` subcommand, translating a permission refusal.

    A raw SecurityException traceback is indistinguishable from a crash, so it
    is converted into an error that names the actual cause and the fix.
    """
    try:
        adb.shell("input", *args)
    except AdbError as error:
        if _injection_denied(error):
            raise InputBlocked(f"input {args[0]} was refused: {MIUI_INPUT_HINT}") from error
        raise


def probe_input(adb: Adb) -> tuple[bool, str]:
    """Test whether shell-injected input is accepted at all.

    Sends KEYCODE_UNKNOWN (0), which the framework discards, so the permission
    gate is exercised without disturbing whatever is on screen. Run this before
    a task that depends on tapping, so a blocked device is discovered up front
    rather than after a silent no-op.
    """
    try:
        adb.shell("input", "keyevent", "0", timeout=15.0)
    except AdbError as error:
        if _injection_denied(error):
            return False, MIUI_INPUT_HINT
        return False, str(error)
    return True, ""


def _quote_for_device_shell(text: str) -> str:
    return shlex.quote(text) if _SHELL_METACHARACTERS.search(text) else text


def tap(adb: Adb, x: int, y: int) -> None:
    _inject(adb, "tap", str(int(x)), str(int(y)))


def long_press(adb: Adb, x: int, y: int, *, duration_ms: int = 800) -> None:
    """A long press is a swipe that does not move, held past the threshold."""
    _inject(adb, "swipe", str(int(x)), str(int(y)), str(int(x)), str(int(y)), str(int(duration_ms)))


def swipe(adb: Adb, x1: int, y1: int, x2: int, y2: int, *, duration_ms: int = 300) -> None:
    _inject(adb, "swipe", str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(duration_ms)))


def swipe_direction(
    adb: Adb,
    direction: str,
    *,
    distance_ratio: float = 0.5,
    duration_ms: int = 300,
) -> None:
    """Swipe within the screen, sized relative to the display.

    `direction` is the finger's travel, so "up" scrolls the content down.
    """
    width, height = adb.display_size()
    center_x, center_y = width // 2, height // 2
    if direction in ("up", "down"):
        span = max(1, int(height * distance_ratio) // 2)
        if direction == "up":
            start_y, end_y = center_y + span, center_y - span
        else:
            start_y, end_y = center_y - span, center_y + span
        swipe(adb, center_x, start_y, center_x, end_y, duration_ms=duration_ms)
    elif direction in ("left", "right"):
        span = max(1, int(width * distance_ratio) // 2)
        if direction == "left":
            start_x, end_x = center_x + span, center_x - span
        else:
            start_x, end_x = center_x - span, center_x + span
        swipe(adb, start_x, center_y, end_x, center_y, duration_ms=duration_ms)
    else:
        raise ValueError(f"direction must be up/down/left/right, got {direction!r}")


def type_text(adb: Adb, text: str) -> None:
    """Type into the focused field.

    `input text` is ASCII-only. Non-ASCII is rejected here rather than dropped
    on the device; Chinese input needs an IME that accepts ADB broadcasts
    (e.g. ADBKeyboard) or scrcpy with a host-side IME.
    """
    if not text:
        return
    offenders = sorted({character for character in text if ord(character) > 0x7F})
    if offenders:
        raise Unsupported(
            "`input text` is ASCII-only and would silently drop "
            f"{''.join(offenders)[:20]!r}. For CJK text install an ADB-broadcast "
            "IME (ADBKeyboard) and send it an action broadcast, or type through "
            "scrcpy with a host-side IME."
        )
    # The literal command needs spaces encoded as %s before shell quoting.
    _inject(adb, "text", _quote_for_device_shell(text.replace(" ", "%s")))


def key(adb: Adb, name: str) -> None:
    """Send a single key by name (see KEYCODES) or raw KEYCODE_* value."""
    lowered = name.lower()
    if lowered in KEYCODES:
        code = KEYCODES[lowered]
    elif name.isdigit():
        code = int(name)
    else:
        raise ValueError(f"unknown key {name!r}; known: {', '.join(sorted(KEYCODES))}")
    _inject(adb, "keyevent", str(code))


def back(adb: Adb) -> None:
    key(adb, "back")


def home(adb: Adb) -> None:
    key(adb, "home")


def recents(adb: Adb) -> None:
    key(adb, "app_switch")


# ---------------------------------------------------------------------------
# App lifecycle and focus
# ---------------------------------------------------------------------------


def current_focus(adb: Adb) -> tuple[str, str] | None:
    """Return (package, activity) for the focused window, or None."""
    for command in (
        ("dumpsys", "window", "displays"),
        ("dumpsys", "window"),
        ("dumpsys", "activity", "activities"),
    ):
        output = adb.shell(*command, timeout=25.0, check=False)
        for pattern in _FOCUS_PATTERNS:
            match = pattern.search(output)
            if match:
                return match.group(1), match.group(2)
    return None


def current_package(adb: Adb) -> str | None:
    focus = current_focus(adb)
    return focus[0] if focus else None


def launched_packages(adb: Adb) -> set[str]:
    """Package ids that actually exist on the device.

    `pm list packages` returns lines like `package:com.android.chrome`.
    """
    output = adb.shell("pm", "list", "packages", timeout=30.0)
    return {line.strip().removeprefix("package:") for line in output.splitlines() if line.startswith("package:")}


def _match_package(installed: set[str], name: str) -> str:
    """Resolve a short name or partial id against a known package set."""
    if name in installed:
        return name
    matches = sorted(package for package in installed if package.endswith(f".{name}") or name in package)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise Unsupported(f"no installed package matches {name!r}")
    raise Unsupported(f"{name!r} is ambiguous: {', '.join(matches[:8])}")


def resolve_package(adb: Adb, name: str) -> str:
    """Resolve a short name or partial id to an installed package id.

    `open_app("chrome")` should find `com.android.chrome` without the caller
    knowing the vendor prefix.
    """
    return _match_package(launched_packages(adb), name)


def launch(adb: Adb, package_or_name: str, *, activity: str | None = None, wait: bool = True) -> str:
    """Bring an app to the foreground, returning the resolved package id.

    Uses the monkey launcher rather than `am start -n`, because it resolves the
    correct launcher activity without the caller naming it.
    """
    package = _match_package(launched_packages(adb), package_or_name)
    if activity:
        adb.shell("am", "start", "-n", f"{package}/{activity}", timeout=30.0)
    else:
        adb.shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1", timeout=30.0)
    if wait:
        wait_for_package(adb, package)
    return package


def wait_for_package(adb: Adb, package: str, *, timeout: float = 10.0, interval: float = 0.4) -> bool:
    """Poll the focused window until it is `package`, or the timeout expires."""
    deadline = time.monotonic() + timeout
    while True:
        if current_package(adb) == package:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def install(adb: Adb, apk: str | Path, *, replace: bool = True) -> str:
    args = ["install"]
    if replace:
        args.append("-r")
    args.append(str(apk))
    return adb.run(*args, timeout=300.0)


def pull(adb: Adb, remote: str, local: str | Path) -> Path:
    adb.run("pull", remote, str(local), timeout=300.0)
    return Path(local)


def push(adb: Adb, local: str | Path, remote: str) -> None:
    adb.run("push", str(local), remote, timeout=300.0)
