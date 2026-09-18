"""The human-visible half: a scrcpy mirror window.

This is the piece Escrcpy contributes. scrcpy renders the device screen so a
person can watch what the agent is doing, and its HID input path gives a human
direct control.

Deliberately NOT the agent's action path. scrcpy drives the device through a
video stream plus HID events and exposes no semantic read model, so an agent
would be reduced to reading pixels and guessing coordinates. Keeping the agent
on adb (see tree.py / input.py) and scrcpy on the human side is what lets both
use the same device without either giving up its capability.

On a device that refuses injected input — MIUI/HyperOS without "USB debugging
(Security settings)" — scrcpy's *default* SDK input path is blocked exactly like
`adb shell input`, so the mirror window renders but the mouse does nothing.
`MirrorOptions.hid()` still works there: UHID registers a virtual HID device in
the kernel, underneath the framework check MIUI enforces, so the events never
meet it. See `environment()` for the two variables scrcpy needs from us.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from .adb import Adb, find_scrcpy, find_scrcpy_icons, find_scrcpy_server


@dataclass
class MirrorOptions:
    """The scrcpy flags worth exposing, with sane defaults."""

    max_size: int | None = 1024
    bit_rate: str | None = "8M"
    max_fps: int | None = 30
    stay_awake: bool = True
    turn_screen_off: bool = False
    no_audio: bool = True
    window_title: str | None = None
    always_on_top: bool = False
    fullscreen: bool = False
    no_control: bool = False
    mouse: str | None = None
    keyboard: str | None = None
    extra: list[str] = field(default_factory=list)

    @classmethod
    def hid(cls, **overrides: object) -> MirrorOptions:
        """Options whose input survives a framework-level injection block.

        `--mouse=uhid --keyboard=uhid` make the device see a virtual HID mouse
        and keyboard. The events enter through the kernel, so MIUI's refusal of
        `injectInputEvent` never applies to them. The trade-off is that the
        mouse is *relative* (scrcpy captures it; LAlt or Cmd releases it) rather
        than scrcpy's usual absolute positioning.
        """
        return cls(mouse="uhid", keyboard="uhid", **overrides)

    def to_args(self) -> list[str]:
        args: list[str] = []
        if self.max_size:
            args += ["--max-size", str(self.max_size)]
        if self.bit_rate:
            args += ["--video-bit-rate", self.bit_rate]
        if self.max_fps:
            args += ["--max-fps", str(self.max_fps)]
        if self.stay_awake:
            args.append("--stay-awake")
        if self.turn_screen_off:
            args.append("--turn-screen-off")
        if self.no_audio:
            args.append("--no-audio")
        if self.window_title:
            args += ["--window-title", self.window_title]
        if self.always_on_top:
            args.append("--always-on-top")
        if self.fullscreen:
            args.append("--fullscreen")
        if self.no_control:
            args.append("--no-control")
        if self.mouse:
            args += ["--mouse", self.mouse]
        if self.keyboard:
            args += ["--keyboard", self.keyboard]
        return args + list(self.extra)


def environment(adb: Adb, scrcpy_path: Path | None = None) -> dict[str, str]:
    """Environment pinning scrcpy to this harness's adb and server jar.

    scrcpy takes the adb executable from the `ADB` variable — there is no
    `--adb` flag — and needs `SCRCPY_SERVER_PATH` whenever the jar does not sit
    next to its binary, which is exactly how the Escrcpy bundle is laid out.
    Without both, scrcpy either ignores our adb choice or fails to start its
    server at all.
    """
    env = dict(os.environ)
    env["ADB"] = str(adb.path)
    server = find_scrcpy_server(scrcpy_path)
    if server is not None:
        env["SCRCPY_SERVER_PATH"] = str(server)
    icons = find_scrcpy_icons(scrcpy_path)
    if icons is not None:
        env["SCRCPY_ICON_DIR"] = str(icons)
    return env


def build_command(adb: Adb, options: MirrorOptions | None = None, scrcpy_path: Path | None = None) -> list[str]:
    """Assemble the scrcpy invocation.

    The adb path travels through the environment rather than argv (scrcpy has no
    `--adb` flag), so that scrcpy agrees with the rest of the harness about which
    adb — and therefore which devices — it is talking to.
    """
    binary = scrcpy_path or find_scrcpy()
    if binary is None:
        raise FileNotFoundError(
            "scrcpy not found. Install Escrcpy (/Applications/Escrcpy.app) "
            "or `brew install scrcpy`."
        )
    command = [str(binary)]
    if adb.serial:
        command += ["--serial", adb.serial]
    return command + (options or MirrorOptions()).to_args()


def start(
    adb: Adb,
    options: MirrorOptions | None = None,
    *,
    scrcpy_path: Path | None = None,
    background: bool = True,
) -> subprocess.Popen | None:
    """Launch the mirror window.

    In background mode the process is detached and its handle returned, so a
    long agent run can keep a window open without blocking. Callers that want
    the window closed again should keep the handle and terminate it.
    """
    command = build_command(adb, options, scrcpy_path)
    env = environment(adb, scrcpy_path)
    if background:
        return subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    subprocess.run(command, env=env, check=True)
    return None


def record(
    adb: Adb,
    output: str | Path,
    *,
    seconds: float | None = None,
    options: MirrorOptions | None = None,
    scrcpy_path: Path | None = None,
) -> Path:
    """Record the mirror to a file for evidence or review.

    For a bounded recording scrcpy is asked to record without a window and is
    stopped after `seconds`; without a limit it records until interrupted.
    """
    base = options or MirrorOptions()
    recording = replace(base, no_control=True)
    command = build_command(adb, recording, scrcpy_path)
    command += ["--no-window", "--record", str(output)]
    env = environment(adb, scrcpy_path)
    if seconds is None:
        subprocess.run(command, env=env, check=True)
        return Path(output)

    process = subprocess.Popen(
        command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    try:
        process.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    return Path(output)


def available() -> bool:
    binary = find_scrcpy()
    return binary is not None and Path(binary).is_file()


def version(scrcpy_path: Path | None = None) -> str:
    binary = scrcpy_path or find_scrcpy()
    if binary is None:
        raise FileNotFoundError("scrcpy not found")
    completed = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=20)
    return completed.stdout.splitlines()[0].strip() if completed.stdout else ""


def terminate(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
