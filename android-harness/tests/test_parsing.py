"""Offline tests for the parsing, lookup and geometry layers.

These are the parts a device is not needed to verify, and the parts most likely
to break silently: a bounds regex that mis-parses, a lookup that returns the
full-screen wrapper instead of the button, a swipe that goes the wrong way.
"""

from __future__ import annotations

import unittest

from androidharness.adb import parse_devices
from androidharness.input import Unsupported, swipe_direction, type_text
from androidharness.screen import looks_blank
from androidharness.tree import (
    UiDumpError,
    all_labels,
    find_nodes,
    find_one,
    parse_dump,
)
from androidharness.verify import screen_signature

# A trimmed but structurally realistic dump: a labelled TextView nested inside a
# clickable container (the case that makes naive coordinate tapping miss).
SAMPLE_DUMP = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.example.app"
        content-desc="" clickable="false" enabled="true" bounds="[0,0][1080,2400]">
    <node index="0" text="Settings" resource-id="com.example.app:id/title"
          class="android.widget.TextView" package="com.example.app" content-desc=""
          clickable="false" enabled="true" bounds="[40,200][400,280]" />
    <node index="1" text="" resource-id="com.example.app:id/row_wifi"
          class="android.widget.LinearLayout" package="com.example.app" content-desc=""
          clickable="true" enabled="true" bounds="[0,300][1080,420]">
      <node index="0" text="Wi-Fi" resource-id="com.example.app:id/label"
            class="android.widget.TextView" package="com.example.app" content-desc=""
            clickable="false" enabled="true" bounds="[40,320][300,400]" />
    </node>
    <node index="2" text="" resource-id=""
          class="android.widget.ImageButton" package="com.example.app"
          content-desc="Search" clickable="true" enabled="true" bounds="[900,200][1040,340]" />
    <node index="3" text="Disabled action" resource-id="com.example.app:id/off"
          class="android.widget.Button" package="com.example.app" content-desc=""
          clickable="true" enabled="false" bounds="[0,500][540,600]" />
    <node index="4" text="" resource-id="" class="android.view.View"
          package="com.example.app" content-desc="" clickable="false" enabled="true"
          bounds="[0,0][0,0]" />
  </node>
</hierarchy>
"""


class ParseDevicesTests(unittest.TestCase):
    def test_parses_usb_device(self):
        output = (
            "List of devices attached\n"
            "eeb08bb9               device usb:337641472X product:alioth model:M2012K11AC "
            "device:alioth transport_id:6\n"
        )
        devices = parse_devices(output)
        self.assertEqual(len(devices), 1)
        device = devices[0]
        self.assertEqual(device.serial, "eeb08bb9")
        self.assertEqual(device.state, "device")
        self.assertEqual(device.model, "M2012K11AC")
        self.assertEqual(device.product, "alioth")
        self.assertEqual(device.transport, "usb")
        self.assertTrue(device.usable)

    def test_values_may_contain_colons(self):
        # `usb:337641472X` must keep its value, not split again on the colon.
        devices = parse_devices("List of devices attached\nabc device usb:337641472X\n")
        self.assertEqual(devices[0].attributes["usb"], "337641472X")

    def test_wifi_serial_is_detected_as_wifi(self):
        devices = parse_devices("List of devices attached\n192.168.1.5:5555  device\n")
        self.assertEqual(devices[0].transport, "wifi")
        self.assertTrue(devices[0].usable)

    def test_unauthorized_device_is_not_usable(self):
        devices = parse_devices("List of devices attached\nabc  unauthorized\n")
        self.assertFalse(devices[0].usable)

    def test_offline_device_is_not_usable(self):
        devices = parse_devices("List of devices attached\nabc  offline\n")
        self.assertFalse(devices[0].usable)

    def test_empty_list(self):
        self.assertEqual(parse_devices("List of devices attached\n\n"), [])

    def test_daemon_chatter_is_ignored(self):
        output = (
            "* daemon not running; starting now at tcp:5037\n"
            "* daemon started successfully\n"
            "List of devices attached\n"
            "abc  device\n"
        )
        self.assertEqual([device.serial for device in parse_devices(output)], ["abc"])

    def test_multiple_devices(self):
        output = "List of devices attached\nabc  device\ndef  unauthorized\n"
        self.assertEqual(len(parse_devices(output)), 2)


class ParseDumpTests(unittest.TestCase):
    def setUp(self):
        self.root = parse_dump(SAMPLE_DUMP)

    def test_root_and_nesting(self):
        self.assertEqual(len(self.root.children), 5)
        self.assertEqual(self.root.children[1].children[0].text, "Wi-Fi")

    def test_parent_links_are_set(self):
        wifi_label = self.root.children[1].children[0]
        self.assertIs(wifi_label.parent, self.root.children[1])

    def test_bounds_are_parsed(self):
        self.assertEqual(self.root.bounds, (0, 0, 1080, 2400))
        self.assertEqual(self.root.children[0].bounds, (40, 200, 400, 280))

    def test_center_and_size(self):
        title = self.root.children[0]
        self.assertEqual(title.center, (220, 240))
        self.assertEqual(title.width, 360)
        self.assertEqual(title.height, 80)

    def test_zero_area_node_is_not_visible(self):
        zero = self.root.children[4]
        self.assertFalse(zero.visible)

    def test_negative_bounds_do_not_crash(self):
        xml = (
            '<hierarchy><node index="0" text="x" bounds="[-10,-20][30,40]" '
            'class="android.view.View" package="p" resource-id="" content-desc="" '
            'clickable="true" enabled="true" /></hierarchy>'
        )
        node = parse_dump(xml)
        self.assertEqual(node.bounds, (-10, -20, 30, 40))
        self.assertEqual(node.center, (10, 10))

    def test_flags_are_parsed(self):
        disabled = self.root.children[3]
        self.assertTrue(disabled.clickable)
        self.assertFalse(disabled.enabled)

    def test_plain_text_failure_is_rejected(self):
        # uiautomator prints errors on stdout instead of XML; must not be parsed as a tree.
        with self.assertRaises(UiDumpError):
            parse_dump("ERROR: could not get idle state.")

    def test_empty_dump_is_rejected(self):
        with self.assertRaises(UiDumpError):
            parse_dump("   ")

    def test_malformed_xml_is_rejected(self):
        with self.assertRaises(UiDumpError):
            parse_dump("<hierarchy><node bounds='[0,0][1,1]'>")

    def test_preamble_before_declaration_is_tolerated(self):
        root = parse_dump("warning: something\n" + SAMPLE_DUMP)
        self.assertEqual(root.bounds, (0, 0, 1080, 2400))


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.root = parse_dump(SAMPLE_DUMP)

    def test_find_by_text(self):
        matches = find_nodes(self.root, text="Wi-Fi")
        self.assertEqual(len(matches), 1)

    def test_text_matching_is_case_insensitive_by_default(self):
        self.assertEqual(len(find_nodes(self.root, text="wi-fi")), 1)

    def test_exact_matching_rejects_partial(self):
        self.assertEqual(find_nodes(self.root, text="Wi", exact=True), [])
        self.assertEqual(len(find_nodes(self.root, text="Wi-Fi", exact=True)), 1)

    def test_find_by_content_desc(self):
        matches = find_nodes(self.root, content_desc="Search")
        self.assertEqual(matches[0].class_name, "android.widget.ImageButton")

    def test_find_by_resource_id(self):
        matches = find_nodes(self.root, resource_id="row_wifi")
        self.assertEqual(matches[0].bounds, (0, 300, 1080, 420))

    def test_clickable_filter(self):
        self.assertEqual(find_nodes(self.root, resource_id="title", clickable=True), [])

    def test_invisible_nodes_are_excluded(self):
        self.assertEqual(find_nodes(self.root, class_name="android.view.View"), [])
        self.assertEqual(len(find_nodes(self.root, class_name="android.view.View", visible_only=False)), 1)

    def test_find_one_prefers_the_smallest_clickable_match(self):
        # Both the row and its label contain "Wi-Fi"; the row is clickable.
        node = find_one(self.root, text="Wi-Fi")
        self.assertEqual(node.resource_id, "com.example.app:id/label")

    def test_find_one_returns_none_when_absent(self):
        self.assertIsNone(find_one(self.root, text="definitely-not-here"))


class TapTargetTests(unittest.TestCase):
    def setUp(self):
        self.root = parse_dump(SAMPLE_DUMP)

    def test_label_walks_up_to_clickable_ancestor(self):
        label = self.root.children[1].children[0]
        self.assertFalse(label.clickable)
        target = label.tap_target()
        self.assertTrue(target.clickable)
        self.assertEqual(target.resource_id, "com.example.app:id/row_wifi")

    def test_tap_target_centre_is_inside_the_row(self):
        label = self.root.children[1].children[0]
        x, y = label.tap_target().center
        self.assertEqual((x, y), (540, 360))

    def test_node_without_clickable_ancestor_falls_back_to_self(self):
        title = self.root.children[0]
        self.assertIs(title.tap_target(), title)

    def test_disabled_clickable_is_skipped(self):
        # `off` is clickable but disabled, so the nearest *usable* target is itself
        # only because there is no enabled clickable ancestor.
        disabled = self.root.children[3]
        self.assertIs(disabled.tap_target(), disabled)


class LabelTests(unittest.TestCase):
    def test_all_labels_clickable_only(self):
        labels = all_labels(parse_dump(SAMPLE_DUMP))
        self.assertIn("Wi-Fi", labels)
        self.assertIn("Search", labels)
        self.assertNotIn("Settings", labels, "non-clickable title should be excluded")

    def test_all_labels_including_non_clickable(self):
        labels = all_labels(parse_dump(SAMPLE_DUMP), clickable_only=False)
        self.assertIn("Settings", labels)


class ScreenSignatureTests(unittest.TestCase):
    def test_signature_is_stable_for_the_same_tree(self):
        self.assertEqual(screen_signature(parse_dump(SAMPLE_DUMP)), screen_signature(parse_dump(SAMPLE_DUMP)))

    def test_signature_changes_when_a_label_changes(self):
        changed = SAMPLE_DUMP.replace("Wi-Fi", "Bluetooth")
        self.assertNotEqual(screen_signature(parse_dump(SAMPLE_DUMP)), screen_signature(parse_dump(changed)))

    def test_signature_changes_when_geometry_changes(self):
        shifted = SAMPLE_DUMP.replace("[0,300][1080,420]", "[0,340][1080,460]")
        self.assertNotEqual(screen_signature(parse_dump(SAMPLE_DUMP)), screen_signature(parse_dump(shifted)))

    def test_signature_ignores_unlabelled_wrappers(self):
        # Layout wrappers churn without the visible screen changing.
        with_extra = SAMPLE_DUMP.replace(
            '<node index="0" text="Settings"',
            '<node index="9" text="" class="android.view.ViewGroup" package="com.example.app" '
            'resource-id="" content-desc="" clickable="false" enabled="true" bounds="[0,0][1,1]" />'
            '<node index="0" text="Settings"',
        )
        self.assertEqual(screen_signature(parse_dump(SAMPLE_DUMP)), screen_signature(parse_dump(with_extra)))


class FakeAdb:
    """Records shell calls so geometry and guards can be tested without a device."""

    def __init__(self, width: int = 1080, height: int = 2400) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._size = (width, height)

    def shell(self, *args: str, **_kwargs) -> str:
        self.calls.append(args)
        return ""

    def display_size(self) -> tuple[int, int]:
        return self._size


class SwipeGeometryTests(unittest.TestCase):
    def test_swipe_up_starts_low_and_ends_high(self):
        adb = FakeAdb()
        swipe_direction(adb, "up")
        args = adb.calls[-1]
        self.assertEqual(args[:2], ("input", "swipe"))
        start_y, end_y = int(args[3]), int(args[5])
        self.assertGreater(start_y, end_y, "finger must travel upward to scroll content down")
        self.assertEqual(int(args[2]), 540)

    def test_swipe_down_starts_high_and_ends_low(self):
        adb = FakeAdb()
        swipe_direction(adb, "down")
        args = adb.calls[-1]
        self.assertLess(int(args[3]), int(args[5]))

    def test_swipe_left_travels_negative_x(self):
        adb = FakeAdb()
        swipe_direction(adb, "left")
        args = adb.calls[-1]
        self.assertGreater(int(args[2]), int(args[4]))

    def test_invalid_direction_raises(self):
        with self.assertRaises(ValueError):
            swipe_direction(FakeAdb(), "sideways")

    def test_span_is_non_zero_on_tiny_displays(self):
        adb = FakeAdb(width=2, height=2)
        swipe_direction(adb, "up")
        args = adb.calls[-1]
        self.assertNotEqual(int(args[3]), int(args[5]))


class TypeTextGuardTests(unittest.TestCase):
    def test_ascii_text_is_sent(self):
        adb = FakeAdb()
        type_text(adb, "hello")
        self.assertEqual(adb.calls[-1], ("input", "text", "hello"))

    def test_spaces_become_percent_s(self):
        adb = FakeAdb()
        type_text(adb, "hello world")
        self.assertIn("%s", adb.calls[-1][-1])
        self.assertNotIn(" ", adb.calls[-1][-1])

    def test_shell_metacharacters_are_quoted(self):
        adb = FakeAdb()
        type_text(adb, "a;rm -rf /")
        payload = adb.calls[-1][-1]
        self.assertTrue(payload.startswith("'") or "\\" in payload, payload)

    def test_cjk_is_rejected_rather_than_silently_dropped(self):
        adb = FakeAdb()
        with self.assertRaises(Unsupported) as context:
            type_text(adb, "你好")
        self.assertIn("ASCII", str(context.exception))
        self.assertEqual(adb.calls, [], "nothing should reach the device")

    def test_emoji_is_rejected(self):
        with self.assertRaises(Unsupported):
            type_text(FakeAdb(), "ok \U0001F600")

    def test_empty_text_is_a_no_op(self):
        adb = FakeAdb()
        type_text(adb, "")
        self.assertEqual(adb.calls, [])


class LookBlankTests(unittest.TestCase):
    def test_small_png_is_blank(self):
        self.assertTrue(looks_blank(b"\x89PNG" + b"\x00" * 15_000))

    def test_large_png_is_not_blank(self):
        self.assertFalse(looks_blank(b"\x89PNG" + b"\x00" * 200_000))


if __name__ == "__main__":
    unittest.main()
