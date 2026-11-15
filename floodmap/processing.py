"""
Radar image preparation: multilooking, speckle filtering, dB conversion and
resampling onto a common Web-Mercator grid for the web map.

SAR primer for the steps in this file
-------------------------------------
* Speckle: every radar pixel is the coherent sum of many tiny scatterers, so
  even a uniform field looks grainy ("salt and pepper"). The noise is
  *multiplicative*: it scales with the signal, so filters must work on linear
  power, not on dB.
* Multilooking: averaging neighbouring pixels in linear power trades
  resolution for less speckle. A 3x3 multilook turns 10 m pixels into 30 m
  pixels with ~9x the "equivalent number of looks" (ENL).
* dB: backscatter spans several orders of magnitude, so we display and
  compare it on a log scale: dB = 10 * log10(power).
"""

import numpy as np
from pyproj import Transformer
from scipy.ndimage import uniform_filter

EARTH_RADIUS_M = 6378137.0  # sphere radius used by Web Mercator (EPSG:3857)


def block_mean(power, factor):
    """Multilook by averaging `factor` x `factor` blocks of linear power.

    Invalid pixels (NaN or <= 0) are ignored; a block with no valid pixel
    becomes NaN.
    """
    if factor <= 1:
        return power.astype(np.float32)
    h, w = power.shape
    h2, w2 = h // factor, w // factor
    p = power[: h2 * factor, : w2 * factor].astype(np.float64)
    valid = np.isfinite(p) & (p > 0)
    p = np.where(valid, p, 0.0)
    shape = (h2, factor, w2, factor)
    total = p.reshape(shape).sum(axis=(1, 3))
    count = valid.reshape(shape).sum(axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = total / count
    out[count == 0] = np.nan
    return out.astype(np.float32)


def lee_filter(power, window=5, enl=9.0):
    """Classic Lee speckle filter on linear power.

    In flat areas (local variance close to what speckle alone would cause)
    the pixel is replaced by the local mean; near edges (variance much higher
    than speckle) the original pixel is kept. This smooths speckle without
    blurring field and river boundaries as much as a plain mean filter would.

    Parameters
    ----------
    power : 2-D array of linear backscatter (NaN = no data)
    window : odd filter size in pixels
    enl : equivalent number of looks of the input; speckle variation
          coefficient is Cu^2 = 1 / ENL
    """
    valid = np.isfinite(power) & (power > 0)
    fill = np.nanmedian(power[valid]) if valid.any() else 0.0
    img = np.where(valid, power, fill).astype(np.float64)

    mean = uniform_filter(img, window)
    mean_sq = uniform_filter(img * img, window)
    var = np.maximum(mean_sq - mean * mean, 0.0)

    cu2 = 1.0 / enl                          # speckle-only variation
    with np.errstate(invalid="ignore", divide="ignore"):
        ci2 = var / (mean * mean)            # observed local variation
        k = np.clip((1.0 - cu2 / ci2) / (1.0 + cu2), 0.0, 1.0)
    k = np.nan_to_num(k)
    out = mean + k * (img - mean)
    out[~valid] = np.nan
    return out.astype(np.float32)


def to_db(power, floor_db=-40.0):
    """Linear power -> decibels, with a floor so log10(0) never appears."""
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(power)
    db = np.where(np.isfinite(db), np.maximum(db, floor_db), np.nan)
    return db.astype(np.float32)


def mercator_grid(bbox, res_m):
    """Build a north-up Web-Mercator pixel grid covering a lon/lat bbox.

    Returns a dict with pixel-centre coordinates (xs, ys in metres),
    the latitude of every row (for true-area calculations) and the
    Leaflet-style bounds [[south, west], [north, east]] of the pixel edges.
    """
    west, south, east, north = bbox
    to_merc = Transformer.from_crs(4326, 3857, always_xy=True)
    x0, y0 = to_merc.transform(west, south)
    x1, y1 = to_merc.transform(east, north)
    nx = int(np.ceil((x1 - x0) / res_m))
    ny = int(np.ceil((y1 - y0) / res_m))
    xs = x0 + (np.arange(nx) + 0.5) * res_m
    ys = y1 - (np.arange(ny) + 0.5) * res_m          # top row first
    to_ll = Transformer.from_crs(3857, 4326, always_xy=True)
    w_ll, s_ll = to_ll.transform(x0, y1 - ny * res_m)
    e_ll, n_ll = to_ll.transform(x0 + nx * res_m, y1)
    row_lat = np.degrees(np.arctan(np.sinh(ys / EARTH_RADIUS_M)))
    return {
        "xs": xs, "ys": ys, "res_m": res_m, "row_lat": row_lat,
        "bounds": [[s_ll, w_ll], [n_ll, e_ll]],
    }


def resample_to_grid(arr, src_x, src_y, src_epsg, grid):
    """Nearest-neighbour resample of a regularly gridded raster onto `grid`.

    `src_x` / `src_y` are pixel-centre coordinates of `arr` in `src_epsg`
    (UTM metres for GCOV, degrees for the preview images). Both must be
    evenly spaced, which holds for NISAR's geocoded grids.
    """
    tf = Transformer.from_crs(3857, src_epsg, always_xy=True)
    out = np.full((len(grid["ys"]), len(grid["xs"])), np.nan, dtype=np.float32)
    dx = src_x[1] - src_x[0]
    dy = src_y[1] - src_y[0]
    # Process in row strips to keep memory use modest on large grids
    for r0 in range(0, len(grid["ys"]), 512):
        ys = grid["ys"][r0:r0 + 512]
        gx, gy = np.meshgrid(grid["xs"], ys)
        sx, sy = tf.transform(gx, gy)
        ix = np.rint((sx - src_x[0]) / dx).astype(np.int64)
        iy = np.rint((sy - src_y[0]) / dy).astype(np.int64)
        ok = (ix >= 0) & (ix < arr.shape[1]) & (iy >= 0) & (iy < arr.shape[0])
        strip = out[r0:r0 + len(ys)]
        strip[ok] = arr[iy[ok], ix[ok]]
    return out


def pixel_area_km2(grid):
    """True ground area of each pixel row in km^2.

    Web-Mercator stretches distances by 1/cos(latitude), so a res_m pixel
    really covers (res_m * cos(lat))^2 on the ground.
    """
    side = grid["res_m"] * np.cos(np.radians(grid["row_lat"]))
    return (side ** 2) / 1e6
