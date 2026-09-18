"""Drive the phone through a monitor definition and emit observations.

A monitor config is the whole definition of *what is being watched*: the master
data (products, floor policies) and the search targets (which keyword to run on
which channel, and how to tell our product apart from lookalikes in the
results). One file plus one command should be enough to start a watch.

Two decisions worth knowing about:

* **Observations are deduplicated by day.** The observation id folds in the
  listing fingerprint and the UTC date, so re-running a monitor updates the same
  row instead of piling up near-identical evidence, while still keeping a
  day-by-day history. Prices move slowly; the daily grain is what the case queue
  actually reasons about.
* **A target that matches nothing is reported, not silently skipped.** A search
  that returns no matching listing is the failure mode that matters most: the
  monitor looks healthy while watching nothing at all.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..models import PriceObservation, PricePolicy, Product
from .channels import CHANNELS, Listing, extract_listings, get_channel, open_search
from .device import AndroidDevice
from .ocr import OcrLine, run_ocr
from .ocr_listings import listings_from_ocr

_DEFAULT_CURRENCY = "CNY"


def _observation_id(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return f"obs-{digest[:16]}"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class MonitorTarget:
    """One (product, channel, keyword) search to run."""

    product_id: str
    package_type: str
    channel: str
    keyword: str
    match_all: tuple[str, ...] = ()
    match_any: tuple[str, ...] = ()
    package_types: dict[str, tuple[str, ...]] = field(default_factory=dict)
    merchant: str | None = None
    max_listings: int = 5
    # What gets typed on a channel that is driven through its UI. May differ
    # from ``keyword``: Taobao cannot receive Chinese over adb, so a Chinese
    # brand is typed as its pinyin and the suggestion list supplies the rest.
    lookup: str | None = None

    @classmethod
    def from_dict(cls, raw: dict) -> "MonitorTarget":
        rules = raw.get("package_types") or {}
        return cls(
            product_id=raw["product_id"],
            package_type=raw.get("package_type", "single"),
            channel=raw["channel"],
            keyword=raw["keyword"],
            match_all=tuple(raw.get("match_all", ())),
            match_any=tuple(raw.get("match_any", ())),
            package_types={key: tuple(value) for key, value in rules.items()},
            merchant=raw.get("merchant"),
            max_listings=int(raw.get("max_listings", 5)),
            lookup=raw.get("lookup"),
        )

    @property
    def search_term(self) -> str:
        """The text actually sent to the device."""
        return self.lookup or self.keyword

    def matches(self, listing: Listing) -> bool:
        """Whether this listing is our product rather than a lookalike.

        Matching is done on the *normalized* title, so the zero-width padding
        vendors interleave through titles cannot hide a match.
        """
        haystack = listing.title.casefold()
        if any(needle.casefold() not in haystack for needle in self.match_all):
            return False
        if self.match_any and not any(needle.casefold() in haystack for needle in self.match_any):
            return False
        return True

    def classify(self, listing: Listing) -> str | None:
        """Which packaging this listing is, or ``None`` if it cannot be told.

        A keyword search returns a mix of pack sizes -- one box, three boxes, a
        gift set -- while floor prices are set per packaging. Judging a single
        box against a bundle floor manufactures breaches that do not exist, so a
        listing whose packaging cannot be determined is reported as unclassified
        rather than priced against the wrong policy.

        Rules are evaluated in config order, so put the most specific first
        (``3盒`` before ``盒``).
        """
        if not self.package_types:
            return self.package_type
        title = listing.title.casefold()
        for package_type, needles in self.package_types.items():
            if any(needle.casefold() in title for needle in needles):
                return package_type
        return None

    def merge_terms(self) -> tuple[str, ...]:
        return self.match_all + self.match_any


@dataclass
class MonitorConfig:
    monitor_id: str
    targets: list[MonitorTarget] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    policies: list[PricePolicy] = field(default_factory=list)
    currency: str = _DEFAULT_CURRENCY
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "MonitorConfig":
        return cls(
            monitor_id=raw.get("monitor_id") or "unnamed-monitor",
            targets=[MonitorTarget.from_dict(item) for item in raw.get("targets", [])],
            products=[Product(**item) for item in raw.get("products", [])],
            policies=[PricePolicy(**item) for item in raw.get("policies", [])],
            currency=raw.get("currency", _DEFAULT_CURRENCY),
            notes=raw.get("notes", ""),
        )

    @classmethod
    def load(cls, path: str | Path) -> "MonitorConfig":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate(self) -> list[str]:
        """Return the problems that would make this monitor misleading."""
        problems: list[str] = []
        if not self.targets:
            problems.append("no targets: this monitor would watch nothing")
        known = {product.product_id for product in self.products}
        policied = {(p.product_id, p.package_type) for p in self.policies}
        for target in self.targets:
            if self.products and target.product_id not in known:
                problems.append(
                    f"target {target.product_id!r} has no product entry"
                )
            if self.policies:
                declared = (
                    tuple(target.package_types) if target.package_types else (target.package_type,)
                )
                for package_type in declared:
                    if (target.product_id, package_type) not in policied:
                        problems.append(
                            f"target {target.product_id!r}/{package_type!r} has no floor policy, "
                            "so breaches can never be raised for it"
                        )
            channel = CHANNELS.get(target.channel)
            if channel is None:
                problems.append(
                    f"unknown channel {target.channel!r}; known: {', '.join(sorted(CHANNELS))}"
                )
            elif channel.extraction == "screenshot" and not target.search_term.isascii():
                # Caught here rather than on the phone: input text raises
                # NullPointerException on CJK, so a Chinese term would burn a
                # full collection run before failing.
                problems.append(
                    f"target {target.product_id!r} on {target.channel!r} would type "
                    f"{target.search_term!r}, which cannot be sent over adb (input text "
                    'handles ASCII only); set "lookup" to an ASCII search term'
                )
            if not target.merge_terms():
                problems.append(
                    f"target {target.product_id!r} on {target.channel!r} has no match terms: "
                    "every listing would match, including competitors"
                )
        return problems


@dataclass
class TargetOutcome:
    """What happened for one target, including the boring-but-important cases."""

    target: MonitorTarget
    listings: list[Listing] = field(default_factory=list)
    matched: list[Listing] = field(default_factory=list)
    unclassified: list[Listing] = field(default_factory=list)
    observations: list[PriceObservation] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error:
            return f"{self.target.channel}: FAILED -- {self.error}"
        if not self.listings:
            return f"{self.target.channel}: 0 listings parsed (page may not have settled)"
        if not self.matched:
            sample = ", ".join(repr(item.title[:28]) for item in self.listings[:3])
            return (
                f"{self.target.channel}: {len(self.listings)} listings, none matched "
                f"{self.target.merge_terms()} (saw {sample})"
            )
        text = f"{self.target.channel}: {len(self.matched)}/{len(self.listings)} matched"
        if self.unclassified:
            text += (
                f", {len(self.unclassified)} skipped as unclassified packaging "
                "(no package_types rule matched)"
            )
        return text


@dataclass
class MonitorRun:
    monitor_id: str
    outcomes: list[TargetOutcome] = field(default_factory=list)
    observed_at: str = ""

    @property
    def observations(self) -> list[PriceObservation]:
        return [item for outcome in self.outcomes for item in outcome.observations]

    @property
    def problems(self) -> list[str]:
        return [outcome.summary() for outcome in self.outcomes if not outcome.matched]

    def describe(self) -> str:
        lines = [f"monitor {self.monitor_id!r} at {self.observed_at}"]
        lines += [f"  {outcome.summary()}" for outcome in self.outcomes]
        return "\n".join(lines)


def read_screen_lines(device: AndroidDevice) -> list[OcrLine]:
    """Recognise every line of text on the current screen.

    The screenshot goes into a temporary directory that is torn down as soon as
    OCR has run: these are 1-2MB PNGs, and a monitor that runs daily would
    otherwise pile them up forever.
    """
    with tempfile.TemporaryDirectory() as tmp:
        image = device.screenshot(Path(tmp) / "results.png")
        return run_ocr(image)


def read_listings_from_screen(device: AndroidDevice) -> list[Listing]:
    """Read listings off the pixels, for a page with no accessibility tree."""
    return listings_from_ocr(read_screen_lines(device))


def collect_target(
    device: AndroidDevice,
    target: MonitorTarget,
    *,
    currency: str = _DEFAULT_CURRENCY,
    observed_at: str | None = None,
    sleep=time.sleep,
) -> TargetOutcome:
    """Run one search on the phone and turn matched listings into observations.

    ``sleep`` is injectable so the pipeline can be tested without waiting out
    the real page-settle delays.
    """
    outcome = TargetOutcome(target=target)
    channel = get_channel(target.channel)
    uri = channel.search_uri(target.keyword)
    moment = observed_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    try:
        lines = open_search(
            device,
            channel,
            target.keyword,
            term=target.search_term,
            needles=target.merge_terms(),
            sleep=sleep,
        )
        if channel.extraction == "screenshot":
            # The channel's driver only returns once it has seen a price on the
            # page, so when it hands back its own reading, use it: a second
            # screenshot would add nothing but another chance to catch the list
            # mid-scroll.
            outcome.listings = listings_from_ocr(
                lines if lines is not None else read_screen_lines(device)
            )
        else:
            outcome.listings = extract_listings(device.read_tree())
    except Exception as error:  # noqa: BLE001 - surfaced into the outcome
        outcome.error = f"{type(error).__name__}: {error}"
        return outcome

    day = moment[:10]
    for listing in outcome.listings:
        if not target.matches(listing):
            continue
        outcome.matched.append(listing)
        package_type = target.classify(listing)
        if package_type is None:
            # Pricing this against the target's default packaging would invent
            # breaches, so it is surfaced instead of guessed at.
            outcome.unclassified.append(listing)
            continue
        merchant = listing.merchant or target.merchant or channel.label
        outcome.observations.append(
            PriceObservation(
                observation_id=_observation_id(
                    target.product_id,
                    package_type,
                    target.channel,
                    merchant,
                    listing.fingerprint,
                    day,
                ),
                product_id=target.product_id,
                package_type=package_type,
                channel=target.channel,
                merchant=merchant,
                observed_at=moment,
                listed_price=listing.listed_price,
                net_price=listing.net_price,
                currency=currency,
                evidence_url=uri,
                source=f"android:{target.channel}:{device.serial}",
                metadata={
                    "title": listing.title,
                    "fingerprint": listing.fingerprint,
                    "prices_seen": list(listing.prices_seen),
                    "keyword": target.keyword,
                    # "tree" is read from the accessibility tree; "screenshot"
                    # went through OCR, where a digit can be misread. Downstream
                    # consumers need to tell the two apart.
                    "extraction": channel.extraction,
                },
            )
        )
    return outcome


def run_monitor(
    config: MonitorConfig,
    device: AndroidDevice,
    *,
    channels: tuple[str, ...] | None = None,
    sleep=time.sleep,
) -> MonitorRun:
    """Run every target (optionally only some channels) and collect the evidence."""
    moment = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    run = MonitorRun(monitor_id=config.monitor_id, observed_at=moment)
    for target in config.targets:
        if channels and target.channel not in channels:
            continue
        run.outcomes.append(
            collect_target(
                device,
                target,
                currency=config.currency,
                observed_at=moment,
                sleep=sleep,
            )
        )
    return run
