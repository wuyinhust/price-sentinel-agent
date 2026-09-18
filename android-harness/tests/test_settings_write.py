"""Tests for the settings-write probe.

This probe exists because of a misdiagnosis worth not repeating. On MIUI/HyperOS
a refused `settings put` reports `WRITE_SETTINGS`, which reads like a second,
unrelated restriction sitting next to the input-injection gate. It is not: both
are governed by the same developer toggle. Having a probe means the question
"can I write settings here?" gets asked directly instead of being inferred from
whether tapping works.

The probe writes a key's *current* value back to itself, so a passing run has no
effect on the device. These tests pin that, plus the refusal paths.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from androidharness import cli
from androidharness.adb import AdbError
from androidharness.input import (
    MIUI_SETTINGS_HINT,
    probe_settings_write,
)


class _ScriptedAdb:
    """An Adb stand-in that answers `settings` calls from a script."""

    def __init__(self, *, current: str = "60000", put_error: AdbError | None = None):
        self.current = current
        self.put_error = put_error
        self.calls: list[tuple[str, ...]] = []

    def shell(self, *args: str, timeout: float = 30.0, check: bool = True) -> str:
        self.calls.append(args)
        if args[:2] == ("settings", "get"):
            return self.current
        if args[:2] == ("settings", "put"):
            if self.put_error is not None:
                raise self.put_error
            self.current = args[-1]
            return ""
        raise AssertionError(f"unexpected call {args}")


def _denied(permission: str) -> AdbError:
    return AdbError(
        ["shell"],
        255,
        f"java.lang.SecurityException: com.android.shell was not granted this permission: {permission}",
    )


class ProbeSettingsWriteTests(unittest.TestCase):
    def test_a_writable_device_reports_ok(self):
        adb = _ScriptedAdb(current="60000")
        writable, reason = probe_settings_write(adb)
        self.assertTrue(writable)
        self.assertEqual(reason, "")

    def test_the_probe_writes_the_value_back_unchanged(self):
        """A passing probe must not alter the device."""
        adb = _ScriptedAdb(current="60000")
        probe_settings_write(adb)
        put = [call for call in adb.calls if call[:2] == ("settings", "put")]
        self.assertEqual(len(put), 1)
        self.assertEqual(put[0][-1], "60000", "the probe wrote something other than the current value")

    def test_refusal_names_the_same_toggle_as_input_injection(self):
        adb = _ScriptedAdb(put_error=_denied("android.permission.WRITE_SETTINGS"))
        writable, reason = probe_settings_write(adb)
        self.assertFalse(writable)
        self.assertEqual(reason, MIUI_SETTINGS_HINT)
        self.assertIn("SAME gate", reason, "the hint must not present this as a second restriction")

    def test_secure_variant_is_recognised_too(self):
        adb = _ScriptedAdb(put_error=_denied("android.permission.WRITE_SECURE_SETTINGS"))
        writable, reason = probe_settings_write(adb)
        self.assertFalse(writable)
        self.assertEqual(reason, MIUI_SETTINGS_HINT)

    def test_an_unrelated_failure_is_surfaced_verbatim(self):
        adb = _ScriptedAdb(put_error=AdbError(["shell"], 1, "device offline"))
        writable, reason = probe_settings_write(adb)
        self.assertFalse(writable)
        self.assertNotEqual(reason, MIUI_SETTINGS_HINT)
        self.assertIn("offline", reason)

    def test_an_unset_key_is_not_guessed_at(self):
        """Writing a guess would change the device to prove a point."""
        adb = _ScriptedAdb(current="null")
        writable, reason = probe_settings_write(adb)
        self.assertFalse(writable)
        self.assertIn("not writing a guess", reason)
        self.assertFalse([call for call in adb.calls if call[:2] == ("settings", "put")])


class _FakeProcess:
    """Stands in for the Popen handle the mirror command waits on."""

    pid = 4321

    def wait(self) -> int:
        return 0


class _FakeSession:
    """A connected session whose adb never leaves the process.

    ``_cmd_mirror`` calls ``connect()`` before it looks at anything else, so
    without this the gating tests below would need a real, authorized phone
    plugged in -- and would error out on every machine that has none.
    """

    def __init__(self):
        self.adb = mock.MagicMock()


def _fake_connect(_serial=None):
    return _FakeSession()


class StayAwakeGatingTests(unittest.TestCase):
    """`--stay-awake` writes a global setting, so it depends on the write probe."""

    def _run(self, *, writable: bool, args=None):
        started: dict = {}

        def fake_start(_adb, options, **_kwargs):
            started["options"] = options
            return _FakeProcess()

        with (
            mock.patch("androidharness.connect", _fake_connect),
            mock.patch.object(cli, "probe_input", return_value=(True, "")),
            mock.patch.object(
                cli, "probe_settings_write", return_value=(writable, "" if writable else "blocked")
            ),
            mock.patch.object(cli.mirror, "start", fake_start),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = cli._cmd_mirror(args or _mirror_args())
        self.assertEqual(code, 0)
        return started["options"]

    def test_stay_awake_is_kept_when_writes_work(self):
        options = self._run(writable=True)
        self.assertTrue(options.stay_awake)

    def test_stay_awake_is_dropped_when_writes_are_refused(self):
        options = self._run(writable=False)
        self.assertFalse(options.stay_awake, "a refused write prints a Java stack trace on every launch")

    def test_the_probe_is_skipped_when_the_user_already_said_no(self):
        args = _mirror_args()
        args.no_stay_awake = True
        with (
            mock.patch("androidharness.connect", _fake_connect),
            mock.patch.object(cli, "probe_input", return_value=(True, "")),
            mock.patch.object(cli, "probe_settings_write") as probe,
            mock.patch.object(cli.mirror, "start", return_value=None),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli._cmd_mirror(args)
        probe.assert_not_called()


def _mirror_args():
    import argparse

    return argparse.Namespace(
        serial=None,
        input="auto",
        max_size=1024,
        audio=False,
        no_stay_awake=False,
        turn_screen_off=False,
        title=None,
        on_top=False,
        foreground=False,
    )


if __name__ == "__main__":
    unittest.main()
