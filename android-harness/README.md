# android-harness

Control an Android device over adb, with scrcpy for the human-visible mirror.

> Lives in the PriceSentinel repository at `android-harness/`. It has no
> dependency on the sentinel and is usable on its own — run its tests and CLI
> from this directory.

This replaces the previous `phone-harness` Android path. The design decision that
shapes everything here:

> **adb is the agent's path. scrcpy is the human's path. Neither does the other's job.**

Escrcpy is a GUI built on scrcpy. It gives you a window, a mouse and a keyboard —
but scrcpy drives the device through a video stream plus HID events and exposes
**no semantic read model**. An agent using only scrcpy is reduced to reading
pixels and guessing coordinates. So:

| Layer | Tool | What it provides |
|---|---|---|
| Eyes | adb + `uiautomator dump` | Exact labels, resource ids, content descriptions. No OCR, no misreads. |
| Hands | adb + `input` | Precise taps, swipes, keys, ASCII typing. |
| Verification | adb + tree diffing | "This label appeared" is a real assertion. |
| Mirror | scrcpy | A live window so a person can watch and take over. |

Escrcpy is used here as a **supplier of binaries**: it bundles a complete,
self-contained adb + scrcpy pair, so a machine that already has Escrcpy needs no
Homebrew, no `android-platform-tools`, and no PATH changes.

---

## Setup

Nothing to install if Escrcpy is present:

```bash
ls /Applications/Escrcpy.app     # supplies adb + scrcpy
android-harness doctor           # verify binaries, device, screen, tree
```

Binaries are discovered in this order: `PATH` first (respecting an explicit
install), then the Escrcpy bundle, then Homebrew's `escrcpy`.

### On the phone, once

1. Settings → About phone → tap **Build number** 7× (on HyperOS: **OS version**)
2. Settings → System → **Developer options**
3. **USB debugging** on → plug in → tap **Allow**
4. **MIUI / HyperOS only — see below**

---

## MIUI / HyperOS: input injection is blocked by default

On a Xiaomi device (verified on a Redmi K40 / `alioth`, MIUI on Android 13),
`adb shell input` **fails** until an extra toggle is enabled. Reads work fine;
only input is blocked:

```
java.lang.SecurityException: Injecting input events requires the caller
(or the source of the instrumentation, if any) to have the INJECT_EVENTS permission.
```

Fix: Developer options → **USB debugging (Security settings)** (USB调试(安全设置)).
The plain "USB debugging" toggle is not enough. MIUI may require a signed-in
Xiaomi account and an inserted SIM for that toggle. scrcpy's server asks you to
reboot, but **a reboot is not actually required** — measured on this device at
23h uptime with no reboot in between, injection worked the moment the toggle
flipped. Re-plugging USB is enough. Probe rather than assume.

Because scrcpy's SDK input is blocked by exactly the same gate, the mirror window
renders while its mouse does nothing until that toggle is on.

Three things worth knowing, all verified on this device:

- **It is not an AOSP permission you can grant.** `dumpsys package
  com.android.shell` already reports `INJECT_EVENTS: granted=true` and injection
  is still refused, so `pm grant` cannot fix it.
- **`WRITE_SETTINGS` / `WRITE_SECURE_SETTINGS` failures are the *same* gate, not a
  second one.** While the toggle was off, `settings put` reported
  `com.android.shell was not granted WRITE_SETTINGS` and `wm size` reported
  `WRITE_SECURE_SETTINGS`. The moment the toggle went on, both recovered — no
  other step. Verified by a round trip (`60000 → 90000`, read back `90000`, then
  restored). Do not conclude "settings cannot be written" from the early failure.
- **The toggle's state is readable in one command**, without touching the UI:
  ```bash
  adb shell getprop persist.security.adbinput   # empty/0 = off, 1 = on
  ```
  Neither `development_settings_enabled` nor `adb_enabled` tells you anything
  about it — both are `1` while injection is still refused. That is exactly how
  "adb connects but taps do nothing" gets misdiagnosed.

`android-harness doctor` detects this up front and says so, rather than letting a
script tap away at nothing. Check it in code with:

```python
ok, reason = phone.input_available()
if not ok:
    print(reason)   # names the exact setting
```

Reads that keep working even while the gate is closed: `tree`, `labels`, `shot`,
`state`, `packages`, `wait`, plus `am start` for opening a settings page by name.

---

## Driving the phone from the computer (dead touchscreen, or blocked input)

**Only needed while that toggle is off.** With the gate open, plain `mirror` is
strictly better — the mouse is absolute instead of relative.

scrcpy's normal input is the *sdk* backend — it calls the same Android API MIUI
refuses, so on those devices the mirror window is a picture, not a remote control.
Switch scrcpy to **UHID** and the events arrive as a virtual HID mouse and
keyboard instead. UHID is a kernel device: the events never reach the framework
check that MIUI enforces, so they land regardless of that toggle.

```bash
android-harness mirror                 # probes, and falls back to UHID automatically
android-harness mirror --input uhid    # or ask for it explicitly
android-harness mirror --input sdk     # force the default backend
```

`--input auto` (the default) spends one no-op keyevent to find out which kind of
device this is, then picks the backend that will actually work — a dead-looking
mirror window is a confusing thing to debug, and the probe is cheaper.

What to expect in UHID mode:

| | |
|---|---|
| Mouse | **Relative** and captured by the window. `LAlt` (or `Cmd`) releases it back to the computer. |
| Keyboard | A real HID keyboard. Its layout must be configured **on the device**, once: `adb shell am start -a android.settings.HARD_KEYBOARD_SETTINGS`. |
| Waking the screen | Any key press is real user activity, so it wakes the phone. A sleeping phone shows a black mirror and an empty tree. |
| Unlocking | Works if your lock screen accepts keyboard input — a PIN can be typed on the HID keyboard. Anything requiring a swipe or a tap works too, since the mouse is a real mouse. |
| Back / Home / Recents | `LAlt`+`b` / `h` / `s` (scrcpy shortcut keys). Right-click is forwarded to the device as a real right-click. |
| `--stay-awake` | Skipped automatically while the gate is closed (it writes a global setting). Once the gate is open the write lands, so it is applied normally. |

Because the mouse is relative, the on-screen pointer can drift out of sync with
where you think it is; move it to a screen corner to re-seat it. Turning down
screen brightness helps you see the pointer.

Once the security toggle is on, go back to `--input auto` (or `--input sdk`): the
mouse becomes absolute, which is far nicer for precise work, and the agent side
gets `adb shell input` back. `--input auto` makes that switch by itself, so the
same command is correct before and after.

### Reading a long list in one shot

`uiautomator dump` only contains **visible** nodes, so anything below the fold is
invisible to the harness. Now that the gate is open, temporarily enlarge the
virtual display and the whole list fits on one screen:

```bash
adb shell wm size 1080x6000     # clamped to ~2x physical height (2400 -> 4800)
# ... dump here ...
adb shell wm size reset         # always restore
```

Measured on the developer-options page: 83 nodes / 21 text entries at normal
size, **165 nodes / 49 text entries** enlarged — 28 entries recovered in a single
dump that were previously unreachable. Wrap the reset in `try/finally`; if reset
ever fails, `wm size 1080x2400` (the explicit physical size) is an equivalent
fallback.

Let the page settle first (`wait_stable`, or the tree comes back half-rendered
with a misleadingly low node count).

### Typing non-ASCII

Opening the gate does **not** make `adb shell input text` accept Chinese. On this
device it does not silently drop the text — it throws:

```
java.lang.NullPointerException: Attempt to get length of null array
    at com.android.server.input.InputShellCommand.sendText(InputShellCommand.java:325)
```

`type_text()` therefore refuses non-ASCII up front rather than appearing to work.
Options, best first:

1. **Install ADBKeyboard** (`com.android.adbkeyboard`) — the only clean route.
   Not installed here. Then `adb shell am broadcast -a ADB_INPUT_TEXT --es msg '你好'`.
2. **scrcpy clipboard sync** — copy on the Mac, scrcpy syncs it to the device,
   then `adb shell input keyevent 279` (`KEYCODE_PASTE`). Note `cmd clipboard` is
   not implemented on this MIUI, so you cannot seed the clipboard over adb.

Beware `$?` after a piped `adb shell input ...` — it reports the last command in
the pipe, so a failure can look like success. Check by reading the field back.

---

## Usage

### As a CLI

```bash
android-harness doctor                 # self-check, run this first
android-harness state                  # screen / lock / focus
android-harness tree --clickable       # what is on screen and tappable
android-harness labels                 # just the labels you can act on
android-harness tap-text "Wi-Fi"       # tap by label, not by coordinate
android-harness tap 540 1200           # tap by coordinate
android-harness key back
android-harness text "hello"           # ASCII only — see below
android-harness open chrome
android-harness shot --out /tmp/s.png
android-harness mirror                 # open the scrcpy window (for humans)
android-harness mirror --input auto    # the same, stating the input policy
android-harness wait                   # wait for settle, report what changed
```

### Batch a whole sub-task

Each tree dump costs seconds on a slow phone, so batch work into one invocation
rather than one call per step:

```bash
android-harness run <<'PY'
s = connect()
print(s.state().describe())
print(s.labels()[:20])
PY
```

### As a library

```python
from androidharness import connect

with connect() as phone:
    ok, reason = phone.input_available()
    if not ok:
        raise SystemExit(reason)

    phone.wake()                          # screen on; never unlocks
    tree = phone.wait_stable()            # settled tree
    node = phone.tap_label("Wi-Fi")       # exact label from the tree
    phone.require_text("Network & internet", timeout=10)
```

---

## Verification is mandatory, not optional

`adb` reports nothing about the outcome of an input event — a tap on empty space
"succeeds". After every action:

```python
phone.tap_label("Got it")
phone.wait_for_app("com.android.chrome")   # or:
phone.require_text("Got it")               # returns the node, raises on timeout
phone.text_gone("Got it")                  # confirm a dismissal
```

`screencap` for pixels, the tree for meaning. Prefer the tree: "the label
disappeared" is an assertion, "the pixels changed" is not.

---

## Design notes

**Taps go through `tap_target()`.** Android routinely puts a row's visible text
on a non-clickable child `TextView`, so tapping the label's own centre can miss.
`tap_label()` walks up to the nearest enabled, clickable, visible ancestor.

**`labels` folds in descendant text.** Without it, a listing shows
`com.example.app:id/row_wifi` where the screen says "Wi-Fi" — useless for
deciding what to tap.

**Non-ASCII typing is refused, not attempted.** `input text` is ASCII-only and
the device *silently drops* CJK and emoji. `type_text("你好")` raises and says
why, instead of leaving you with an empty field and no error. For CJK, install an
ADB-broadcast IME (ADBKeyboard) or type through scrcpy with a host-side IME.

**A sleeping phone still answers adb.** It just returns a black screenshot and an
almost-empty tree. `display_state()` reports `wakefulness` / `lock` explicitly so
that is never mistaken for a broken read. `wake()` turns the screen on but will
not try to unlock — a PIN can only be entered by the person holding the phone.

**Device selection is explicit or singular.** With one device attached it is
picked automatically; with several the error lists them rather than guessing.

---

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

51 offline tests cover the parsing, lookup and geometry layers — the parts that
fail silently: a bounds regex that mis-parses, a lookup that returns the
full-screen wrapper instead of the button, a swipe that travels the wrong way,
CJK reaching the device. A further 13 cover the scrcpy invocation, because three
of its arguments were wrong in a way nothing would have reported: `--adb` is not
a scrcpy flag (the adb path is the `ADB` environment variable), the server jar
does not sit beside the scrcpy binary in the Escrcpy bundle, and the input
backend has to be selectable for devices that refuse injected input. The
device-facing calls are verified by `doctor`.
