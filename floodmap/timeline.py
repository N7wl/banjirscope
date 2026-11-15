"""
Multi-date events: one dry baseline compared against several flood dates.

NISAR revisits the same orbit track every 12 days, so a flood can be followed
as it rises and recedes. Every flood date is compared with the *same*
baseline on the *same* grid, which keeps the dates comparable to each other.

Event config
------------
"before" is the baseline and "after" the main flood date, exactly as for a
single before/after pair. Extra acquisitions go in an optional list:

    "timeline": [
      {"date": "2026-10-12", "scene": "NISAR_L2_PR_GCOV_..."},
      {"date": "2026-10-24", "scene": "NISAR_L2_PR_GCOV_..."}
    ]

A config without "timeline" is a plain pair and behaves as before.
"""

from datetime import date

import numpy as np


def flood_dates(event):
    """All flood-date acquisitions of an event, oldest first.

    Merges "after" with the optional "timeline" list and returns a list of
    dicts {date, scene, default}. `default` marks the main flood date (the
    "after" entry); it is the one shown first in the app and the one the
    top-level meta.json fields describe.
    """
    entries = [{**event["after"], "default": True}]
    entries += [{**e, "default": False} for e in event.get("timeline", [])]

    seen = {}
    for entry in entries:
        for key in ("date", "scene"):
            if not entry.get(key):
                raise ValueError(f"Flood-date entry needs a '{key}': {entry}")
        date.fromisoformat(entry["date"])            # raises on a malformed date
        if entry["date"] == event["before"]["date"]:
            raise ValueError(f"{entry['date']} is the baseline date, not a flood date")
        other = seen.setdefault(entry["date"], entry)
        if other is not entry and other["scene"] != entry["scene"]:
            raise ValueError(f"Two different scenes listed for {entry['date']}")
    # "after" was inserted first, so it wins if the timeline repeats its date
    return sorted(seen.values(), key=lambda e: e["date"])


def subset_stem(entry):
    """File stem of the GCOV subset saved for one flood date.

    The main flood date keeps the name "after" so subsets fetched for a
    single pair stay valid; extra dates get the date in their name.
    """
    return "after" if entry["default"] else f"after_{entry['date']}"


def layer_prefix(entry, n_dates):
    """Sub-folder (with trailing slash) holding one date's web layers.

    A single pair keeps the flat layout docs/data/<id>/after.png; with
    several dates each one gets its own folder docs/data/<id>/<date>/.
    """
    return "" if n_dates == 1 else f"{entry['date']}/"


def shared_stretch(images, percentiles=(2, 98)):
    """One display range (vmin, vmax) in dB for a set of images.

    Every date is drawn with the same stretch, so a pixel that looks darker
    on one date really is darker, both in the swipe and across the timeline.
    """
    values = np.concatenate([img[np.isfinite(img)].ravel() for img in images])
    vmin, vmax = (float(v) for v in np.percentile(values, percentiles))
    return vmin, vmax


def days_between(start_iso, end_iso):
    """Whole days from one ISO date to another."""
    return (date.fromisoformat(end_iso) - date.fromisoformat(start_iso)).days
