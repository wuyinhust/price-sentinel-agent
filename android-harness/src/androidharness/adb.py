"""Locate and drive the Android Debug Bridge.

The Escrcpy desktop app ships a complete, self-contained adb + scrcpy pair, so
a machine that already has Escrcpy does not need Homebrew, android-platform-tools
or a PATH entry. Discovery therefore looks at PATH first (respecting an explicit
user install) and falls back to the app bundle.

Nothing here is Escrcpy-specific at runtime: Escrcpy is just a convenient
supplier of the two binaries.
"""

from __future__ import annotations

import glob as globlib
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Escrcpy keeps its vendored binaries per-architecture, so the middle segment is
# a glob. These are directories that contain the `adb` and `scrcpy` binaries.
_ESCRCPY_BUNDLE_DIRS = (
    "/Applications/Escrcpy.app/Contents/Resources/extra/*/scrcpy",
    "~/Applications/Escrcpy.app/Contents/Resources/extra/*/scrcpy",
    "/opt/homebrew/opt/escrcpy/libexec/escrcpy/scrcpy",
    "/usr/local/opt/escrcpy/libexec/escrcpy/scrcpy",
)


class AdbNotFound(RuntimeError):
    """No usable adb binary could be located."""


class AdbError(RuntimeError):
    """adb exited non-zero."""

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.args_list = args
        self.returncode = returncode
        self.stderr = stderr.strip()
        super().__init__(f"adb {' '.join(args)} exited {returncode}: {self.stderr}")


def _candidates(name: str) -> list[Path]:
    """All plausible locations for an adb-family binary, PATH first."""
    found: list[Path] = []
    on_path = shutil.which(name)
    if on_path:
        found.append(Path(on_path))
    for pattern in _ESCRCPY_BUNDLE_DIRS:
        for directory in sorted(globlib.glob(os.path.expanduser(pattern))):
            candidate = Path(directory) / name
            if candidate not in found:
                found.append(candidate)
    return found


def find_adb() -> Path:
    for candidate in _candidates("adb"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise AdbNotFound(
        "no adb found on PATH or bundled with Escrcpy. "
        "Install Escrcpy (/Applications/Escrcpy.app) or `brew install android-platform-tools`."
    )


def find_scrcpy() -> Path | None:
    for candidate in _candidates("scrcpy"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


# scrcpy's server jar and its icons are NOT next to the scrcpy binary in the
# Escrcpy bundle: they live in a sibling `extra/common/` tree. scrcpy only looks
# beside its own binary, so the path has to be handed over explicitly, otherwise
# it dies with "scrcpy-server does not exist" / "Server connection failed".
_ESCRCPY_COMMON_DIRS = (
    "/Applications/Escrcpy.app/Contents/Resources/extra/common/*",
    "~/Applications/Escrcpy.app/Contents/Resources/extra/common/*",
    "/opt/homebrew/opt/escrcpy/libexec/escrcpy",
    "/usr/local/opt/escrcpy/libexec/escrcpy",
)


def _common_dirs(scrcpy_path: Path | None = None) -> list[Path]:
    """Directories that may hold the server jar, best candidate first.

    The binary's own directory comes first so a Homebrew install that keeps the
    jar alongside still wins. The glob is sorted, which also puts
    `common/scrcpy` ahead of `common/wscrcpy` — only the first is the real
    server, the second is the wireless helper.
    """
    dirs: list[Path] = []
    binary = scrcpy_path or find_scrcpy()
    if binary is not None:
        dirs.append(Path(binary).parent)
    for pattern in _ESCRCPY_COMMON_DIRS:
        for directory in sorted(globlib.glob(os.path.expanduser(pattern))):
            candidate = Path(directory)
            if candidate not in dirs:
                dirs.append(candidate)
    return dirs


def find_scrcpy_server(scrcpy_path: Path | None = None) -> Path | None:
    """Locate the server jar, for SCRCPY_SERVER_PATH."""
    override = os.environ.get("SCRCPY_SERVER_PATH")
    if override and Path(override).is_file():
        return Path(override)
    for directory in _common_dirs(scrcpy_path):
        candidate = directory / "scrcpy-server"
        if candidate.is_file():
            return candidate
    return None


def find_scrcpy_icons(scrcpy_path: Path | None = None) -> Path | None:
    """Locate the directory holding scrcpy.png / disconnected.png.

    Purely cosmetic: without it scrcpy logs `Could not open icon image`, which
    looks alarming but changes nothing.
    """
    override = os.environ.get("SCRCPY_ICON_DIR")
    if override and Path(override).is_dir():
        return Path(override)
    for directory in _common_dirs(scrcpy_path):
        if (directory / "scrcpy.png").is_file():
            return directory
    return None


@dataclass(frozen=True)
class Device:
    """One entry from `adb devices -l`."""

    serial: str
    state: str  # device | offline | unauthorized | ...
    attributes: dict[str, str] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return self.state == "device"

    @property
    def model(self) -> str:
        return self.attributes.get("model", "?")

    @property
    def product(self) -> str:
        return self.attributes.get("product", "?")

    @property
    def transport(self) -> str:
        # `adb devices -l` emits `usb:337641472X`; the key is "usb" and its value
        # holds the port, so match the key, not "usb:".
        if "usb" in self.attributes:
            return "usb"
        if ":" in self.serial:
            return "wifi"
        return "unknown"

    def describe(self) -> str:
        return f"{self.serial}  {self.state:13} {self.transport:7} {self.model} ({self.product})"


def parse_devices(output: str) -> list[Device]:
    """Parse `adb devices -l` output.

    Lines look like:
        eeb08bb9   device usb:337641472X product:alioth model:M2012K11AC transport_id:6
    The first two whitespace-separated columns are the serial and state; the
    rest are ``key:value`` pairs whose values may themselves contain colons
    (``usb:337641472X``), so only the first colon splits.
    """
    devices: list[Device] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        if line.startswith("*"):  # daemon startup chatter
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        attributes: dict[str, str] = {}
        for token in parts[2:]:
            key, separator, value = token.partition(":")
            if separator:
                attributes[key] = value
        devices.append(Device(serial=serial, state=state, attributes=attributes))
    return devices


class Adb:
    """A thin, serial-scoped wrapper around one adb binary."""

    def __init__(self, path: Path | None = None, serial: str | None = None) -> None:
        self.path = path or find_adb()
        self.serial = serial

    # ---- plumbing ---------------------------------------------------------

    def _base(self) -> list[str]:
        command = [str(self.path)]
        if self.serial:
            command += ["-s", self.serial]
        return command

    def run(self, *args: str, timeout: float = 30.0, check: bool = True) -> str:
        """Run adb and return stdout as text."""
        completed = subprocess.run(
            [*self._base(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and completed.returncode != 0:
            raise AdbError(list(args), completed.returncode, completed.stderr)
        return completed.stdout

    def run_bytes(self, *args: str, timeout: float = 60.0) -> bytes:
        """Run adb and return raw stdout, for binary payloads (screencap)."""
        completed = subprocess.run(
            [*self._base(), *args],
            capture_output=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            raise AdbError(list(args), completed.returncode, completed.stderr.decode("utf-8", "replace"))
        return completed.stdout

    def shell(self, *args: str, timeout: float = 30.0, check: bool = True) -> str:
        return self.run("shell", *args, timeout=timeout, check=check)

    # ---- device selection -------------------------------------------------

    def devices(self) -> list[Device]:
        return parse_devices(self.run("devices", "-l"))

    def for_serial(self, serial: str) -> Adb:
        return Adb(path=self.path, serial=serial)

    def resolve(self) -> Adb:
        """Pick a device if none is pinned: a single usable one, else fail loudly.

        A plugged-in phone always wins over a Wi-Fi one, matching the ordering
        adb itself reports.
        """
        if self.serial:
            return self
        devices = self.devices()
        usable = [device for device in devices if device.usable]
        if len(usable) == 1:
            return self.for_serial(usable[0].serial)
        if not usable:
            detail = "; ".join(device.describe() for device in devices) or "none attached"
            raise AdbError(["devices"], 1, f"no usable device ({detail})")
        listing = "; ".join(f"{device.serial} ({device.model})" for device in usable)
        raise AdbError(["devices"], 1, f"multiple devices attached, pass a serial: {listing}")

    # ---- common reads -----------------------------------------------------

    def prop(self, name: str) -> str:
        return self.shell("getprop", name).strip()

    def version(self) -> str:
        return self.run("version").splitlines()[0].strip()

    def display_size(self) -> tuple[int, int]:
        """Physical screen size in pixels, from `wm size`."""
        output = self.shell("wm", "size")
        for line in output.splitlines():
            if "Physical size:" in line:
                raw = line.split("Physical size:")[1].strip()
                width, _, height = raw.partition("x")
                return int(width), int(height)
        raise AdbError(["shell", "wm", "size"], 1, f"could not parse size from {output!r}")

    def display_density(self) -> int:
        output = self.shell("wm", "density")
        for line in output.splitlines():
            if "Physical density:" in line:
                return int(line.split("Physical density:")[1].strip())
        raise AdbError(["shell", "wm", "density"], 1, f"could not parse density from {output!r}")
