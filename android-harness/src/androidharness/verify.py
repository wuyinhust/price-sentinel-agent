"""Post-action verification.

`adb` reports nothing about whether an input event did anything — a tap on
empty space is a success. The capture is the only ground truth, so every action
should be followed by one of these checks rather than assuming the UI moved.

The tree is preferred over pixels: it is semantic, so "the label is gone" is a
real assertion, whereas "the pixels changed" only means *something* happened.
"""

from __future__ import annotations

import hashlib
import time

from .adb import Adb
from .input import current_package
from .tree import Node, UiDumpError, dump_tree, find_one


def screen_signature(root: Node) -> str:
    """Stable hash of the visible, labelled tree.

    Includes geometry so that a scrolling list registers as changed, and labels
    so that a relabelled button does. Unlabelled layout wrappers are skipped, as
    they churn without the user-visible screen changing.
    """
    digest = hashlib.sha1()
    for node in root.walk():
        if not node.visible:
            continue
        if not (node.text or node.content_desc or node.resource_id):
            continue
        digest.update(f"{node.text}|{node.content_desc}|{node.resource_id}|{node.bounds}\n".encode())
    return digest.hexdigest()


def wait_stable(
    adb: Adb,
    *,
    timeout: float = 10.0,
    interval: float = 0.4,
    required_identical: int = 2,
) -> Node:
    """Wait until the screen stops changing, and return the settled tree.

    `required_identical` consecutive identical signatures count as settled; two
    is enough to ride out a double-buffered animation frame.
    """
    deadline = time.monotonic() + timeout
    previous: str | None = None
    streak = 0
    last_tree: Node | None = None

    while True:
        tree = dump_tree(adb)
        signature = screen_signature(tree)
        if signature == previous:
            streak += 1
            if streak >= required_identical:
                return tree
        else:
            streak = 0
            previous = signature
        last_tree = tree
        if time.monotonic() >= deadline:
            if last_tree is None:
                raise UiDumpError("screen never produced a readable tree")
            return last_tree
        time.sleep(interval)


def wait_for_text(
    adb: Adb,
    text: str,
    *,
    timeout: float = 10.0,
    interval: float = 0.4,
    exact: bool = False,
) -> Node | None:
    """Poll until `text` appears, returning its node, or None on timeout.

    Returning None rather than raising lets a caller branch on absence without
    exception handling; use `require_text` when absence is a hard failure.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            tree = dump_tree(adb, attempts=1)
        except UiDumpError:
            tree = None
        if tree is not None:
            node = find_one(tree, text=text, exact=exact)
            if node is None:
                node = find_one(tree, content_desc=text, exact=exact)
            if node is not None:
                return node
        if time.monotonic() >= deadline:
            return None
        time.sleep(interval)


def require_text(adb: Adb, text: str, *, timeout: float = 10.0, exact: bool = False) -> Node:
    node = wait_for_text(adb, text, timeout=timeout, exact=exact)
    if node is None:
        raise UiDumpError(f"{text!r} did not appear within {timeout}s")
    return node


def text_gone(
    adb: Adb,
    text: str,
    *,
    timeout: float = 10.0,
    interval: float = 0.4,
    exact: bool = False,
) -> bool:
    """Wait for `text` to disappear. Useful to confirm a dismissal worked."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            tree = dump_tree(adb, attempts=1)
        except UiDumpError:
            return True  # unreadable usually means a transition; treat as gone
        if find_one(tree, text=text, exact=exact) is None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def wait_for_app(adb: Adb, package: str, *, timeout: float = 10.0, interval: float = 0.4) -> bool:
    """Poll the focused window until `package` owns it."""
    deadline = time.monotonic() + timeout
    while True:
        if current_package(adb) == package:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def snapshot_labels(adb: Adb) -> list[str]:
    """Current clickable labels — a cheap, readable assertion target."""
    tree = dump_tree(adb)
    return [
        node.label
        for node in tree.walk()
        if node.visible and node.clickable and node.label
    ]


def diff(root_a: Node, root_b: Node) -> dict[str, list[str]]:
    """What appeared and disappeared between two trees.

    Makes a failed assertion self-explaining: the caller sees the actual
    transition instead of only "expected X".
    """

    def labelled(root: Node) -> set[str]:
        return {
            node.label
            for node in root.walk()
            if node.visible and node.label
        }

    before, after = labelled(root_a), labelled(root_b)
    return {"appeared": sorted(after - before), "disappeared": sorted(before - after)}
