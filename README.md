# PriceSentinel Agent

## Repository layout

Two pieces that work together, plus the device-side checklist they both depend on:

| Path | What it is |
|---|---|
| `src/pricesentinel/` | The agent: policy lookup, deterministic breach rules, cases, audit trail. |
| `src/pricesentinel/collect/` | The collection edge. Drives a USB-attached Android phone over adb and reads live prices off each marketplace's results page. |
| `android-harness/` | A standalone Android control harness — adb for the agent, scrcpy for the human. The collector **reuses its findings but does not import it**; the two are siblings, not layers. See `android-harness/README.md`. |
| `monitors/` | Monitor configs: the master data, floor policies and searches that define one watch. |
| `HUAWEI-P30-SETUP.md` | Device-side setup checklist for driving a **Huawei P30** (开发者选项 / USB 调试 / HDB / 纯净模式, plus probe commands). 中文. |

The harness is kept separate on purpose. It is a general-purpose way to see and
touch any Android device; the collector is one consumer of it. Merging them into
one package would make the collector's device quirks look like harness bugs.

---

PriceSentinel is an auditable, enterprise-oriented agent for cross-channel price monitoring. It turns price observations into deterministic breach decisions and trackable cases.

The product boundary is intentional:

- **Agent** orchestrates ingestion, policy lookup, evaluation, case creation and notifications.
- **Skills/adapters** provide platform-specific collection, promotion normalization, reporting and ticket integrations.
- **Database** is the source of truth for products, policies, observations, cases and audit events.

The breach decision does not depend on an LLM. An upstream connector should normalize coupons, membership discounts and bundle allocation into `net_price`; the rule engine then compares the effective price with the policy floor.

## Quick start

```bash
cd price-sentinel-agent
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m pricesentinel.cli init-db prices.sqlite3
```

Ingest a JSON payload:

```json
{
  "products": [
    {"product_id": "p-1", "brand": "Demo", "name": "Demo product", "sku": "SKU-1"}
  ],
  "policies": [
    {"product_id": "p-1", "package_type": "single", "floor_price": 100, "effective_from": "2026-01-01"}
  ],
  "observations": [
    {"observation_id": "obs-1", "product_id": "p-1", "package_type": "single", "channel": "marketplace-a", "merchant": "shop-1", "observed_at": "2026-09-08T00:00:00+00:00", "listed_price": 110, "net_price": 90, "listing_url": "https://example.com/item"}
  ]
}
```

```bash
PYTHONPATH=src python -m pricesentinel.cli ingest prices.sqlite3 payload.json
PYTHONPATH=src python -m pricesentinel.cli cases prices.sqlite3
```

## Core data model

`products` and `policies` define the controlled master data. `observations` is append-only monitoring evidence. `cases` is the operational queue, and `case_events` preserves the audit trail.

An open case is keyed by product, package, channel and merchant. Repeated breaches update the same active case instead of creating duplicate tickets.

## Collecting prices from a phone

The engine above judges prices; it does not gather them. `collect` is the
gathering half: it drives a USB-attached Android phone over `adb`, reads each
marketplace's results page the way that page allows, and feeds the prices in as
observations.

Using a real phone rather than a crawler is a deliberate choice -- the shopping
apps are already installed and already logged in, there is no API key to leak and
no scraping ban surface.

```bash
make check-monitor                       # validate the config, touch no device
make collect                             # drive the phone, ~2 min for 4 targets
make collect-dry                         # collect but write nothing
make baseline                            # what has actually been quoted so far
```

`MONITOR=` overrides the config, and `make collect` already defaults to the real
watch. `make baseline` is the one to run while floors are still unfilled: it
prints the min/quartiles/max each product has actually quoted, split by channel
and by how the price was read, so the floor comes from data instead of from a
guess. An invented floor raises cases that are not real, and nothing erodes trust
in a price watch faster than crying wolf on day one.

A monitor config is the whole definition of a watch: master data, floor
policies, and the searches to run. `policies` may be empty -- that is baseline
mode: prices are recorded and nothing is judged, which is how you find out what
a product actually sells for before setting a floor.

```json
{
  "monitor_id": "jinyin-joyinbag-watch",
  "products": [{"product_id": "jinyin-americano", "brand": "吉饮", "name": "吉饮咖啡美式鲜萃咖啡"}],
  "policies": [
    {"product_id": "jinyin-americano", "package_type": "bundle", "floor_price": 79.0, "effective_from": "2026-09-01"}
  ],
  "targets": [
    {
      "product_id": "jinyin-americano", "package_type": "bundle", "channel": "jd",
      "keyword": "吉饮咖啡美式鲜萃咖啡",
      "match_all": ["吉饮"], "match_any": ["美式", "鲜萃", "浓缩"],
      "package_types": {"bundle": ["3盒", "5盒", "18支"], "single": ["1盒", "6支"]}
    }
  ]
}
```

`match_all` / `match_any` decide which listings are *our* product;
`package_types` decides which packaging each listing is, so a single box is never
judged against a bundle floor. A listing whose packaging matches no rule is
reported as unclassified instead of being priced against the wrong policy.

Supported channels: `jd` (verified end to end), `pinduoduo`, `taobao`. See
`src/pricesentinel/collect/channels.py` for the deep links and their status.

### Two kinds of channel, because two kinds of app

Every channel declares how its results page is read back:

* **`tree`** -- `uiautomator dump`. Works when the app uses standard Android
  views. JD is like this: a search returns product names and `¥50.2` as plain
  text -- precise and cheap.
* **`screenshot`** -- `screencap` plus OCR (macOS Vision). Needed when the app
  draws its own list, so nothing reaches the accessibility tree. Taobao is like
  this: a live results page that *visibly* shows a grid of prices produced a
  93-node dump containing a toolbar and zero prices.

A `screenshot` channel usually refuses deep links as well -- Taobao's search
activities are all unexported, and `taobao://page.tb/search` is not a route (the
app treats `page.tb` as an external host and shows a security warning). So such a
channel carries a **UI driver** instead, reaching results the way a person would.
That path brings one more constraint: `input text` raises NullPointerException on
CJK, so a Chinese keyword goes in as its **pinyin**, with the app's own suggestion
list supplying the rest.

```json
{
  "product_id": "jinyin-americano", "channel": "taobao",
  "keyword": "吉饮咖啡美式鲜萃咖啡",
  "lookup": "jiyin"
}
```

`lookup` is the ASCII text actually typed; `keyword` stays human-readable and is
what the store records. `check-config` rejects a `screenshot` target whose term is
not ASCII, so that mistake surfaces at the desk rather than after a Taobao cold
start.

> **Prices read from a screenshot are lower confidence.** OCR misreads digits --
> measured on this project, a struck-through `¥288` came back as `¥233`. Every
> observation records its `extraction` in `metadata`, so a `screenshot` price is a
> *lead to confirm*, not evidence to decide on. Treat OCR-backed breaches as cases
> for a human to check.

#### A UI-driven channel has to verify, because every step is a guess

Driving an app by tapping coordinates read off a screenshot means nothing about a
tap can be assumed. The suggestion list is redrawn as it settles, so a tap that
was correctly aimed at a row a moment earlier can land in the gap that opened
under it and do *nothing at all* -- and the phone simply sits on the search door.
The same is true one step later: the results window is resumed seconds before its
custom-drawn list reaches the screen, so a screenshot taken in that gap is an
empty page.

Both failures are silent, and both look exactly like a search that found nothing.
That is the worst shape a monitoring bug can take: the report says "0 listings",
which reads like good news.

So the Taobao driver reports success only for a page it has **seen a price on**:

* A tap that produces no results page is not accepted -- the driver goes back to
  the search door, retypes the keyword, and tries the next suggestion (up to
  `_TAOBAO_SUGGESTION_ATTEMPTS`). Suggestions are tried in Taobao's own order.
* A results page that never draws a price is not accepted either: it gets a few
  more screenshots before the driver abandons that suggestion.
* Waiting is done by polling for those two conditions, not by one flat sleep, so
  a tap that worked is used about two seconds later and a tap that missed is
  noticed just as quickly. The flat sleep could not tell "still drawing" from
  "drew nothing" -- it only chose which of the two mistakes to make.
* When no suggestion works, the run fails **loudly** with the suggestions it
  tried, rather than recording a healthy zero.

The driver hands back the lines it validated, so the same page is not
photographed a second time.

### Why the collector looks the way it does

Every one of these was hit on a real device (Redmi K40, MIUI / Android 13), and
each one fails *silently* rather than loudly if ignored:

- **Chinese cannot be typed.** `input text` raises `NullPointerException` on any
  non-ASCII string. Searches therefore go through deep links with a
  percent-encoded keyword, which never types anything at all.
- **`uiautomator dump` prints a MIUI stack trace and still succeeds.** Success is
  decided by the written file, never by stderr or the exit code.
- **A failed dump leaves the previous file behind**, so the file is deleted
  before every dump; otherwise a stale screen reads as a fresh one.
- **Vendors pad titles with zero-width characters** (JD interleaves U+200B
  between every character). Left in, `吉饮@once` never matches the keyword
  `once` and the monitor reports zero breaches -- a failure that looks like good
  news.
- **A card can show several prices**: a bundle price, a member price, a coupon
  and the shelf price. The highest full price becomes `listed_price`, the lowest
  becomes `net_price`, and the rule engine judges the lowest.

## Next integrations

1. Add source adapters for approved APIs, browser collection or customer-uploaded files.
2. Add promotion and bundle normalization with explicit evidence and exceptions.
3. Replace SQLite with Postgres for multi-tenant deployments.
4. Add approval-aware notification and ticket connectors.
5. Add dashboards for breach rate, remediation SLA, recurrence and price dispersion.

## Status

This repository is an executable reference core with a working phone-driven
collector, not a production crawler. Platform access, credentials,
customer-specific policies and notification destinations must be configured by
each deployment.
