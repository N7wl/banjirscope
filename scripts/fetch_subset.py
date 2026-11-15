"""
fetch_subset.py — stream only the area of interest out of each NISAR GCOV
file (instead of downloading 6.6 GB per scene) and save it as a small .npz.

The baseline is fetched once, followed by every flood date of the event
(the "after" scene plus any "timeline" entries, oldest first).

Requires a NASA Earthdata token in EARTHDATA_TOKEN (env var or .env file).

Usage:
    python scripts/fetch_subset.py --event events/thailand-chao-phraya-2026.json
    python scripts/fetch_subset.py --event ... --skip-existing
    python scripts/fetch_subset.py --event ... --local before.h5 after.h5 [more_dates.h5 ...]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floodmap.io_gcov import get_token, open_remote, read_subset, scene_url  # noqa: E402
from floodmap.timeline import flood_dates, subset_stem  # noqa: E402


def acquisitions(event):
    """(file stem, {date, scene}) for the baseline and every flood date."""
    return [("before", event["before"])] + [(subset_stem(d), d) for d in flood_dates(event)]


def fetch_event(event, out_dir, local=None, token=None, skip_existing=False):
    """Save one AOI subset per acquisition as <out_dir>/<stem>.npz.

    `local` is an optional list of already-downloaded .h5 paths in the order
    of acquisitions(event); without it each scene is streamed from ASF with
    the Earthdata `token`. Returns the list of .npz paths.
    """
    shots = acquisitions(event)
    if local is not None and len(local) != len(shots):
        order = ", ".join(shot["date"] for _, shot in shots)
        raise ValueError(f"--local needs {len(shots)} files in this order: {order} "
                         f"(got {len(local)})")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = event["processing"]

    saved = []
    for i, (stem, shot) in enumerate(shots):
        out = out_dir / f"{stem}.npz"
        saved.append(out)
        print(f"[{stem}] {shot['date']}  {shot['scene']}")
        if skip_existing and out.exists():
            print(f"    {out} exists, skipping")
            continue
        t0 = time.time()
        if local is not None:
            h5 = h5py.File(local[i], "r")
        else:
            url = scene_url(shot["scene"])
            print(f"    streaming {url}")
            h5 = open_remote(url, token)
        with h5:
            sub = read_subset(h5, event["aoi_bbox"], freq=event["frequency"],
                              pol=event["polarization"], multilook=proc["multilook"])
        np.savez_compressed(out, scene=shot["scene"], date=shot["date"], **sub)
        print(f"    saved {out}  shape={sub['power'].shape}  "
              f"EPSG:{sub['epsg']}  {time.time() - t0:.0f}s")
    return saved


def main():
    ap = argparse.ArgumentParser(description="Stream GCOV subsets for an event")
    ap.add_argument("--event", required=True, help="event config JSON")
    ap.add_argument("--local", nargs="+", metavar="H5",
                    help="use already-downloaded .h5 files instead of streaming: "
                         "the baseline first, then one per flood date, oldest first")
    ap.add_argument("--skip-existing", action="store_true",
                    help="keep subsets that were already saved (resume a partial fetch)")
    args = ap.parse_args()

    event = json.loads(Path(args.event).read_text())
    try:
        token = None if args.local else get_token()
        fetch_event(event, Path("data/processed") / event["id"], local=args.local,
                    token=token, skip_existing=args.skip_existing)
    except (RuntimeError, ValueError) as err:
        sys.exit(str(err))


if __name__ == "__main__":
    main()
