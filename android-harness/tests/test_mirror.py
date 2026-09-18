"""Tests for the mirror half.

These pin three things that were wrong before and would have failed silently on
a real device:

* the adb executable is handed to scrcpy through the `ADB` environment variable
  — scrcpy has no `--adb` flag, so passing one made every launch fail;
* the server jar does not sit next to the scrcpy binary in the Escrcpy bundle,
  so `SCRCPY_SERVER_PATH` has to be supplied or the server never starts;
* the input backend has to be selectable, because a device that refuses injected
  input renders a mirror window whose mouse does nothing at all.
"""

from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest import mock

from androidharness import cli, mirror
from androidharness.adb import Adb, find_scrcpy, find_scrcpy_icons, find_scrcpy_server

FAKE_SCRCPY = Path("/nonexistent/scrcpy")


class MirrorCommandTests(unittest.TestCase):
    def setUp(self):
        self.adb = Adb(path=Path("/nonexistent/adb"), serial="serial-1")

    def test_adb_path_travels_through_the_environment_not_argv(self):
        command = mirror.build_command(self.adb, scrcpy_path=FAKE_SCRCPY)
        self.assertFalse(
            any(argument.startswith("--adb") for argument in command),
            f"scrcpy has no --adb flag; got {command}",
        )
        self.assertEqual(mirror.environment(self.adb)["ADB"], "/nonexistent/adb")

    def test_serial_is_passed_as_a_selector(self):
        command = mirror.build_command(self.adb, scrcpy_path=FAKE_SCRCPY)
        self.assertEqual(command[command.index("--serial") + 1], "serial-1")

    def test_no_serial_means_no_selector(self):
        command = mirror.build_command(Adb(path=Path("/nonexistent/adb")), scrcpy_path=FAKE_SCRCPY)
        self.assertNotIn("--serial", command)

    def test_environment_pins_the_server_and_icons_when_discoverable(self):
        env = mirror.environment(self.adb, scrcpy_path=FAKE_SCRCPY)
        server = find_scrcpy_server(FAKE_SCRCPY)
        if server is None:
            self.skipTest("no scrcpy-server on this machine")
        self.assertEqual(env["SCRCPY_SERVER_PATH"], str(server))
        icons = find_scrcpy_icons(FAKE_SCRCPY)
        if icons is not None:
            self.assertEqual(env["SCRCPY_ICON_DIR"], str(icons))

    def test_record_builds_a_recording_without_a_window(self):
        # Regression: record() called dataclasses.replace without importing it.
        with mock.patch.object(mirror.subprocess, "run") as run:
            path = mirror.record(self.adb, "/tmp/out.mp4", scrcpy_path=FAKE_SCRCPY)
        command = run.call_args.args[0]
        self.assertIn("--no-window", command)
        self.assertIn("--no-control", command)
        self.assertIn("--record", command)
        self.assertEqual(path, Path("/tmp/out.mp4"))


class InputModeTests(unittest.TestCase):
    def test_defaults_leave_input_to_scrcpy(self):
        args = mirror.MirrorOptions().to_args()
        self.assertNotIn("--mouse", args)
        self.assertNotIn("--keyboard", args)

    def test_hid_preset_requests_uhid_for_both_devices(self):
        args = mirror.MirrorOptions.hid().to_args()
        self.assertEqual(args[args.index("--mouse") + 1], "uhid")
        self.assertEqual(args[args.index("--keyboard") + 1], "uhid")

    def test_hid_preset_still_honours_overrides(self):
        options = mirror.MirrorOptions.hid(window_title="phone")
        self.assertEqual(options.window_title, "phone")
        self.assertEqual(options.mouse, "uhid")

    def test_explicit_mode_is_used_verbatim(self):
        mouse, keyboard, notes = cli._resolve_input_mode(argparse.Namespace(input="aoa"), self._adb())
        self.assertEqual((mouse, keyboard), ("aoa", "aoa"))
        self.assertFalse(notes, "an explicit mode needs no explanation")

    def test_auto_falls_back_to_uhid_when_injection_is_refused(self):
        with mock.patch.object(cli, "probe_input", return_value=(False, "INJECT_EVENTS")):
            mouse, keyboard, notes = cli._resolve_input_mode(argparse.Namespace(input="auto"), self._adb())
        self.assertEqual((mouse, keyboard), ("uhid", "uhid"))
        self.assertTrue(any("UHID" in line for line in notes))
        self.assertTrue(any("relative" in line for line in notes))

    def test_auto_leaves_scrcpy_default_when_injection_works(self):
        with mock.patch.object(cli, "probe_input", return_value=(True, "")):
            mouse, keyboard, notes = cli._resolve_input_mode(argparse.Namespace(input="auto"), self._adb())
        self.assertEqual((mouse, keyboard), (None, None))
        self.assertFalse(notes)

    @staticmethod
    def _adb() -> Adb:
        return Adb(path=Path("/nonexistent/adb"))


class DiscoveryTests(unittest.TestCase):
    def test_scrcpy_is_found_on_this_machine(self):
        binary = find_scrcpy()
        if binary is None:
            self.skipTest("scrcpy is not installed here")
        self.assertTrue(binary.is_file())

    def test_server_jar_is_found_next_to_or_beside_the_binary(self):
        binary = find_scrcpy()
        if binary is None:
            self.skipTest("scrcpy is not installed here")
        server = find_scrcpy_server(binary)
        self.assertIsNotNone(server, "scrcpy cannot start without its server jar")
        # The real server is orders of magnitude larger than the wireless helper.
        self.assertGreater(server.stat().st_size, 500_000)


if __name__ == "__main__":
    unittest.main()
