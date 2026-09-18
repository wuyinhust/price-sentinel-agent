"""android-harness: control an Android device over adb, with scrcpy for mirroring.

Division of labour, and the reason both tools are present:

- ``tree`` / ``verify`` / ``input`` use adb and give the agent a *semantic* read
  model (exact labels, resource ids, content descriptions) plus precise input.
- ``mirror`` uses scrcpy and gives the *human* a live window.

Neither tool is asked to do the other's job, so the agent never has to reason
about pixels and the person never has to read XML.

Typical use:

    from androidharness import connect

    with connect() as phone:
        phone.wake()
        tree = phone.tree()
        phone.tap_label("Settings")
        phone.require_text("Network & internet", timeout=10)
"""

from __future__ import annotations

from .adb import Adb, AdbError, AdbNotFound, Device, find_adb, find_scrcpy, parse_devices
from .input import (
    KEYCODES,
    InputBlocked,
    Unsupported,
    back,
    current_focus,
    current_package,
    home,
    install,
    key,
    launch,
    launched_packages,
    long_press,
    probe_input,
    probe_settings_write,
    pull,
    push,
    recents,
    resolve_package,
    swipe,
    swipe_direction,
    tap,
    type_text,
    wait_for_package,
)
from .mirror import MirrorOptions
from .screen import (
    DisplayState,
    Wakefulness,
    display_state,
    is_locked,
    looks_blank,
    screenshot,
    screenshot_bytes,
    sleep,
    stay_awake,
    wake,
    wakefulness,
)
from .tree import (
    Node,
    UiDumpError,
    all_labels,
    dump_tree,
    find_nodes,
    find_one,
    parse_dump,
    summarize,
    tap_label,
)
from .verify import (
    diff,
    require_text,
    screen_signature,
    snapshot_labels,
    text_gone,
    wait_for_app,
    wait_for_text,
    wait_stable,
)

__all__ = [
    "Adb",
    "AdbError",
    "AdbNotFound",
    "Device",
    "DisplayState",
    "KEYCODES",
    "InputBlocked",
    "MirrorOptions",
    "Node",
    "Session",
    "UiDumpError",
    "Unsupported",
    "Wakefulness",
    "all_labels",
    "back",
    "connect",
    "current_focus",
    "current_package",
    "diff",
    "display_state",
    "dump_tree",
    "find_adb",
    "find_nodes",
    "find_one",
    "find_scrcpy",
    "home",
    "install",
    "is_locked",
    "key",
    "launch",
    "launched_packages",
    "long_press",
    "looks_blank",
    "parse_devices",
    "parse_dump",
    "probe_input",
    "probe_settings_write",
    "pull",
    "push",
    "recents",
    "require_text",
    "resolve_package",
    "screenshot",
    "screenshot_bytes",
    "screen_signature",
    "sleep",
    "snapshot_labels",
    "stay_awake",
    "summarize",
    "swipe",
    "swipe_direction",
    "tap",
    "tap_label",
    "text_gone",
    "type_text",
    "wait_for_app",
    "wait_for_package",
    "wait_for_text",
    "wait_stable",
    "wake",
    "wakefulness",
    "find_"
]


class Session:
    """A device-bound handle exposing the whole API as methods.

    Preferred over passing an `Adb` around everywhere: it keeps the serial and
    the module functions in one object, which is what a script actually wants.
    """

    def __init__(self, adb: Adb) -> None:
        self.adb = adb

    # ---- lifecycle --------------------------------------------------------

    @property
    def serial(self) -> str | None:
        return self.adb.serial

    def identify(self) -> dict[str, str]:
        return {
            "serial": self.adb.serial or "?",
            "model": self.adb.prop("ro.product.model"),
            "product": self.adb.prop("ro.product.name"),
            "android": self.adb.prop("ro.build.version.release"),
            "sdk": self.adb.prop("ro.build.version.sdk"),
            "abi": self.adb.prop("ro.product.cpu.abi"),
        }

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def close(self) -> None:
        """No persistent connection is held; adb manages the daemon."""

    # ---- delegated API ----------------------------------------------------

    def tree(self, **kwargs) -> Node:
        return dump_tree(self.adb, **kwargs)

    def labels(self, **kwargs) -> list[str]:
        return all_labels(self.tree(), **kwargs)

    def find(self, **criteria) -> list[Node]:
        return find_nodes(self.tree(), **criteria)

    def find_one(self, **criteria) -> Node | None:
        return find_one(self.tree(), **criteria)

    def tap_label(self, label: str, **kwargs) -> Node:
        return tap_label(self.adb, label, **kwargs)

    def tap(self, x: int, y: int) -> None:
        tap(self.adb, x, y)

    def long_press(self, x: int, y: int, **kwargs) -> None:
        long_press(self.adb, x, y, **kwargs)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, **kwargs) -> None:
        swipe(self.adb, x1, y1, x2, y2, **kwargs)

    def swipe_direction(self, direction: str, **kwargs) -> None:
        swipe_direction(self.adb, direction, **kwargs)

    def key(self, name: str) -> None:
        key(self.adb, name)

    def back(self) -> None:
        back(self.adb)

    def home(self) -> None:
        home(self.adb)

    def recents(self) -> None:
        recents(self.adb)

    def type_text(self, text: str) -> None:
        type_text(self.adb, text)

    def launch(self, package: str, **kwargs) -> str:
        return launch(self.adb, package, **kwargs)

    def open_app(self, package: str, **kwargs) -> str:
        return launch(self.adb, package, **kwargs)

    def current_package(self) -> str | None:
        return current_package(self.adb)

    def packages(self) -> set[str]:
        return launched_packages(self.adb)

    def state(self) -> DisplayState:
        return display_state(self.adb)

    def input_available(self) -> tuple[bool, str]:
        """Whether this device accepts injected input, plus the reason if not.

        Worth calling once at the start of a task that depends on tapping: a
        locked MIUI device silently discards input, and discovering that at the
        end of a script is worse than up front.
        """
        return probe_input(self.adb)

    def settings_writable(self) -> tuple[bool, str]:
        """Whether this device lets the shell write settings, plus the reason.

        Same OEM toggle as `input_available` on MIUI, but a separate failure
        mode: a refused write leaves the old value in place and returns, so a
        script that "changed" the screen timeout can end up having changed
        nothing at all.
        """
        return probe_settings_write(self.adb)

    def wake(self) -> DisplayState:
        return wake(self.adb)

    def sleep(self) -> None:
        sleep(self.adb)

    def screenshot(self, path: str = "screenshot.png"):
        return screenshot(self.adb, path)

    def screenshot_bytes(self) -> bytes:
        return screenshot_bytes(self.adb)

    def wait_stable(self, **kwargs) -> Node:
        return wait_stable(self.adb, **kwargs)

    def wait_for_text(self, text: str, **kwargs) -> Node | None:
        return wait_for_text(self.adb, text, **kwargs)

    def require_text(self, text: str, **kwargs) -> Node:
        return require_text(self.adb, text, **kwargs)

    def wait_for_app(self, package: str, **kwargs) -> bool:
        return wait_for_app(self.adb, package, **kwargs)

    def text_gone(self, text: str, **kwargs) -> bool:
        return text_gone(self.adb, text, **kwargs)


def connect(serial: str | None = None) -> Session:
    """Resolve a device and return a ready Session.

    With no serial, a single attached device is selected automatically; with
    several attached, the error names them rather than guessing.
    """
    adb = Adb(serial=serial).resolve()
    return Session(adb)
