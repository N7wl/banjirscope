"""
run_pipeline.py — turn a before/after radar pair into web map layers.

    speckle filter -> dB -> common grid -> log-ratio + Otsu -> PNG overlays

An event may list several flood dates (see floodmap/timeline.py). Each one is
compared with the same dry baseline on the same grid and drawn with the same
brightness stretch, so the app can step through the flood's rise and fall.

Modes
-----
gcov     calibrated NISAR L2 GCOV subsets saved by fetch_subset.py (default)
preview  free browse PNGs as an uncalibrated stand-in (no login needed)

Usage:
    python scripts/run_pipeline.py --event events/thailand-chao-phraya-2026.json
    python scripts/run_pipeline.py --event ... --mode preview
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from floodmap import detection, export, processing as proc, timeline  # noqa: E402
from floodmap.io_preview import download_browse, load_browse  # noqa: E402

FLOOD_RGB = (0, 170, 255)          # bright cyan-blue: new flood water
PERMANENT_RGB = (20, 40, 110)      # dark navy: rivers, lakes, reservoirs
FLOODED_VEG_RGB = (25, 195, 160)   # teal: flooded vegetation / built-up areas

# Flooded vegetation is only accepted this close to open flood water. It
# normally borders it, and the rule keeps out fields that merely got
# brighter because the crop grew.
FLOODED_VEG_MAX_DIST_M = 1000
ASF_LINK = "https://search.asf.alaska.edu/#/?dataset=NISAR&searchType=List%20Search&searchList="


def load_inputs(event, mode):
    """Return (baseline, [one raster per flood date]) plus mode settings.

    The flood-date rasters follow the order of timeline.flood_dates(event).
    """
    p = event["processing"]
    dates = timeline.flood_dates(event)
    if mode == "gcov":
        src_dir = ROOT / "data/processed" / event["id"]
        pair = []
        for which in ["before"] + [timeline.subset_stem(d) for d in dates]:
            f = src_dir / f"{which}.npz"
            if not f.exists():
                sys.exit(f"Missing {f}. Run scripts/fetch_subset.py first "
                         f"(or use --mode preview).")
            d = dict(np.load(f, allow_pickle=False))
            d["epsg"] = int(d["epsg"])
            pair.append(d)
        native = float(pair[0].get("native_looks", np.nan))
        native = native if np.isfinite(native) and native > 0 else 1.0
        settings = {
            "res_m": p["target_res_m"],
            "lee_window": p["lee_window"],
            "enl": max(native, 1.0) * p["multilook"] ** 2,
            # Calibrated gamma0: open water sits well below -14 dB at L-band
            "water_db_range": (-26.0, -14.0),
        }
    else:
        browse_dir = ROOT / "data/raw/browse"
        pair = []
        for shot in [event["before"]] + dates:
            png = browse_dir / f"{shot['scene']}_LATLON.png"
            if not png.exists():
                print(f"  downloading browse preview for {shot['date']}")
                download_browse(shot["scene"], png)
            pair.append(load_browse(png))
        settings = {"res_m": 150, "lee_window": 3, "enl": 4.0, "water_db_range": None}
    return (pair[0], pair[1:]), settings


def prepare(src, grid, settings):
    """Speckle-filter in linear power, convert to dB, resample to the grid."""
    filtered = proc.lee_filter(src["power"], settings["lee_window"], settings["enl"])
    db = proc.to_db(filtered)
    return proc.resample_to_grid(db, src["x"], src["y"], src["epsg"], grid)


def summary_figure(before_db, after_db, flood, perm, vmin, vmax, meta, path, veg=None):
    """Three-panel PNG (before | after | flood map) for the README."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    fig, axes = plt.subplots(1, 3, figsize=(15, 6.6), constrained_layout=True)
    for ax, img, title in ((axes[0], before_db, f"Before · {meta['before']['date']}"),
                           (axes[1], after_db, f"After · {meta['after']['date']}")):
        ax.imshow(img, cmap="gray", vmin=vmin, vmax=vmax)
        ax.set_title(title, fontsize=13)
    axes[2].imshow(after_db, cmap="gray", vmin=vmin, vmax=vmax, alpha=0.55)
    axes[2].imshow(np.where(perm, 1, np.nan), cmap=ListedColormap([np.array(PERMANENT_RGB) / 255]))
    axes[2].imshow(np.where(flood, 1, np.nan), cmap=ListedColormap([np.array(FLOOD_RGB) / 255]))
    axes[2].set_title(f"Detected flood water · {meta['stats']['flood_km2']:,.0f} km²", fontsize=13)
    handles = [Patch(color=np.array(FLOOD_RGB) / 255, label="New flood water"),
               Patch(color=np.array(PERMANENT_RGB) / 255, label="Water on both dates")]
    # Only draw flooded vegetation when it is large enough to see at this scale
    if veg is not None and veg.sum() >= 0.01 * max(flood.sum(), 1):
        axes[2].imshow(np.where(veg, 1, np.nan), cmap=ListedColormap([np.array(FLOODED_VEG_RGB) / 255]))
        handles.append(Patch(color=np.array(FLOODED_VEG_RGB) / 255, label="Flooded vegetation / towns"))
    axes[2].legend(handles=handles, loc="lower left", fontsize=10, framealpha=0.9)
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    label = "NISAR L2 GCOV, HH" if meta["mode"] == "gcov" else "NISAR browse preview (uncalibrated)"
    fig.suptitle(f"{meta['title']} — {label}", fontsize=15)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def timeline_figure(before_db, frames, vmin, vmax, meta, path):
    """One row of panels (baseline, then every flood date) for the README.

    `frames` is a list of (timeline entry, after_db, flood mask), oldest first.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    n = len(frames) + 1
    fig, axes = plt.subplots(1, n, figsize=(3.6 * n, 5.9), constrained_layout=True)
    axes[0].imshow(before_db, cmap="gray", vmin=vmin, vmax=vmax)
    axes[0].set_title(f"Baseline · {meta['before']['date']}", fontsize=12)
    flood_cmap = ListedColormap([np.array(FLOOD_RGB) / 255])
    for ax, (entry, after_db, flood) in zip(axes[1:], frames):
        ax.imshow(after_db, cmap="gray", vmin=vmin, vmax=vmax)
        ax.imshow(np.where(flood, 1, np.nan), cmap=flood_cmap, alpha=0.85)
        ax.set_title(f"{entry['date']} · {entry['stats']['flood_km2']:,.0f} km² flooded", fontsize=12)
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    label = "NISAR L2 GCOV, HH" if meta["mode"] == "gcov" else "NISAR browse preview (uncalibrated)"
    fig.suptitle(f"{meta['title']} — new flood water (blue) on each pass — {label}", fontsize=14)
    fig.savefig(path, dpi=100)
    plt.close(fig)


def min_patch_px(event, settings):
    """Smallest flood patch kept, converted from hectares to pixels."""
    return max(2, round(event["processing"]["min_flood_area_ha"] * 1e4 / settings["res_m"] ** 2))


def analyse_date(before_db, after_db, grid, settings, min_px):
    """Change detection of one flood date against the baseline.

    Maps open flood water (darkening) and, as a separate layer, flooded
    vegetation / built-up areas (double-bounce brightening). Returns the
    detection result plus its area statistics and thresholds.
    """
    result = detection.detect_flood(before_db, after_db, min_pixels=min_px,
                                    water_db_range=settings["water_db_range"])
    row_area = proc.pixel_area_km2(grid)
    stats = {
        "flood_km2": round(detection.area_km2(result["flood"], row_area), 1),
        "permanent_water_km2": round(detection.area_km2(result["permanent_water"], row_area), 1),
        "mapped_area_km2": round(detection.area_km2(result["valid"], row_area), 1),
    }
    stats["flood_pct"] = round(100 * stats["flood_km2"] / max(stats["mapped_area_km2"], 1e-9), 1)
    thresholds = {"water_db": round(result["water_threshold_db"], 2),
                  "change_db": round(result["change_threshold_db"], 2)}

    near_px = max(1, round(FLOODED_VEG_MAX_DIST_M / settings["res_m"]))
    veg = detection.detect_flooded_vegetation(before_db, after_db, result,
                                              min_pixels=min_px, near_flood_px=near_px)
    result["flooded_vegetation"] = veg["flooded_vegetation"]
    stats["flooded_vegetation_km2"] = round(detection.area_km2(veg["flooded_vegetation"], row_area), 1)
    thresholds.update({"vegetation_bright_db": round(veg["bright_threshold_db"], 2),
                       "vegetation_rise_db": round(veg["rise_threshold_db"], 2),
                       "vegetation_max_dist_m": FLOODED_VEG_MAX_DIST_M})
    return result, stats, thresholds


def export_date(after_db, result, vmin, vmax, out_dir, prefix=""):
    """Write one flood date's overlays; return their paths relative to out_dir."""
    layers = {"after": f"{prefix}after.png", "flood": f"{prefix}flood.png",
              "permanent_water": f"{prefix}permanent_water.png",
              "log_ratio": f"{prefix}log_ratio.png",
              "flooded_vegetation": f"{prefix}flooded_vegetation.png"}
    (out_dir / prefix).mkdir(parents=True, exist_ok=True)
    export.save_gray(after_db, vmin, vmax, out_dir / layers["after"])
    export.save_mask(result["flood"], FLOOD_RGB, out_dir / layers["flood"])
    export.save_mask(result["permanent_water"], PERMANENT_RGB, out_dir / layers["permanent_water"], alpha=200)
    export.save_log_ratio(result["log_ratio"], out_dir / layers["log_ratio"])
    export.save_mask(result["flooded_vegetation"], FLOODED_VEG_RGB, out_dir / layers["flooded_vegetation"])
    return layers


def build_meta(event, mode, grid, settings, min_px, vmin, vmax, entries):
    """Assemble meta.json.

    `entries` is the per-date "timeline" list. The top-level after / stats /
    thresholds / layers fields repeat the default (main) flood date, so a
    reader that only knows about a single before/after pair keeps working.
    """
    main_entry = next(e for e in entries if e["default"])
    (s, w), (n, e) = grid["bounds"]
    return {
        "id": event["id"], "title": event["title"], "location": event["location"],
        "description": event["description"], "mode": mode,
        "bounds": grid["bounds"], "center": [(s + n) / 2, (w + e) / 2],
        "before": {**event["before"], "url": ASF_LINK + event["before"]["scene"]},
        "after": {k: main_entry[k] for k in ("date", "scene", "url")},
        "days_between": main_entry["days_between"],
        "stack": event["stack"], "polarization": event["polarization"][:2],
        "stats": main_entry["stats"],
        "thresholds": main_entry["thresholds"],
        "processing": {"resolution_m": settings["res_m"], "lee_window": settings["lee_window"],
                       "enl": round(settings["enl"], 1), "min_patch_px": min_px,
                       "display_db_range": [round(vmin, 1), round(vmax, 1)]},
        "layers": {"before": "before.png", **main_entry["layers"]},
        "timeline": entries,
        "news": event.get("news", []),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
    }


def process_event(event, mode, before_db, after_dbs, grid, settings, out_root, figures=True):
    """Detect, export and describe every flood date of an event.

    `before_db` and `after_dbs` (one per timeline.flood_dates(event), same
    order) are dB images already on `grid`. Returns the meta.json content.
    """
    dates = timeline.flood_dates(event)
    out_dir = Path(out_root) / event["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    min_px = min_patch_px(event, settings)

    # One stretch for the baseline and all dates so brightness is comparable
    vmin, vmax = timeline.shared_stretch([before_db, *after_dbs])
    export.save_gray(before_db, vmin, vmax, out_dir / "before.png")

    entries, frames = [], []
    for shot, after_db in zip(dates, after_dbs):
        result, stats, thresholds = analyse_date(before_db, after_db, grid, settings, min_px)
        print(f"  {shot['date']}: water < {thresholds['water_db']:.2f} dB, "
              f"change < {thresholds['change_db']:.2f} dB, "
              f"flood {stats['flood_km2']} km² ({stats['flood_pct']}% of mapped area), "
              f"permanent water {stats['permanent_water_km2']} km², "
              f"flooded vegetation {stats['flooded_vegetation_km2']} km²")
        layers = export_date(after_db, result, vmin, vmax, out_dir,
                             timeline.layer_prefix(shot, len(dates)))
        entries.append({
            "date": shot["date"], "scene": shot["scene"], "url": ASF_LINK + shot["scene"],
            "default": shot["default"],
            "days_between": timeline.days_between(event["before"]["date"], shot["date"]),
            "stats": stats, "thresholds": thresholds, "layers": layers,
        })
        frames.append((entries[-1], after_db, result["flood"], result["permanent_water"],
                       result["flooded_vegetation"]))

    meta = build_meta(event, mode, grid, settings, min_px, vmin, vmax, entries)
    export.write_json(meta, out_dir / "meta.json")
    export.update_index(out_root, {"id": event["id"], "title": event["title"],
                                   "after_date": meta["after"]["date"], "mode": mode})
    if figures:
        _, after_db, flood, perm, veg = next(f for f in frames if f[0]["default"])
        summary_figure(before_db, after_db, flood, perm, vmin, vmax, meta, out_dir / "summary.png",
                       veg=veg)
        if len(frames) > 1:
            timeline_figure(before_db, [f[:3] for f in frames], vmin, vmax, meta,
                            out_dir / "timeline.png")
    print(f"  wrote layers + meta.json to {out_dir}")
    return meta


def main():
    ap = argparse.ArgumentParser(description="Run the flood-mapping pipeline")
    ap.add_argument("--event", required=True)
    ap.add_argument("--mode", choices=("gcov", "preview"), default="gcov")
    ap.add_argument("--out", default=str(ROOT / "docs/data"))
    args = ap.parse_args()

    event = json.loads(Path(args.event).read_text())
    print(f"Event: {event['title']}  (mode: {args.mode})")
    (before_src, after_srcs), settings = load_inputs(event, args.mode)

    # Speckle filter, dB, common Web-Mercator grid (baseline prepared once)
    grid = proc.mercator_grid(event["aoi_bbox"], settings["res_m"])
    before_db = prepare(before_src, grid, settings)
    after_dbs = [prepare(src, grid, settings) for src in after_srcs]
    print(f"  grid {before_db.shape[1]}x{before_db.shape[0]} px at {settings['res_m']} m, "
          f"{len(after_dbs)} flood date(s)")

    # Change detection per date, overlays, meta.json, figures
    process_event(event, args.mode, before_db, after_dbs, grid, settings, args.out)


if __name__ == "__main__":
    main()
