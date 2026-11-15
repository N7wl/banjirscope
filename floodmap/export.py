"""
Export results as web-friendly PNG overlays plus a JSON sidecar with the
geographic bounds and statistics. Leaflet's ImageOverlay only needs a PNG and
its [[south, west], [north, east]] bounds, so no tile server or backend is
required and the site can be hosted on GitHub Pages.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image


def _fit(img, max_px, resample):
    """Downscale a PIL image so its longest side is at most max_px."""
    scale = max_px / max(img.size)
    if scale >= 1:
        return img
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, resample)


# Longest side of exported overlays. 3000 px keeps ~30 m detail for a
# ~100 km area while keeping each image small enough for a web page.
MAX_WEB_PX = 3000


def _save(img, path):
    """Save as lossy WebP (photo-like radar images) or lossless PNG (masks).

    The format follows the file extension. WebP keeps transparency and is
    typically 10x smaller than PNG for speckled radar imagery.
    """
    if str(path).lower().endswith(".webp"):
        img.convert("RGBA").save(path, "WEBP", quality=80, method=4)
    else:
        img.save(path, optimize=True)


def save_gray(db, vmin, vmax, path, max_px=MAX_WEB_PX):
    """Grayscale backscatter overlay; no-data becomes transparent.

    Both dates must use the same vmin/vmax so brightness is comparable when
    swiping between them.
    """
    valid = np.isfinite(db)
    g = np.clip((np.nan_to_num(db, nan=vmin) - vmin) / (vmax - vmin), 0, 1)
    # Gray + alpha ("LA") is about a third smaller than RGBA for the same picture
    la = np.zeros(db.shape + (2,), np.uint8)
    la[..., 0] = (g * 255).astype(np.uint8)
    la[..., 1] = np.where(valid, 255, 0)
    _save(_fit(Image.fromarray(la, "LA"), max_px, Image.LANCZOS), path)


def save_mask(mask, rgb, path, alpha=230, max_px=MAX_WEB_PX):
    """Coloured, semi-transparent mask overlay.

    When downscaling, the mask is box-averaged so thin flooded channels fade
    out gradually instead of disappearing.
    """
    frac = Image.fromarray((mask.astype(np.float32) * 255).astype(np.uint8), "L")
    frac = np.asarray(_fit(frac, max_px, Image.BOX), np.float32) / 255.0
    rgba = np.zeros(frac.shape + (4,), np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = (np.clip(frac * 1.5, 0, 1) * alpha).astype(np.uint8)
    _save(Image.fromarray(rgba, "RGBA"), path)


def save_log_ratio(lr, path, limit_db=10.0, max_px=MAX_WEB_PX // 2):
    """Diverging overlay of the log-ratio change image.

    Blue = got darker (towards water), red = got brighter. Opacity grows with
    the size of the change, so 'no change' areas stay see-through. It is
    a diagnostic layer, so it is exported at half resolution to stay light.
    """
    v = np.clip(np.nan_to_num(lr) / limit_db, -1, 1)
    rgba = np.zeros(lr.shape + (4,), np.uint8)
    neg, pos = v < 0, v > 0
    rgba[neg] = [33, 113, 181, 0]       # blue
    rgba[pos] = [203, 24, 29, 0]        # red
    rgba[..., 3] = (np.abs(v) ** 0.8 * 220).astype(np.uint8)
    rgba[~np.isfinite(lr), 3] = 0
    _save(_fit(Image.fromarray(rgba, "RGBA"), max_px, Image.LANCZOS), path)


def write_json(obj, path):
    Path(path).write_text(json.dumps(obj, indent=2))


def update_index(data_dir, entry):
    """Add or replace an event in docs/data/events.json (the app's index)."""
    index_path = Path(data_dir) / "events.json"
    events = json.loads(index_path.read_text()) if index_path.exists() else []
    events = [e for e in events if e["id"] != entry["id"]] + [entry]
    events.sort(key=lambda e: e.get("after_date", ""), reverse=True)
    write_json(events, index_path)
