"""
Test the GCOV subset reader against a small mock file that follows the
documented NISAR L2 GCOV layout (/science/LSAR/GCOV/grids/frequencyA/...).
"""

import sys
from pathlib import Path

import h5py
import numpy as np
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floodmap.io_gcov import read_subset  # noqa: E402

EPSG = 32647  # UTM 47N, as used for central Thailand


def make_mock_gcov(path, res=20.0):
    tf = Transformer.from_crs(4326, EPSG, always_xy=True)
    x0, y0 = tf.transform(100.0, 14.6)
    x = x0 + np.arange(600) * res
    y = y0 - np.arange(600) * res
    power = np.full((600, 600), 0.1, np.float32)
    power[:, :300] = 0.01                     # left half "water"
    power[:5, :] = np.nan                     # no-data strip
    with h5py.File(path, "w") as f:
        g = f.create_group("/science/LSAR/GCOV/grids/frequencyA")
        g["xCoordinates"], g["yCoordinates"] = x, y
        p = g.create_dataset("projection", data=np.uint32(EPSG))
        p.attrs["epsg_code"] = np.uint32(EPSG)
        g.create_dataset("HHHH", data=power, chunks=(128, 128))
    return x, y


def test_read_subset_window_and_multilook(tmp_path):
    f = tmp_path / "mock.h5"
    x, y = make_mock_gcov(f)
    bbox = (100.02, 14.54, 100.06, 14.58)     # inside the mock grid
    with h5py.File(f, "r") as h5:
        sub = read_subset(h5, bbox, multilook=3, progress=False)

    assert sub["epsg"] == EPSG
    assert sub["power"].shape == (len(sub["y"]), len(sub["x"]))
    # subset is much smaller than the full 600 x 600 grid (multilooked by 3)
    assert sub["power"].shape[0] < 200 and sub["power"].shape[1] < 200
    # the bbox corners fall inside the subset's coordinate range
    tf = Transformer.from_crs(4326, EPSG, always_xy=True)
    ex, ny = tf.transform([bbox[0], bbox[2]], [bbox[1], bbox[3]])
    assert sub["x"].min() <= min(ex) and sub["x"].max() >= max(ex)
    assert sub["y"].min() <= min(ny) and sub["y"].max() >= max(ny)
    # multilooked values stay within the two classes and both classes survive
    # (blocks straddling the water/land edge average to in-between values)
    vals = sub["power"][np.isfinite(sub["power"])]
    assert vals.min() >= 0.01 - 1e-6 and vals.max() <= 0.1 + 1e-6
    assert np.isclose(vals, 0.01).any() and np.isclose(vals, 0.1).any()
