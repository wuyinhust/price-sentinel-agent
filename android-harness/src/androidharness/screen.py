"""Screen capture and display power state.

Screen state matters more than it does on iOS: a locked or sleeping Android
still answers `adb shell`, so input silently goes nowhere and screenshots come
back uniformly black. Every one of those cases is detectable, so they are
reported as explicit states rather than left to look like a UI bug.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .adb import Adb

_WAKE_KEYCODE = 224   # KEYCODE_WAKEUP
_SLEEP_KEYCODE = 223  # KEYCODE_SLEEP

# `dumpsys power` prints mWakefulness=Awake|Asleep|Dozing on API 21+.
_WAKEFULNESS_PATTERN = re.compile(r"mWakefulness=(\w+)")

# Keyguard indicators vary by OEM and API level, so several are checked.
_LOCK_INDICATORS = (
    re.compile(r"mShowingLockscreen=true"),
    re.compile(r"mDreamingLockscreen=true"),
    re.compile(r"isStatusBarKeyguard=true"),
    re.compile(r"mKeyguardShowing=true"),
)
_KEYGUARD_FOCUS = re.compile(r"Keyguard", re.IGNORECASE)


class Wakefulness(StrEnum):
    AWAKE = "Awake"
    ASLEEP = "Asleep"
    DOZING = "Dozing"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class DisplayState:
    wakefulness: Wakefulness
    locked: bool
    screen_on: bool

    def describe(self) -> str:
        return (
            f"wakefulness={self.wakefulness} "
            f"screen={'on' if self.screen_on else 'off'} "
            f"lock={'locked' if self.locked else 'unlocked'}"
        )


def wakefulness(adb: Adb) -> Wakefulness:
    output = adb.shell("dumpsys", "power", timeout=20.0)
    for line in output.splitlines():
        match = _WAKEFULNESS_PATTERN.search(line)
        if match:
            try:
                return Wakefulness(match.group(1))
            except ValueError:
                return Wakefulness.UNKNOWN
    return Wakefulness.UNKNOWN


def is_locked(adb: Adb) -> bool:
    """Best-effort keyguard check.

    There is no single stable keyguard API across OEM builds. Two independent
    signals are combined: the explicit flags in `dumpsys window`, and whether
    the current focused window belongs to the keyguard.
    """
    window_dump = adb.shell("dumpsys", "window", timeout=25.0)
    if any(pattern.search(window_dump) for pattern in _LOCK_INDICATORS):
        return True
    for line in window_dump.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            if _KEYGUARD_FOCUS.search(line):
                return True
    return False


def display_state(adb: Adb) -> DisplayState:
    state = wakefulness(adb)
    screen_on = state is Wakefulness.AWAKE
    return DisplayState(wakefulness=state, locked=is_locked(adb), screen_on=screen_on)


def wake(adb: Adb, *, settle: float = 1.0) -> DisplayState:
    """Turn the screen on. Never unlocks — a PIN can only be entered by the user."""
    if wakefulness(adb) is not Wakefulness.AWAKE:
        _send_key(adb, "wakeup")
        time.sleep(settle)
    return display_state(adb)


def sleep(adb: Adb) -> None:
    """Turn the screen back off."""
    _send_key(adb, "sleep")


def stay_awake(adb: Adb, enabled: bool) -> None:
    """Toggle `svc power stayon` while the device is charging.

    This DOES change a device setting, unlike the keyevent-based wake. Always
    pair an enable with a disable, and prefer wake() for one-off actions.
    """
    adb.shell("svc", "power", "stayon", "true" if enabled else "false")


def screenshot_bytes(adb: Adb) -> bytes:
    """Capture the framebuffer as PNG bytes.

    `exec-out` streams the PNG without a device-side file or a pty, so binary
    data survives intact.
    """
    return adb.run_bytes("exec-out", "screencap", "-p", timeout=60.0)


def screenshot(adb: Adb, path: str | Path = "screenshot.png") -> Path:
    destination = Path(path)
    destination.write_bytes(screenshot_bytes(adb))
    return destination


def looks_blank(png_bytes: bytes) -> bool:
    """Heuristic: a uniform black frame means the screen is off or the app is blank.

    Screenshots of a sleeping device compress to almost nothing, so a very small
    PNG relative to the pixel count is a reliable cheap signal. This avoids
    adding an image library dependency just to notice the obvious.
    """
    return len(png_bytes) < 25_000
