"""
preview_scenes.py — download the small geocoded browse PNGs (no login needed)
for one repeat-pass stack on chosen dates, so candidate flood pairs can be
checked by eye before committing to multi-GB HDF5 downloads.

Usage:
    python scripts/preview_scenes.py --track 4 --frame 81 --dates 2026-08-13 2026-09-30
"""

import argparse
import json
import os
import urllib.request

import asf_search as asf

OUT_DIR = os.path.join("data", "raw", "browse")


def pick_product(products):
    """Several products can exist per date (20 vs 40 MHz bandwidth, full vs
    partial frame). Prefer full-frame coverage, then the smaller file."""
    products.sort(key=lambda p: (p.properties.get("frameCoverage") != "Full",
                                 p.properties["bytes"][p.properties["fileName"]]["bytes"]))
    return products[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", type=int, required=True)
    ap.add_argument("--frame", type=int, required=True)
    ap.add_argument("--dates", nargs="+", required=True)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    for date in args.dates:
        res = asf.search(dataset="NISAR", processingLevel="GCOV",
                         relativeOrbit=args.track, frame=args.frame,
                         start=f"{date}T00:00:00Z", end=f"{date}T23:59:59Z")
        if not res:
            print(f"{date}: no product found")
            continue
        p = pick_product(list(res))
        pr = p.properties
        url = next(u for u in pr["browse"] if u.endswith("_LATLON.png"))
        out = os.path.join(OUT_DIR, f"t{args.track:03d}_f{args.frame:03d}_{date}.png")
        urllib.request.urlretrieve(url, out)
        # Save footprint so the preview can be located on a map later
        with open(out.replace(".png", ".json"), "w") as fh:
            json.dump({"scene": pr["sceneName"], "geometry": p.geometry,
                       "bandwidth": pr.get("rangeBandwidth"),
                       "coverage": pr.get("frameCoverage")}, fh, indent=1)
        print(f"{date}: {pr['sceneName']}  ->  {out}")


if __name__ == "__main__":
    main()
