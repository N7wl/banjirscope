"""
fetch_subset.py — stream only the area of interest out of each NISAR GCOV
file (instead of downloading 2 x 6.6 GB) and save it as a small .npz.

Requires a NASA Earthdata token in EARTHDATA_TOKEN (env var or .env file).

Usage:
    python scripts/fetch_subset.py --event events/thailand-chao-phraya-2026.json
    python scripts/fetch_subset.py --event ... --local path/to/before.h5 path/to/after.h5
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


def main():
    ap = argparse.ArgumentParser(description="Stream GCOV subsets for an event")
    ap.add_argument("--event", required=True, help="event config JSON")
    ap.add_argument("--local", nargs=2, metavar=("BEFORE_H5", "AFTER_H5"),
                    help="use already-downloaded .h5 files instead of streaming")
    args = ap.parse_args()

    event = json.loads(Path(args.event).read_text())
    out_dir = Path("data/processed") / event["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = event["processing"]
    try:
        token = None if args.local else get_token()
    except RuntimeError as err:
        sys.exit(str(err))

    for i, which in enumerate(("before", "after")):
        scene = event[which]["scene"]
        print(f"[{which}] {event[which]['date']}  {scene}")
        t0 = time.time()
        if args.local:
            h5 = h5py.File(args.local[i], "r")
        else:
            url = scene_url(scene)
            print(f"    streaming {url}")
            h5 = open_remote(url, token)
        with h5:
            sub = read_subset(h5, event["aoi_bbox"], freq=event["frequency"],
                              pol=event["polarization"], multilook=proc["multilook"])
        out = out_dir / f"{which}.npz"
        np.savez_compressed(out, scene=scene, date=event[which]["date"], **sub)
        print(f"    saved {out}  shape={sub['power'].shape}  "
              f"EPSG:{sub['epsg']}  {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
