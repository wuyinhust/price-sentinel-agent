"""Turn an accessibility dump into priced listings, and know how to reach each channel.

Parsing is structural rather than text-only, because a flat list of strings from
a search page is useless: it says the page contains ``¥23.66`` and three dozen
product names, but not which belongs to which.

Everything is anchored to the **card** -- the list row an offer lives in. A card
is found by walking up from a price to the nearest clickable ancestor that is
big enough to be a row. One card yields exactly one listing; emitting one
listing per price node instead is a real bug that silently double-counts every
offer that shows two prices.

Four hostile details are handled here, each of which corrupts data silently
rather than failing loudly:

* **Zero-width characters.** JD interleaves U+200B between every character of a
  title to defeat naive matching. Left in place, ``"吉饮@once"`` never matches
  the keyword ``"once"`` and the monitor reports zero breaches -- the worst
  possible failure, because it looks like good news.
* **Split currency.** Some layouts put ``¥`` and the digits in sibling nodes.
* **Secondary prices.** A card can carry a bundle price (``2件单价约¥48.7``), a
  member price (``¥23.66 PLUS到手价``) and a coupon (``优惠¥16.3``) beside the
  shelf price. Only the highest full price is the listed price; the lowest
  becomes ``net_price`` so the rule engine judges the most aggressive visible
  offer.
* **Obfuscated ids.** JD ships resource-ids like ``ao3`` / ``anv`` that change
  between app builds, so nothing here keys off them.

Adding a channel means adding one :class:`Channel` entry -- a package, a deep
link template and how long to wait. The parser is shared.
"""

from __future__ import annotations

import re
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from .device import AndroidDevice, DeviceError
from .ocr import OcrLine, run_ocr

# Characters vendors sprinkle through titles and that must never reach the
# keyword matcher: zero-width space/non-joiner/joiner, word joiner, BOM.
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)

_CURRENCY = "\u00a5\uffe5"  # ¥ and ￥
_SYMBOL_ONLY = re.compile(rf"^[{_CURRENCY}]$")
_PRICE_ONLY = re.compile(rf"^[{_CURRENCY}]\s*\d+(?:[.,]\d+)?$")
_BARE_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# ``优惠¥16.3`` / ``券后¥23`` -- a promotional reference, not the shelf price.
_DISCOUNT_MARKERS = ("优惠", "券后", "满减", "立减", "返现", "红包")
# ``2件单价约¥48.7`` -- a per-unit price when bought in quantity.
_BUNDLE_MARKERS = ("件单价", "件均", "单价约", "拼单价", "团购价")

# Ranked: the first marker that matches wins, so a real shop name beats a badge.
_SHOP_MARKERS = (
    "旗舰店",
    "官方店",
    "专营店",
    "专卖店",
    "自营店",
    "海外店",
    "自营",
)

_LEADING_NOISE = re.compile(r"^[\s&|｜•·\-–—:：,，]+")
_TRAILING_NOISE = re.compile(r"[\s&|｜•·\-–—:：,，]+$")
# ``自营,`` / ``京东自营,`` -- a shop prefix glued onto the title by the vendor.
_SHOP_PREFIX = re.compile(r"^(?:[\u4e00-\u9fff]{1,6}|\w{1,6})[,，]\s*")
_MARKETING_TAG = re.compile(r"[|｜]{1,}")

_MIN_CARD_AREA = 30_000
_MIN_TITLE_CHARS = 6
_MAX_MERCHANT_CHARS = 30


@dataclass(frozen=True)
class Listing:
    """One priced offer as it appears on a search results page."""

    title: str
    listed_price: float
    net_price: float | None = None
    merchant: str | None = None
    prices_seen: tuple[str, ...] = ()
    position: int = 0

    @property
    def price(self) -> float:
        """The price the rule engine will judge: the most aggressive visible one."""
        return self.net_price if self.net_price is not None else self.listed_price

    @property
    def fingerprint(self) -> str:
        """Stable identity for this offer across runs, for change detection.

        Titles carry specs that drift between runs (stock counts, badges), so the
        fingerprint is built from the offer's own name and the shop selling it.
        """
        material = f"{self.title}|{self.merchant or ''}".casefold()
        normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", material)
        return normalized[:80] or f"pos{self.position}"


class TreeError(RuntimeError):
    """The dump is not usable."""


def normalize_text(value: str) -> str:
    """Strip zero-width padding, normalize width and squeeze whitespace.

    NFKC is applied so full-width digits and latin letters collapse onto their
    ASCII forms -- ``２５０ｇ`` and ``250g`` must compare equal.
    """
    stripped = unicodedata.normalize("NFKC", value.translate(_ZERO_WIDTH))
    return re.sub(r"\s+", " ", stripped).strip()


def _clean_label(value: str) -> str:
    return _TRAILING_NOISE.sub("", _LEADING_NOISE.sub("", normalize_text(value)))


def _strip_shop_prefix(value: str) -> str:
    """Drop the vendor's glued-on shop decoration (``自营,`` / ``京东自营,``)."""
    return _SHOP_PREFIX.sub("", value).strip()


def _bounds(element: ElementTree.Element) -> tuple[int, int, int, int]:
    match = _BOUNDS.match(element.get("bounds") or "")
    if not match:
        return (0, 0, 0, 0)
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]


def _area(element: ElementTree.Element) -> int:
    left, top, right, bottom = _bounds(element)
    return max(0, right - left) * max(0, bottom - top)


def _label(element: ElementTree.Element, key: str) -> str:
    return _clean_label(element.get(key) or "")


def _is_price(value: str) -> bool:
    return bool(_PRICE_ONLY.match(value))


def _has_currency_digits(value: str) -> bool:
    return bool(re.search(rf"[{_CURRENCY}]\s*\d", value))


def _is_noise_price(value: str) -> bool:
    """A price-shaped string that is a promotion, not the shelf price."""
    return any(marker in value for marker in _DISCOUNT_MARKERS + _BUNDLE_MARKERS)


def _price_value(raw: str) -> float | None:
    try:
        return float(re.sub(r"[^0-9.]", "", raw))
    except ValueError:
        return None


def _glue_split_price(element: ElementTree.Element, parents: dict) -> str | None:
    """Rebuild ``¥`` + ``1363`` when a layout splits them into sibling nodes.

    Without this the symbol-only node is dropped and the digits read as a bare
    number, so the offer is lost entirely.
    """
    if not _SYMBOL_ONLY.match(_label(element, "text")):
        return None
    parent = parents.get(element)
    if parent is None:
        return None
    siblings = [child for child in parent if child.tag == "node"]
    try:
        index = siblings.index(element)
    except ValueError:
        return None
    for following in siblings[index + 1 : index + 3]:
        candidate = _label(following, "text")
        if _BARE_NUMBER.match(candidate):
            return f"{_CURRENCY[0]}{candidate}"
    return None


def _card_of(node: ElementTree.Element, parents: dict) -> ElementTree.Element | None:
    """Nearest clickable ancestor large enough to be a list row."""
    current = node
    for _ in range(12):
        current = parents.get(current)
        if current is None:
            return None
        if current.get("clickable") == "true" and _area(current) >= _MIN_CARD_AREA:
            return current
    return None


def _card_title(card: ElementTree.Element) -> str | None:
    """The product name, found structurally rather than by length.

    Vendors render the title in a node whose ``text`` and ``content-desc`` hold
    the same string, differing only by a shop decoration prefix
    (``'& 吉饮@once浓缩拿铁3盒18支'`` vs ``'自营,吉饮@once浓缩拿铁3盒18支'``). Picking the
    longest string instead would grab a marketing line such as
    ``'27/6到期|接受者可拍|临期清仓|国产'``, which is longer than many real titles.
    """
    best: str | None = None
    for node in card.iter("node"):
        text = _label(node, "text")
        desc = _label(node, "content-desc")
        if len(text) < _MIN_TITLE_CHARS or _is_price(text) or _BARE_NUMBER.match(text):
            continue
        if not desc:
            continue
        shorter, longer = sorted((text, desc), key=len)
        if not shorter or shorter not in longer:
            continue  # text and desc describe different things; not the title node
        candidate = _strip_shop_prefix(_strip_shop_prefix(longer))
        if _MARKETING_TAG.search(candidate):
            continue
        if best is None or len(candidate) > len(best):
            best = candidate
    return best


def _card_prices(card: ElementTree.Element, parents: dict) -> list[str]:
    """Full ``¥NN.N`` prices in the card, promotions and bundle prices excluded.

    Split ``¥`` + digits pairs are only consulted when the card has no full
    price, so Taobao's split layout still works without letting JD's
    ``2件单价约`` bundle price masquerade as a shelf price.
    """
    def collect(allow_split: bool) -> list[str]:
        found: list[str] = []
        for node in card.iter("node"):
            text = _label(node, "text")
            composed = _glue_split_price(node, parents) if allow_split else None
            value = composed or text
            if not _is_price(value) or _is_noise_price(value):
                continue
            # A bare ``¥`` glued from siblings is allowed; the raw text ``¥``
            # alone is not, or every card would yield a priceless entry.
            if not composed and _SYMBOL_ONLY.match(text):
                continue
            if value not in found:
                found.append(value)
        return found

    priced = collect(allow_split=False)
    return priced or collect(allow_split=True)


def _card_merchant(card: ElementTree.Element) -> str | None:
    """The shop name, ranked so a real name beats a badge like ``年度五星店铺``."""
    best: tuple[int, str] | None = None
    for node in card.iter("node"):
        text = _label(node, "text")
        if not text or len(text) > _MAX_MERCHANT_CHARS:
            continue
        for rank, marker in enumerate(_SHOP_MARKERS):
            if text.endswith(marker) or marker in text:
                if best is None or rank < best[0]:
                    best = (rank, text)
                break
    return best[1] if best else None


def extract_listings(xml_text: str, *, limit: int | None = None) -> list[Listing]:
    """Pull every priced offer out of an accessibility dump.

    Pure function over the dump text, so it is unit-testable without a phone.
    """
    if "<hierarchy" not in xml_text:
        raise TreeError("not a hierarchy dump")
    start = xml_text.find("<?xml")
    if start == -1:
        start = xml_text.find("<hierarchy")
    try:
        root = ElementTree.fromstring(xml_text[start:])
    except ElementTree.ParseError as error:
        raise TreeError(f"malformed hierarchy XML: {error}") from error

    parents = {child: parent for parent in root.iter() for child in parent}

    # Walk cards, not prices: a card showing a member price beside a shelf price
    # must produce one listing, not two.
    cards: list[ElementTree.Element] = []
    for node in root.iter("node"):
        # Glue first: a symbol-only node is not a price on its own, so testing
        # the raw text first would drop every split-currency offer.
        text = _glue_split_price(node, parents) or _label(node, "text")
        if not _is_price(text):
            continue
        card = _card_of(node, parents)
        if card is not None and all(card is not existing for existing in cards):
            cards.append(card)

    listings: list[Listing] = []
    for card in cards:
        raw_prices = _card_prices(card, parents)
        values = [value for value in (_price_value(raw) for raw in raw_prices) if value and value > 0]
        if not values:
            continue
        title = _card_title(card)
        if not title:
            continue
        listings.append(
            Listing(
                title=title,
                listed_price=max(values),
                net_price=min(values) if len(values) > 1 else None,
                merchant=_card_merchant(card),
                prices_seen=tuple(raw_prices),
                position=len(listings),
            )
        )
        if limit is not None and len(listings) >= limit:
            break
    return listings


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Channel:
    """How to reach one marketplace's search results on the phone."""

    key: str
    label: str
    package: str
    uri_template: str
    submit_keycode: str | None = None
    settle_seconds: float = 12.0
    submit_settle_seconds: float = 12.0
    verified: bool = True
    notes: str = ""
    # How the results page is read back:
    #   "tree"       -- uiautomator dump; the app uses standard Android views
    #   "screenshot" -- screencap + OCR; the app draws its own list, so nothing
    #                   reaches the accessibility tree (see ocr_listings)
    extraction: str = "tree"
    # A screenshot channel usually refuses deep links too, so it carries a driver
    # that reaches the results page by operating the app's own UI. A driver that
    # has already read the page returns its lines, so the caller does not pay for
    # a second screenshot; ``None`` means "read the screen yourself".
    # Signature: (device, term, needles, *, sleep) -> list[OcrLine] | None
    driver: Callable[..., list[OcrLine] | None] | None = None

    def search_uri(self, keyword: str) -> str:
        return self.uri_template.format(keyword=quote(keyword, safe=""))


_TAOBAO_START_SECONDS = 18.0
_TAOBAO_SUGGEST_SECONDS = 4.0
_TAOBAO_DOOR_SECONDS = 4.0

# Waiting is done by polling rather than by one flat sleep, because both
# outcomes have to be noticed as soon as they happen. A tap that worked puts the
# results page up in about two seconds; a tap that *missed* has to be recognised
# quickly too, or every retry costs a full wait and the retry loop becomes too
# expensive to keep.
_TAOBAO_POLL_SECONDS = 1.5
_TAOBAO_RESULT_POLLS = 8  # ~12s for the results activity to be resumed
_TAOBAO_SETTLE_SECONDS = 2.0
_TAOBAO_SETTLE_POLLS = 4  # then up to 4 further screenshots waiting for prices

# One tap is not enough. The suggestion list is re-laid-out while it settles, so
# a tap aimed at a row read a moment earlier can land in the gap that opened
# under it and do exactly nothing. From the outside that is invisible -- the
# phone simply stays on the search door -- which is how a monitor comes to
# report "0 listings, page may not have settled" while looking perfectly
# healthy. So each candidate gets tapped, and a candidate that produces no
# priced results page is abandoned in favour of the next one.
_TAOBAO_SUGGESTION_ATTEMPTS = 3

# The results screen. ``MainSearchResultActivity`` is the only one observed, but
# every variant is named after its parent, so the suffix alone identifies it.
_TAOBAO_RESULT_MARKER = "SearchResultActivity"


@dataclass(frozen=True)
class _Suggestion:
    """One row of the search-suggestion list, reduced to a point to tap."""

    text: str
    x: int
    y: int


def _suggestions(
    lines: list[OcrLine],
    term: str,
    needles: tuple[str, ...],
    width: int,
    height: int,
) -> list["_Suggestion"]:
    """The matching suggestion rows, top-down, as tap points.

    Top-down is Taobao's own relevance order, so the first row is normally the
    best query to run; the later ones exist because the first may yet fail to
    produce a page with prices.

    The row carrying the *typed* text is skipped. It is the input field echoing
    back what was just sent, it sits directly above the list, and it looks
    exactly like a suggestion -- but tapping it re-runs the search door instead
    of a search. Equality is the test, since the echo is the term and a real
    suggestion for a brand never is.
    """
    wanted = tuple(needle.casefold() for needle in needles if needle)
    found: list[_Suggestion] = []
    for line in sorted(lines, key=lambda item: item.y, reverse=True):
        text = normalize_text(line.text)
        if not text or text.casefold() == term.casefold():
            continue
        if not any(needle in text.casefold() for needle in wanted):
            continue
        left, top, right, bottom = line.to_pixels(width, height)
        found.append(_Suggestion(text=text, x=(left + right) // 2, y=(top + bottom) // 2))
    return found


def _type_term(device: AndroidDevice, term: str, *, sleep) -> None:
    """Type the keyword into the search box, clearing it first."""
    # MOVE_END then DELETE x24. The app remembers the previous query, and
    # appending to it would silently search for something else.
    device.shell("input", "keyevent", "123", *(["67"] * 24))
    device.shell("input", "text", term)
    sleep(_TAOBAO_SUGGEST_SECONDS)


def _return_to_door(device: AndroidDevice, term: str, *, sleep) -> bool:
    """Back out of the last attempt's page and retype the term.

    A retry does not begin where the last one did: a failed attempt may have
    left the phone on a results page rather than on the door, and either way the
    query has to be in the box for the suggestion list to exist at all.
    """
    for _ in range(4):
        if "SearchDoorActivity" in device.screen_state().focus:
            break
        device.key("4")
        sleep(_TAOBAO_DOOR_SECONDS)
    if "SearchDoorActivity" not in device.screen_state().focus:
        return False
    _type_term(device, term, sleep=sleep)
    return True


def _read_settled_results(device: AndroidDevice, *, sleep) -> list[OcrLine] | None:
    """OCR the results page once it is open *and* actually showing a price.

    ``None`` means this suggestion produced nothing usable, which is the
    caller's signal to try the next one.

    Waiting for the activity alone is not enough. A custom-drawn list is
    resumed seconds before it reaches the screen, and a screenshot taken in that
    gap is an empty page -- indistinguishable, to any later reader, from a
    search that genuinely found nothing. Waiting for a flat interval is the
    other half of the same mistake in reverse: it cannot tell "still drawing"
    from "drew nothing", so it either wastes a full wait or gives up too early.
    Watching for a *price* answers both.
    """
    # Local import: ocr_listings is a consumer of this module, so importing it
    # at module level would be circular.
    from .ocr_listings import priced_lines

    for _ in range(_TAOBAO_RESULT_POLLS):
        sleep(_TAOBAO_POLL_SECONDS)
        if _TAOBAO_RESULT_MARKER in device.screen_state().focus:
            break
    else:
        return None  # the tap never opened a results page at all

    for attempt in range(_TAOBAO_SETTLE_POLLS):
        with tempfile.TemporaryDirectory() as tmp:
            lines = run_ocr(device.screenshot(Path(tmp) / "results.png"))
        if priced_lines(lines):
            return lines
        if attempt + 1 < _TAOBAO_SETTLE_POLLS:
            sleep(_TAOBAO_SETTLE_SECONDS)
    return None


def taobao_search(
    device: AndroidDevice,
    term: str,
    needles: tuple[str, ...],
    *,
    sleep=time.sleep,
) -> list[OcrLine] | None:
    """Drive Taobao's own UI to a search results page.

    Taobao is why this fallback exists at all. Every search activity is
    unexported -- ``MainSearchResultActivity``, ``SearchDoorActivity``,
    ``MainSearchRouteActivity`` and even ``TBMainActivity`` itself -- so
    ``am start`` is refused with a Permission Denial. ``taobao://page.tb/search``
    is not a route either: the app treats ``page.tb`` as an external host and
    shows a security warning instead of searching (Tmall's ``page.tm`` is a real
    route; Taobao registers no ``page.tb``). The https fallback lands on a login
    wall, because the app's session is not shared with the browser.

    So the route that works is the one a person would take:

    * **Launch with monkey.** The launcher intent is the only entry point that
      is not subject to the export check.
    * **Type the keyword in ASCII.** ``input text`` raises NullPointerException
      on CJK, so a Chinese brand goes in as its pinyin and the *suggestion list*
      becomes the entry point -- typing ``jiyin`` suggests 吉饮咖啡 first. The
      suggestion list is custom-drawn too, so it is located by OCR rather than
      by the accessibility tree.

    Because every step of that is a tap at a coordinate read off a screenshot,
    success cannot be assumed from having tapped at all. This driver reports a
    page only once prices are on it, and works down the suggestion list until
    one of them gets there.

    Returns the recognised lines of the settled page, so the caller need not
    photograph it a second time.
    """
    device.shell(
        "monkey", "-p", "com.taobao.taobao", "-c", "android.intent.category.LAUNCHER", "1"
    )
    sleep(_TAOBAO_START_SECONDS)
    focus = device.screen_state().focus
    if "com.taobao.taobao" not in focus:
        raise DeviceError(f"Taobao never reached the foreground (focus={focus!r})")

    for _ in range(4):
        if "SearchDoorActivity" in device.screen_state().focus:
            break
        device.key("4")
        sleep(_TAOBAO_DOOR_SECONDS)
    if "SearchDoorActivity" not in device.screen_state().focus:
        raise DeviceError("could not reach Taobao's search screen")

    _type_term(device, term, sleep=sleep)
    width, height = device.screen_size()

    tried: list[str] = []
    for attempt in range(_TAOBAO_SUGGESTION_ATTEMPTS):
        if attempt and not _return_to_door(device, term, sleep=sleep):
            break
        with tempfile.TemporaryDirectory() as tmp:
            door = run_ocr(device.screenshot(Path(tmp) / "suggest.png"))
        candidates = [
            item
            for item in _suggestions(door, term, needles, width, height)
            if item.text not in tried
        ]
        if not candidates:
            if tried:
                break  # every matching suggestion has now been tried
            raise DeviceError(
                f"no suggestion matched {needles!r} after typing {term!r}; "
                "the channel may have changed its suggestion list"
            )
        choice = candidates[0]
        tried.append(choice.text)
        device.tap(choice.x, choice.y)
        page = _read_settled_results(device, sleep=sleep)
        if page is not None:
            return page

    raise DeviceError(
        f"typed {term!r} and tapped {tried!r}, but none of them opened a search "
        "results page showing a price; Taobao may have changed its search UI"
    )


CHANNELS: dict[str, Channel] = {
    "jd": Channel(
        key="jd",
        label="京东",
        package="com.jingdong.app.mall",
        # JD's own open scheme. The JSON payload carries the search intent, and
        # percent-encoding the Chinese keyword sidesteps the broken `input text`
        # (which raises NullPointerException on any non-ASCII string).
        uri_template=(
            "openapp.jdmobile://virtual?params="
            "%7B%22category%22%3A%22jump%22%2C%22des%22%3A%22search%22%2C"
            "%22keyWord%22%3A%22{keyword}%22%7D"
        ),
        # The deep link only prefills the search box; ENTER submits it. Observed
        # on device: without this the phone parks on the input page, which has
        # suggestion chips but no prices at all.
        submit_keycode="66",
        notes="verified end to end on device: ProductListActivity with live prices",
    ),
    "pinduoduo": Channel(
        key="pinduoduo",
        label="拼多多",
        package="com.xunmeng.pinduoduo",
        uri_template="pinduoduo://com.xunmeng.pinduoduo/search_result.html?search_key={keyword}",
        notes="deep link reaches NewPageActivity; result-page price nodes not yet confirmed",
    ),
    "taobao": Channel(
        key="taobao",
        label="淘宝",
        package="com.taobao.taobao",
        # There is no usable deep link here. Every search activity is unexported,
        # so a taobao:// search is refused by the framework, and tbopen:// only
        # ever lands on the home page or a webview security warning. The UI
        # driver is the route that actually reaches a results page.
        uri_template="taobao://m.taobao.com/",
        extraction="screenshot",
        driver=taobao_search,
        notes=(
            "driven through the UI (pinyin + suggestion list) because no search "
            "activity is exported; the results list is custom-drawn, so prices "
            "are OCR-read and are lower confidence than a tree channel"
        ),
    ),
}


def open_search(
    device: AndroidDevice,
    channel: Channel,
    keyword: str,
    *,
    term: str | None = None,
    needles: tuple[str, ...] = (),
    sleep=time.sleep,
) -> list[OcrLine] | None:
    """Put the phone on a channel's results page for one keyword.

    ``term`` is what actually gets typed when the channel is driven through its
    UI and may differ from ``keyword`` -- Taobao needs the pinyin, not the
    Chinese. ``needles`` are the words used to pick the right suggestion.

    Returns the recognised lines of the results page when the channel's own
    driver has already read it, else ``None``.
    """
    if channel.extraction == "screenshot":
        if channel.driver is None:
            raise DeviceError(f"channel {channel.key!r} has no UI driver")
        return channel.driver(device, term or keyword, needles or (keyword,), sleep=sleep)

    device.open_uri(channel.search_uri(keyword), package=channel.package)
    if channel.submit_keycode:
        # The deep link only prefills the search box on some channels, so the
        # search still has to be submitted before any results exist.
        sleep(2.0)
        device.key(channel.submit_keycode)
        sleep(channel.submit_settle_seconds)
    else:
        sleep(channel.settle_seconds)
    return None


def get_channel(key: str) -> Channel:
    try:
        return CHANNELS[key]
    except KeyError:
        raise KeyError(
            f"unknown channel {key!r}; known: {', '.join(sorted(CHANNELS))}"
        ) from None
