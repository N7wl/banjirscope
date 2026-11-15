"""
Reading NISAR Level-2 GCOV products.

GCOV = Geocoded polarimetric COVariance. Each file holds radiometrically
terrain-corrected backscatter (gamma-naught, linear power) on a UTM grid:

    /science/LSAR/GCOV/grids/frequencyA/HHHH         backscatter, HH pol
    /science/LSAR/GCOV/grids/frequencyA/xCoordinates  pixel-centre easting (m)
    /science/LSAR/GCOV/grids/frequencyA/yCoordinates  pixel-centre northing (m)
    /science/LSAR/GCOV/grids/frequencyA/projection    EPSG code

A full frame is ~6.6 GB, but we only need a small area. HDF5 stores the
image in chunks, so by opening the file over HTTPS with byte-range requests
(fsspec) h5py downloads only the chunks that overlap our area of interest.

Authentication uses a NASA Earthdata (EDL) bearer token read from the
EARTHDATA_TOKEN environment variable (or a git-ignored .env file).
Credentials are never written in code.
"""

import os
from pathlib import Path

import h5py
import numpy as np
from pyproj import Transformer

from .processing import block_mean

GRID = "/science/LSAR/GCOV/grids/frequency{freq}"


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------
def load_dotenv(path=".env"):
    """Minimal .env reader: KEY=VALUE lines into os.environ (no override)."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def get_token():
    """Return the Earthdata bearer token or raise a helpful error."""
    load_dotenv()
    token = os.environ.get("EARTHDATA_TOKEN")
    if not token:
        raise RuntimeError(
            "EARTHDATA_TOKEN is not set.\n"
            "  1. Log in at https://urs.earthdata.nasa.gov and click 'Generate Token'\n"
            "  2. Either `export EARTHDATA_TOKEN=<token>` or put\n"
            "     EARTHDATA_TOKEN=<token> in a .env file in the project root\n"
            "     (.env is git-ignored)."
        )
    return token


def scene_url(scene_name):
    """Look up the HTTPS URL of a GCOV .h5 file by its scene name."""
    import asf_search as asf
    results = asf.granule_search([scene_name])
    for p in results:
        url = p.properties.get("url", "")
        if url.endswith(".h5") and scene_name in url:
            return url
    raise LookupError(f"No .h5 URL found for {scene_name}")


def open_remote(url, token):
    """Open a remote HDF5 file for lazy, chunk-wise reading over HTTPS.

    Settings follow ASF's NISAR cookbook: a bearer-token header and 16 MB
    read blocks (fewer, larger requests are much faster than many tiny ones).
    """
    import fsspec
    fs = fsspec.filesystem(
        "https",
        headers={"Authorization": f"Bearer {token}"},
        block_size=16 * 1024 * 1024,
    )
    fobj = fs.open(url, "rb", cache_type="blockcache")
    return h5py.File(fobj, "r")


# --------------------------------------------------------------------------
# Subsetting
# --------------------------------------------------------------------------
def read_epsg(grid):
    """EPSG code is stored as the value (and attribute) of `projection`."""
    proj = grid["projection"]
    if "epsg_code" in proj.attrs:
        return int(np.asarray(proj.attrs["epsg_code"]).item())
    return int(proj[()])


def bbox_window(x, y, epsg, bbox, pad_px=8):
    """Row/column slice of the GCOV grid covering a lon/lat bbox.

    The bbox edges are densified before projecting so that the curved
    lat/lon edges are fully contained in the UTM window.
    """
    west, south, east, north = bbox
    lons = np.concatenate([np.linspace(west, east, 50), np.full(50, east),
                           np.linspace(east, west, 50), np.full(50, west)])
    lats = np.concatenate([np.full(50, south), np.linspace(south, north, 50),
                           np.full(50, north), np.linspace(north, south, 50)])
    ex, ny_ = Transformer.from_crs(4326, epsg, always_xy=True).transform(lons, lats)
    dx, dy = x[1] - x[0], y[1] - y[0]
    cols = (np.array([ex.min(), ex.max()]) - x[0]) / dx
    rows = (np.array([ny_.min(), ny_.max()]) - y[0]) / dy
    c0 = max(int(np.floor(cols.min())) - pad_px, 0)
    c1 = min(int(np.ceil(cols.max())) + pad_px, len(x))
    r0 = max(int(np.floor(rows.min())) - pad_px, 0)
    r1 = min(int(np.ceil(rows.max())) + pad_px, len(y))
    if c0 >= c1 or r0 >= r1:
        raise ValueError("Area of interest does not overlap this GCOV frame")
    return slice(r0, r1), slice(c0, c1)


def read_subset(h5, bbox, freq="A", pol="HHHH", multilook=3, strip_rows=1536,
                progress=True):
    """Read and multilook the backscatter of one polarisation over a bbox.

    The window is read in horizontal strips so memory stays bounded and
    progress can be reported (each strip triggers the HTTP range requests).

    Returns a dict: power (linear, multilooked), x, y (pixel centres of the
    multilooked grid), epsg, native pixel spacing, and number of looks.
    """
    grid = h5[GRID.format(freq=freq)]
    if pol not in grid:
        raise KeyError(f"{pol} not in product; available: {list(grid.keys())}")
    x = grid["xCoordinates"][:]
    y = grid["yCoordinates"][:]
    epsg = read_epsg(grid)
    rs, cs = bbox_window(x, y, epsg, bbox)

    # Keep strip height a multiple of the multilook factor
    strip_rows -= strip_rows % multilook
    rows = []
    n_rows = rs.stop - rs.start
    for i, r in enumerate(range(rs.start, rs.stop, strip_rows)):
        r_end = min(r + strip_rows, rs.stop)
        block = grid[pol][r:r_end, cs]
        rows.append(block_mean(block, multilook))
        if progress:
            done = min(r_end - rs.start, n_rows)
            print(f"    read rows {done}/{n_rows}", flush=True)
    power = np.vstack(rows)

    # Pixel centres of the multilooked grid = mean of each block's centres
    xs = x[cs][: power.shape[1] * multilook].reshape(-1, multilook).mean(axis=1)
    ys = y[rs][: power.shape[0] * multilook].reshape(-1, multilook).mean(axis=1)

    nlooks = float("nan")          # unknown unless the product provides it
    if "numberOfLooks" in grid:
        sample = grid["numberOfLooks"][rs.start:rs.start + 64, cs.start:cs.start + 64]
        sample = sample[np.isfinite(sample) & (sample > 0)]
        if sample.size:
            nlooks = float(np.median(sample))

    return {
        "power": power.astype(np.float32), "x": xs, "y": ys, "epsg": epsg,
        "native_spacing_m": float(abs(x[1] - x[0])), "multilook": multilook,
        "native_looks": nlooks,
    }
