"""Drive an Android phone over adb to read on-screen prices.

The sentinel never collects by itself; this module is the collection edge. It
speaks adb only, so it works with whatever the phone already has installed and
logged in -- no platform API, no crawler fleet, no ban surface. The price facts
it produces are what the rule engine then judges.

Three device quirks shape the design. All three were observed on the reference
device (Redmi K40, MIUI / Android 13) and each one silently produces *wrong
data* if ignored rather than merely failing:

1. ``uiautomator dump`` prints a MIUI theme-loader stack trace to stderr
   (``/data/system/theme_config/theme_compatibility.xml`` ENOENT) and sometimes
   exits non-zero, *after which it succeeds anyway*. Success is therefore
   decided by the written file, never by stderr or the exit code.
2. A failed dump leaves the previous file in place, so a naive read returns a
   stale screen from an earlier run. The file is deleted before every dump.
3. Chinese cannot be typed with ``input text`` (NullPointerException on this
   build). Searches therefore go through deep links with percent-encoded
   keywords, which has the side benefit of making collection reproducible.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DUMP_PATH = "/sdcard/pricesentinel_dump.xml"
SCREENSHOT_PATH = "/sdcard/pricesentinel_screen.png"

_ADB_CANDIDATES = (
    "{home}/Applications/Escrcpy.app/Contents/Resources/extra/mac-x64/scrcpy/adb",
    "{home}/Applications/Escrcpy.app/Contents/Resources/extra/mac-arm64/scrcpy/adb",
    "/opt/homebrew/bin/adb",
    "/usr/local/bin/adb",
    "/usr/bin/adb",
)


class DeviceError(RuntimeError):
    """The phone could not be driven."""


class DumpError(RuntimeError):
    """The accessibility tree could not be read."""


def find_adb(explicit: str | None = None) -> str:
    """Locate adb: explicit argument, then $ADB, then PATH, then app bundles.

    The Escrcpy entries matter because adb is frequently installed *only* as a
    bundled binary inside a GUI app, where it never reaches PATH.
    """
    if explicit:
        return explicit
    from_env = os.environ.get("ADB")
    if from_env and Path(from_env).exists():
        return from_env
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    for template in _ADB_CANDIDATES:
        candidate = Path(template.format(home=Path.home()))
        if candidate.exists():
            return str(candidate)
    raise DeviceError(
        "adb not found. Pass adb_path=, set $ADB, or install adb. "
        "Searched PATH and the Escrcpy/Homebrew locations."
    )


def _run(argv: list[str], timeout: float) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as error:
        raise DeviceError(f"cannot execute {argv[0]!r}: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise DeviceError(f"{' '.join(argv[:3])} timed out after {timeout}s") from error
    # Deliberately ignore the return code: see quirk 1 in the module docstring.
    # stdout carries the payload; uiautomator's stack trace goes to stderr.
    return done.stdout


@dataclass
class ScreenState:
    awake: bool
    locked: bool
    focus: str

    def describe(self) -> str:
        return (
            f"{'awake' if self.awake else 'asleep'}, "
            f"{'locked' if self.locked else 'unlocked'}, focus={self.focus or '?'}"
        )


class AndroidDevice:
    """An adb-driven phone. One instance per device."""

    def __init__(self, adb_path: str | None = None, serial: str | None = None):
        self.adb = find_adb(adb_path)
        self.serial = serial or self._only_device()

    # ---- plumbing ---------------------------------------------------------

    def _base(self) -> list[str]:
        argv = [self.adb]
        if self.serial:
            argv += ["-s", self.serial]
        return argv

    def shell(self, *args: str, timeout: float = 30.0) -> str:
        return _run(self._base() + ["shell", *args], timeout)

    def _list_devices(self) -> list[str]:
        out = _run([self.adb, "devices"], timeout=15.0)
        found = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                found.append(parts[0])
        return found

    def _only_device(self) -> str:
        """Pick the single attached device.

        With several attached this raises rather than guessing: collecting the
        wrong phone's prices is worse than collecting nothing, because the data
        looks perfectly valid afterwards.
        """
        devices = self._list_devices()
        if not devices:
            raise DeviceError("no device attached (check the USB cable and adb authorization)")
        if len(devices) > 1:
            raise DeviceError(
                f"{len(devices)} devices attached ({', '.join(devices)}); "
                "pass serial= to choose one"
            )
        return devices[0]

    # ---- state ------------------------------------------------------------

    def screen_state(self) -> ScreenState:
        power = self.shell("dumpsys", "power")
        awake = "mWakefulness=Awake" in power or "mWakefulness=Dozing" not in power
        window = self.shell("dumpsys", "window")
        # Take the whole line, not the first token: the value is
        # ``Window{4dedaee u0 com.pkg/.Activity}`` and a ``\S+`` capture stops
        # at the first space, yielding a window id with no app or activity in
        # it -- useless for telling which screen the phone is on.
        match = re.search(r"mCurrentFocus=([^\n]*)", window)
        focus = match.group(1).strip() if match else ""
        locked = "StatusBar" in focus or "Keyguard" in window.split("mCurrentFocus")[0][-400:]
        return ScreenState(awake=awake, locked=locked, focus=focus)

    def wake(self) -> None:
        """Turn the screen on. Never attempts to unlock: a PIN is the user's."""
        self.shell("input", "keyevent", "224")

    # ---- capturing pixels (for pages with no accessibility tree) ----------

    def screenshot(self, destination: str | Path) -> Path:
        """Capture the current screen to a local PNG.

        Written to a device file and then pulled, rather than streamed through
        ``exec-out``: the stream form is subject to line-ending translation that
        can corrupt a PNG, and this is the one path where the bytes must survive
        intact.

        The remote file is deleted first for the same reason as the dump file --
        a failed capture must not be mistaken for the previous screen.
        """
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.shell("rm", "-f", SCREENSHOT_PATH)
        self.shell("screencap", "-p", SCREENSHOT_PATH)
        _run([*self._base(), "pull", SCREENSHOT_PATH, str(target)], 60.0)
        if not target.exists() or target.stat().st_size == 0:
            raise DeviceError("screen capture produced no image")
        return target

    def screen_size(self) -> tuple[int, int]:
        """Physical screen size in pixels, from ``wm size``."""
        out = self.shell("wm", "size")
        match = re.search(r"(\d+)x(\d+)", out)
        if not match:
            raise DeviceError(f"cannot read screen size from {out.strip()!r}")
        return int(match.group(1)), int(match.group(2))

    # ---- reading the screen ----------------------------------------------

    def read_tree(self, *, attempts: int = 3, delay: float = 1.2) -> str:
        """Return the current accessibility tree as XML text.

        The dump file is removed first so a failed dump cannot masquerade as a
        stale success. A readable dump must contain a ``<hierarchy`` element and
        be non-trivial; anything else is retried.
        """
        last = ""
        for attempt in range(1, attempts + 1):
            self.shell("rm", "-f", DUMP_PATH)
            self.shell("uiautomator", "dump", DUMP_PATH, timeout=60.0)
            xml_text = self.shell("cat", DUMP_PATH, timeout=30.0)
            if "<hierarchy" in xml_text and len(xml_text) > 500:
                return xml_text
            last = xml_text.strip()[:200]
            if attempt < attempts:
                time.sleep(delay)
        raise DumpError(
            "could not read the UI tree after "
            f"{attempts} attempts (last output: {last!r}). "
            "A blank or empty tree usually means the screen is off or locked."
        )

    # ---- acting on the phone ---------------------------------------------

    def open_uri(self, uri: str, package: str | None = None) -> None:
        """Fire a VIEW intent, optionally pinned to one app.

        Pinning matters: several shopping apps claim the same https hosts, and
        an unpinned intent gets resolved by whichever registered last.
        """
        args = ["am", "start", "-a", "android.intent.action.VIEW", "-d", uri]
        if package:
            args += ["-p", package]
        self.shell(*args)

    def key(self, keycode: str | int) -> None:
        self.shell("input", "keyevent", str(keycode))

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    def force_stop(self, package: str) -> None:
        self.shell("am", "force-stop", package)
