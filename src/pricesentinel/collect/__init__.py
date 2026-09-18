"""Collection edge: read live prices off a phone and turn them into observations.

The sentinel core judges prices; it does not gather them. This package is the
gathering half, deliberately separated so the judging logic stays deterministic
and testable without a device attached.

* :mod:`pricesentinel.collect.device` -- adb driver (dump, deep links, taps)
* :mod:`pricesentinel.collect.channels` -- per-marketplace deep links and the
  dump-to-listings parser
* :mod:`pricesentinel.collect.runner` -- config in, :class:`~pricesentinel.models.PriceObservation` out
"""

from .channels import CHANNELS, Channel, Listing, TreeError, extract_listings, get_channel, normalize_text
from .device import AndroidDevice, DeviceError, DumpError
from .runner import MonitorConfig, MonitorTarget, run_monitor

__all__ = [
    "CHANNELS",
    "AndroidDevice",
    "Channel",
    "DeviceError",
    "DumpError",
    "Listing",
    "MonitorConfig",
    "MonitorTarget",
    "TreeError",
    "extract_listings",
    "get_channel",
    "normalize_text",
    "run_monitor",
]
