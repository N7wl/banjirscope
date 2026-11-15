"""
find_scenes.py — Step 1 of the pipeline: data discovery.

Searches NASA's Alaska Satellite Facility (ASF) catalogue for NISAR Level-2
GCOV products (geocoded, terrain-corrected radar backscatter) over a set of
flood-prone areas, then groups the hits by repeat-pass "stack".

Why group by stack?
    Change detection needs two images taken from the *same viewing geometry*.
    For SAR that means the same orbit track (pathNumber), the same frame
    (frameNumber) and the same flight direction (ascending/descending).
    Comparing an ascending image with a descending one would show differences
    caused by the look angle, not by flooding.

No login is needed for searching; only downloads need Earthdata credentials.

Usage:
    python scripts/find_scenes.py                 # all default AOIs
    python scripts/find_scenes.py --aoi kelantan  # one AOI
    python scripts/find_scenes.py --out data/scene_inventory.json
"""

import argparse
import json
from collections import defaultdict

import asf_search as asf

# Rough bounding boxes (WKT, lon/lat) around flood-prone lowlands.
# These are deliberately generous; the pipeline crops tighter later.
AOIS = {
    "kelantan":   "POLYGON((101.8 5.4, 102.6 5.4, 102.6 6.3, 101.8 6.3, 101.8 5.4))",   # Kota Bharu / Kuala Krai
    "terengganu": "POLYGON((102.6 4.6, 103.5 4.6, 103.5 5.8, 102.6 5.8, 102.6 4.6))",   # Kuala Terengganu / Dungun
    "pahang":     "POLYGON((102.6 3.3, 103.5 3.3, 103.5 4.0, 102.6 4.0, 102.6 3.3))",   # Pekan / Kuantan / Temerloh-east
    "johor":      "POLYGON((102.9 1.6, 104.1 1.6, 104.1 2.6, 102.9 2.6, 102.9 1.6))",   # Segamat / Kota Tinggi / Batu Pahat
    # West-coast areas hit during the Oct-Nov 2026 floods
    "selangor":   "POLYGON((101.1 3.0, 101.6 3.0, 101.6 3.6, 101.1 3.6, 101.1 3.0))",   # Kuala Selangor / Klang
    "perak":      "POLYGON((100.8 3.8, 101.3 3.8, 101.3 4.2, 100.8 4.2, 100.8 3.8))",   # Teluk Intan / Hilir Perak
    # Fallback: central Thailand floods, late Sep - early Oct 2026
    "thailand_ayutthaya":  "POLYGON((100.3 14.1, 100.8 14.1, 100.8 14.6, 100.3 14.6, 100.3 14.1))",  # Ayutthaya / Ang Thong
    "thailand_prachinburi":"POLYGON((101.2 13.8, 101.7 13.8, 101.7 14.2, 101.2 14.2, 101.2 13.8))",  # Bang Pakong river
    "kedah":      "POLYGON((100.1 6.0, 100.6 6.0, 100.6 6.6, 100.1 6.6, 100.1 6.0))",   # Kubang Pasu / Changlun / Perlis
}


def search_aoi(wkt, start=None, end=None):
    """Return all NISAR GCOV products intersecting the AOI polygon."""
    opts = dict(dataset="NISAR", processingLevel="GCOV", intersectsWith=wkt)
    if start:
        opts["start"] = start
    if end:
        opts["end"] = end
    return asf.search(**opts)


def summarise(results):
    """Group products into repeat-pass stacks: (track, frame, direction)."""
    stacks = defaultdict(list)
    for p in results:
        pr = p.properties
        key = (pr["pathNumber"], pr["frameNumber"], pr["flightDirection"])
        size_gb = pr["bytes"].get(pr["fileName"], {}).get("bytes", 0) / 1e9
        stacks[key].append({
            "date": pr["startTime"][:10],
            "scene": pr["sceneName"],
            "pols": pr.get("mainBandPolarization"),
            "coverage": pr.get("frameCoverage"),
            "bandwidth": pr.get("rangeBandwidth"),
            "size_gb": round(size_gb, 2),
            "url": pr["url"],
        })
    for k in stacks:
        stacks[k].sort(key=lambda d: d["date"])
    return stacks


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--aoi", choices=AOIS.keys(), help="search a single AOI")
    ap.add_argument("--start", help="ISO date, e.g. 2025-10-01")
    ap.add_argument("--end", help="ISO date")
    ap.add_argument("--out", help="write full inventory to this JSON file")
    args = ap.parse_args()

    names = [args.aoi] if args.aoi else list(AOIS)
    inventory = {}
    for name in names:
        res = search_aoi(AOIS[name], args.start, args.end)
        stacks = summarise(res)
        print(f"\n=== {name.upper()}: {len(res)} GCOV products in {len(stacks)} stacks ===")
        for (track, frame, direction), scenes in sorted(stacks.items(), key=lambda kv: -len(kv[1])):
            dates = sorted({s["date"] for s in scenes})
            print(f"  track {track:>3} frame {frame:>3} {direction[:4]}: "
                  f"{len(dates)} dates  {dates[0]} -> {dates[-1]}")
            print(f"      {', '.join(dates)}")
        inventory[name] = {f"{t}_{f}_{d}": v for (t, f, d), v in stacks.items()}

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(inventory, fh, indent=2)
        print(f"\nInventory written to {args.out}")


if __name__ == "__main__":
    main()
