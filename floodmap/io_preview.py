"""
Preview mode: a stand-in for calibrated GCOV data built from the free,
geocoded browse PNGs that ASF publishes for every NISAR product.

The browse image is a colour composite (HH shown in red/blue, HV in green),
already scaled for display, ~150 m per pixel. Its red channel is used as an
*uncalibrated* HH brightness proxy. This is good enough to develop and demo
the pipeline end to end, but area numbers are approximate. The web app
labels results produced this way as "preview".
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def download_browse(scene_name, out_png):
    """Fetch the free geocoded browse PNG (+ footprint JSON) for one scene.

    Browse images are public: no Earthdata login is needed.
    """
    import urllib.request
    import asf_search as asf

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    product = next(p for p in asf.granule_search([scene_name])
                   if p.properties.get("sceneName") == scene_name)
    url = next(u for u in product.properties["browse"] if u.endswith("_LATLON.png"))
    urllib.request.urlretrieve(url, out_png)
    out_png.with_suffix(".json").write_text(json.dumps(
        {"scene": scene_name, "geometry": product.geometry}, indent=1))
    return out_png


def load_browse(png_path, json_path=None):
    """Load a LATLON browse PNG as pseudo linear power on a lon/lat grid.

    The PNG spans the bounding box of the product footprint, with pixel rows
    evenly spaced in latitude and columns in longitude (EPSG:4326).
    Display brightness 0..255 is mapped linearly onto a pseudo dB range of
    -30..0 dB, then to pseudo linear power, so the same processing chain as
    for real data can run unchanged.
    """
    png_path = Path(png_path)
    json_path = Path(json_path) if json_path else png_path.with_suffix(".json")
    meta = json.loads(json_path.read_text())
    ring = meta["geometry"]["coordinates"][0]
    lons = [c[0] for c in ring]
    lats = [c[1] for c in ring]
    west, east, south, north = min(lons), max(lons), min(lats), max(lats)

    red = np.asarray(Image.open(png_path).convert("RGB"))[..., 0].astype(np.float32)
    h, w = red.shape
    x = west + (np.arange(w) + 0.5) * (east - west) / w      # lon of pixel centres
    y = north - (np.arange(h) + 0.5) * (north - south) / h   # lat, top row first

    pseudo_db = -30.0 + 30.0 * red / 255.0
    power = np.power(10.0, pseudo_db / 10.0).astype(np.float32)
    power[red == 0] = np.nan                                  # black = outside swath
    return {"power": power, "x": x, "y": y, "epsg": 4326,
            "scene": meta.get("scene"), "pixel_deg": (east - west) / w}
