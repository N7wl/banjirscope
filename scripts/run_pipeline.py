"""
run_pipeline.py — turn a before/after radar pair into web map layers.

    speckle filter -> dB -> common grid -> log-ratio + Otsu -> PNG overlays

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
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from floodmap import detection, export, processing as proc  # noqa: E402
from floodmap.io_preview import download_browse, load_browse  # noqa: E402

FLOOD_RGB = (0, 170, 255)          # bright cyan-blue: new flood water
PERMANENT_RGB = (20, 40, 110)      # dark navy: rivers, lakes, reservoirs


def load_inputs(event, mode):
    """Return (before, after) source rasters plus mode-specific settings."""
    p = event["processing"]
    if mode == "gcov":
        src_dir = ROOT / "data/processed" / event["id"]
        pair = []
        for which in ("before", "after"):
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
        for which in ("before", "after"):
            png = browse_dir / f"{event[which]['scene']}_LATLON.png"
            if not png.exists():
                print(f"  downloading browse preview for {which}")
                download_browse(event[which]["scene"], png)
            pair.append(load_browse(png))
        settings = {"res_m": 150, "lee_window": 3, "enl": 4.0, "water_db_range": None}
    return pair, settings


def prepare(src, grid, settings):
    """Speckle-filter in linear power, convert to dB, resample to the grid."""
    filtered = proc.lee_filter(src["power"], settings["lee_window"], settings["enl"])
    db = proc.to_db(filtered)
    return proc.resample_to_grid(db, src["x"], src["y"], src["epsg"], grid)


def summary_figure(before_db, after_db, flood, perm, vmin, vmax, meta, path):
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
    axes[2].legend(handles=[Patch(color=np.array(FLOOD_RGB) / 255, label="New flood water"),
                            Patch(color=np.array(PERMANENT_RGB) / 255, label="Water on both dates")],
                   loc="lower left", fontsize=10, framealpha=0.9)
    for ax in axes:
        ax.set_xticks([])
        ax.set_yticks([])
    label = "NISAR L2 GCOV, HH" if meta["mode"] == "gcov" else "NISAR browse preview (uncalibrated)"
    fig.suptitle(f"{meta['title']} — {label}", fontsize=15)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="Run the flood-mapping pipeline")
    ap.add_argument("--event", required=True)
    ap.add_argument("--mode", choices=("gcov", "preview"), default="gcov")
    ap.add_argument("--out", default=str(ROOT / "docs/data"))
    args = ap.parse_args()

    event = json.loads(Path(args.event).read_text())
    print(f"Event: {event['title']}  (mode: {args.mode})")
    (before_src, after_src), settings = load_inputs(event, args.mode)

    # 1-3. Speckle filter, dB, common Web-Mercator grid
    grid = proc.mercator_grid(event["aoi_bbox"], settings["res_m"])
    before_db = prepare(before_src, grid, settings)
    after_db = prepare(after_src, grid, settings)
    print(f"  grid {after_db.shape[1]}x{after_db.shape[0]} px at {settings['res_m']} m")

    # 4. Change detection
    min_px = max(2, round(event["processing"]["min_flood_area_ha"] * 1e4 / settings["res_m"] ** 2))
    result = detection.detect_flood(before_db, after_db, min_pixels=min_px,
                                    water_db_range=settings["water_db_range"])
    row_area = proc.pixel_area_km2(grid)
    stats = {
        "flood_km2": round(detection.area_km2(result["flood"], row_area), 1),
        "permanent_water_km2": round(detection.area_km2(result["permanent_water"], row_area), 1),
        "mapped_area_km2": round(detection.area_km2(result["valid"], row_area), 1),
    }
    stats["flood_pct"] = round(100 * stats["flood_km2"] / max(stats["mapped_area_km2"], 1e-9), 1)
    print(f"  water threshold {result['water_threshold_db']:.2f} dB, "
          f"change threshold {result['change_threshold_db']:.2f} dB")
    print(f"  flood {stats['flood_km2']} km² ({stats['flood_pct']}% of mapped area), "
          f"permanent water {stats['permanent_water_km2']} km²")

    # 5. Export overlays (same stretch for both dates so the swipe is fair)
    out_dir = Path(args.out) / event["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    both = np.concatenate([before_db[np.isfinite(before_db)], after_db[np.isfinite(after_db)]])
    vmin, vmax = (float(v) for v in np.percentile(both, [2, 98]))
    export.save_gray(before_db, vmin, vmax, out_dir / "before.png")
    export.save_gray(after_db, vmin, vmax, out_dir / "after.png")
    export.save_mask(result["flood"], FLOOD_RGB, out_dir / "flood.png")
    export.save_mask(result["permanent_water"], PERMANENT_RGB, out_dir / "permanent_water.png", alpha=200)
    export.save_log_ratio(result["log_ratio"], out_dir / "log_ratio.png")

    (s, w), (n, e) = grid["bounds"]
    days = (date.fromisoformat(event["after"]["date"]) - date.fromisoformat(event["before"]["date"])).days
    asf_link = "https://search.asf.alaska.edu/#/?dataset=NISAR&searchType=List%20Search&searchList="
    meta = {
        "id": event["id"], "title": event["title"], "location": event["location"],
        "description": event["description"], "mode": args.mode,
        "bounds": grid["bounds"], "center": [(s + n) / 2, (w + e) / 2],
        "before": {**event["before"], "url": asf_link + event["before"]["scene"]},
        "after": {**event["after"], "url": asf_link + event["after"]["scene"]},
        "days_between": days,
        "stack": event["stack"], "polarization": event["polarization"][:2],
        "stats": stats,
        "thresholds": {"water_db": round(result["water_threshold_db"], 2),
                       "change_db": round(result["change_threshold_db"], 2)},
        "processing": {"resolution_m": settings["res_m"], "lee_window": settings["lee_window"],
                       "enl": round(settings["enl"], 1), "min_patch_px": min_px,
                       "display_db_range": [round(vmin, 1), round(vmax, 1)]},
        "layers": {"before": "before.png", "after": "after.png", "flood": "flood.png",
                   "permanent_water": "permanent_water.png", "log_ratio": "log_ratio.png"},
        "news": event.get("news", []),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
    }
    export.write_json(meta, out_dir / "meta.json")
    export.update_index(args.out, {"id": event["id"], "title": event["title"],
                                   "after_date": event["after"]["date"], "mode": args.mode})
    summary_figure(before_db, after_db, result["flood"], result["permanent_water"],
                   vmin, vmax, meta, out_dir / "summary.png")
    print(f"  wrote layers + meta.json to {out_dir}")


if __name__ == "__main__":
    main()
