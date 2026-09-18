"""Read the on-screen accessibility tree, and locate nodes to act on.

This is the "eyes" half of the harness and the reason adb is kept alongside
scrcpy: the tree gives exact strings, resource ids and content descriptions,
with no OCR misreads and no coordinate guessing from pixels.

`uiautomator dump` writes to a file on the device; reading it back with `cat`
is the most portable route (some Android builds accept `dump /dev/tty`, many do
not). Dumping also legitimately fails while the UI is still settling, so the
read path retries rather than surfacing a transient error.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from typing import Iterable, Iterator

from .adb import Adb, AdbError

_DEVICE_DUMP_PATH = "/sdcard/window_dump.xml"
_BOUNDS_PATTERN = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


class UiDumpError(RuntimeError):
    """The accessibility tree could not be read."""


@dataclass
class Node:
    """One element of the accessibility tree."""

    index: int
    text: str
    resource_id: str
    class_name: str
    package: str
    content_desc: str
    clickable: bool
    enabled: bool
    focusable: bool
    focused: bool
    scrollable: bool
    long_clickable: bool
    password: bool
    checked: bool
    selected: bool
    bounds: tuple[int, int, int, int]
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = field(default=None, repr=False)

    # ---- geometry ---------------------------------------------------------

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return (left + right) // 2, (top + bottom) // 2

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def visible(self) -> bool:
        return self.width > 0 and self.height > 0

    # ---- semantics --------------------------------------------------------

    @property
    def label(self) -> str:
        """Best human-readable name for this node."""
        return self.text or self.content_desc or self.resource_id or self.class_name

    @property
    def short_class(self) -> str:
        return self.class_name.rsplit(".", 1)[-1]

    def tap_target(self) -> "Node":
        """The node that should actually receive a tap.

        Android frequently puts the label on a non-clickable TextView nested in
        a clickable container, or vice versa, so tapping the label's own centre
        can miss. Walk up to the nearest clickable ancestor that is still on
        screen; if there is none, fall back to self.
        """
        node: Node | None = self
        while node is not None:
            if node.clickable and node.enabled and node.visible:
                return node
            node = node.parent
        return self

    def walk(self) -> Iterator["Node"]:
        """Depth-first, self first, preserves document order."""
        yield self
        for child in self.children:
            yield from child.walk()

    def describe(self) -> str:
        left, top, right, bottom = self.bounds
        flags = "".join(
            letter
            for letter, value in (
                ("c", self.clickable),
                ("s", self.scrollable),
                ("!", not self.enabled),
            )
            if value
        )
        return (
            f"[{flags or '-':<3}] {self.short_class:<18} "
            f"{self.label[:52]!r:<56} [{left},{top}][{right},{bottom}]"
            + (f" id={self.resource_id}" if self.resource_id else "")
        )


def _flag(element: ElementTree.Element, name: str) -> bool:
    return element.get(name) == "true"


def _bounds(element: ElementTree.Element) -> tuple[int, int, int, int]:
    raw = element.get("bounds", "")
    match = _BOUNDS_PATTERN.search(raw)
    if not match:
        return (0, 0, 0, 0)
    left, top, right, bottom = (int(group) for group in match.groups())
    return left, top, right, bottom


def _build(element: ElementTree.Element, parent: Node | None) -> Node | None:
    if element.tag != "node":
        return None
    node = Node(
        index=int(element.get("index", "0")),
        text=element.get("text", ""),
        resource_id=element.get("resource-id", ""),
        class_name=element.get("class", ""),
        package=element.get("package", ""),
        content_desc=element.get("content-desc", ""),
        clickable=_flag(element, "clickable"),
        enabled=_flag(element, "enabled"),
        focusable=_flag(element, "focusable"),
        focused=_flag(element, "focused"),
        scrollable=_flag(element, "scrollable"),
        long_clickable=_flag(element, "long-clickable"),
        password=_flag(element, "password"),
        checked=_flag(element, "checked"),
        selected=_flag(element, "selected"),
        bounds=_bounds(element),
        parent=parent,
    )
    for child_element in element:
        child = _build(child_element, node)
        if child is not None:
            node.children.append(child)
    return node


def _union_bounds(nodes: Iterable[Node]) -> tuple[int, int, int, int]:
    nodes = [node for node in nodes if node.bounds != (0, 0, 0, 0)]
    if not nodes:
        return (0, 0, 0, 0)
    return (
        min(node.bounds[0] for node in nodes),
        min(node.bounds[1] for node in nodes),
        max(node.bounds[2] for node in nodes),
        max(node.bounds[3] for node in nodes),
    )


def parse_dump(xml_text: str) -> Node:
    """Parse a uiautomator hierarchy dump into a Node tree.

    The document root is ``<hierarchy>``, not a ``<node>``. When it holds a
    single node (what uiautomator actually produces) that node is promoted to be
    the root, so ``root`` is the real top-level view rather than a synthetic
    wrapper. Anything else gets a synthetic root spanning its children.
    """
    text = xml_text.strip()
    if not text:
        raise UiDumpError("empty UI dump")
    if "<hierarchy" not in text:
        # uiautomator reports failures as plain text on stdout.
        raise UiDumpError(f"not a hierarchy dump: {text[:200]!r}")
    # Tolerate any preamble before the XML declaration.
    start = text.find("<?xml")
    if start == -1:
        start = text.find("<hierarchy")
    try:
        element = ElementTree.fromstring(text[start:])
    except ElementTree.ParseError as error:
        raise UiDumpError(f"malformed hierarchy XML: {error}") from error

    if element.tag == "hierarchy":
        children = [node for node in (_build(child, None) for child in element) if node is not None]
        if len(children) == 1:
            return children[0]
        root = Node(
            index=0,
            text="",
            resource_id="",
            class_name="hierarchy",
            package="",
            content_desc="",
            clickable=False,
            enabled=True,
            focusable=False,
            focused=False,
            scrollable=False,
            long_clickable=False,
            password=False,
            checked=False,
            selected=False,
            bounds=_union_bounds(children),
        )
        for child in children:
            child.parent = root
        root.children = children
        return root

    root = _build(element, None)
    if root is None:
        raise UiDumpError("hierarchy root is not a <node>")
    return root


def dump_tree(adb: Adb, *, attempts: int = 3, delay: float = 0.4, compressed: bool = False) -> Node:
    """Read the accessibility tree, retrying while the UI is settling."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            args = ["uiautomator", "dump"]
            if compressed:
                args.append("--compressed")
            args.append(_DEVICE_DUMP_PATH)
            adb.shell(*args, timeout=30.0)
            xml_text = adb.shell("cat", _DEVICE_DUMP_PATH, timeout=30.0)
            root = parse_dump(xml_text)
            if root.visible:
                return root
            last_error = UiDumpError("tree root has no area (screen likely off or locking)")
        except (AdbError, UiDumpError) as error:
            last_error = error
        if attempt < attempts:
            time.sleep(delay)
    raise UiDumpError(f"could not read the UI tree after {attempts} attempts: {last_error}") from last_error


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def find_nodes(
    root: Node,
    *,
    text: str | None = None,
    resource_id: str | None = None,
    content_desc: str | None = None,
    class_name: str | None = None,
    package: str | None = None,
    clickable: bool | None = None,
    exact: bool = False,
    visible_only: bool = True,
) -> list[Node]:
    """Find nodes matching every supplied criterion.

    Matching is case-insensitive substring by default, which is what makes
    labelling robust against trailing spaces and varying casing. Pass
    ``exact=True`` for equality.
    """

    def matches(value: str, wanted: str) -> bool:
        if exact:
            return value == wanted
        return wanted.casefold() in value.casefold()

    results: list[Node] = []
    for node in root.walk():
        if visible_only and not node.visible:
            continue
        if text is not None and not matches(node.text, text):
            continue
        if resource_id is not None and not matches(node.resource_id, resource_id):
            continue
        if content_desc is not None and not matches(node.content_desc, content_desc):
            continue
        if class_name is not None and not matches(node.class_name, class_name):
            continue
        if package is not None and not matches(node.package, package):
            continue
        if clickable is not None and node.clickable != clickable:
            continue
        results.append(node)
    return results


def find_one(root: Node, **criteria) -> Node | None:
    """First match, preferring the smallest clickable hit.

    A label often appears twice: once on the TextView and once combined with
    siblings on the clickable container. The smallest match is the most specific
    and the least likely to be a full-screen wrapper.
    """
    matches = find_nodes(root, **criteria)
    if not matches:
        return None
    actionable = [node for node in matches if node.clickable]
    pool = actionable or matches
    return min(pool, key=lambda node: node.area or 1 << 30)


def tap_label(
    adb: Adb,
    label: str,
    *,
    root: Node | None = None,
    by: str = "auto",
    exact: bool = False,
    timeout: float = 0.0,
) -> Node:
    """Find a labelled element and tap it, raising with what IS visible on miss.

    ``by`` selects the matching field: ``text``, ``resource_id``,
    ``content_desc`` or ``auto`` (try text, then content-desc, then resource-id).
    """
    deadline = time.monotonic() + timeout
    while True:
        tree = root or dump_tree(adb)
        candidates: list[dict] = []
        if by in ("auto", "text"):
            candidates.append({"text": label, "exact": exact})
        if by in ("auto", "content_desc"):
            candidates.append({"content_desc": label, "exact": exact})
        if by in ("auto", "resource_id"):
            candidates.append({"resource_id": label, "exact": exact})

        for criteria in candidates:
            node = find_one(tree, **criteria)
            if node is not None:
                target = node.tap_target()
                x, y = target.center
                adb.shell("input", "tap", str(x), str(y))
                return node

        if time.monotonic() >= deadline:
            visible = [
                node.label
                for node in tree.walk()
                if node.visible and (node.text or node.content_desc) and node.clickable
            ]
            raise UiDumpError(
                f"{label!r} not found on screen. Clickable labels present: {visible[:25]}"
            )
        time.sleep(0.5)


def summarize(root: Node, *, clickable_only: bool = False, limit: int | None = None) -> list[str]:
    """Flat, printable view of the tree for inspection and debugging."""
    lines: list[str] = []
    for node in root.walk():
        if not node.visible:
            continue
        if clickable_only and not node.clickable:
            continue
        if not (node.text or node.content_desc or node.resource_id):
            continue
        lines.append(node.describe())
        if limit is not None and len(lines) >= limit:
            break
    return lines


def all_labels(root: Node, *, clickable_only: bool = True, include_descendant_text: bool = True) -> list[str]:
    """Distinct non-empty labels, useful for 'what can I tap' listings.

    Android puts a row's visible text on a non-clickable child TextView, so the
    clickable container itself often carries only a resource id. Descendant text
    is therefore folded in by default; without it a listing would show
    ``com.example.app:id/row_wifi`` where the screen says "Wi-Fi".
    """
    seen: dict[str, None] = {}

    def add(value: str) -> None:
        if value and value not in seen:
            seen[value] = None

    for node in root.walk():
        if not node.visible:
            continue
        if clickable_only and not node.clickable:
            continue
        add(node.text)
        add(node.content_desc)
        add(node.resource_id)
        if include_descendant_text:
            for descendant in node.walk():
                if descendant is not node and descendant.visible:
                    add(descendant.text)
                    add(descendant.content_desc)
    return list(seen)
