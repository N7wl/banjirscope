"""
Tests on synthetic radar scenes where the true flood extent is known.

Run with:  python -m pytest -q
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floodmap import detection, processing as proc  # noqa: E402

RNG = np.random.default_rng(42)


def speckle(db_scene, looks=9):
    """Apply realistic multiplicative speckle (gamma-distributed) to a dB scene."""
    power = 10 ** (db_scene / 10)
    noise = RNG.gamma(shape=looks, scale=1 / looks, size=power.shape)
    return power * noise


def synthetic_pair(n=300, flooded=True):
    """Fields (-11 dB), towns (-1 dB), a river (-23 dB) and a flood box.

    Returns before/after linear power and the true masks.
    """
    scene = np.full((n, n), -11.0)
    scene[40:60, 200:260] = -1.0                  # town
    scene[220:235, 30:120] = -6.0                 # forest patch
    river = np.zeros((n, n), bool)
    river[:, 140:150] = True
    flood = np.zeros((n, n), bool)
    flood[100:180, 40:130] = True                 # 80 x 90 = 7200 px
    before = scene.copy()
    before[river] = -23.0
    after = before.copy()
    if flooded:
        after[flood] = -22.0
    return speckle(before), speckle(after), flood, river


def run_detection(before_p, after_p):
    b = proc.to_db(proc.lee_filter(before_p, window=5, enl=9))
    a = proc.to_db(proc.lee_filter(after_p, window=5, enl=9))
    return detection.detect_flood(b, a, min_pixels=20, edge_px=0)


def test_flood_area_recovered():
    before_p, after_p, flood_true, river = synthetic_pair()
    res = run_detection(before_p, after_p)
    detected = res["flood"]
    overlap = (detected & flood_true).sum()
    precision = overlap / detected.sum()
    recall = overlap / flood_true.sum()
    assert precision > 0.95, precision
    assert recall > 0.90, recall


def test_river_is_not_flood_but_is_water():
    before_p, after_p, _, river = synthetic_pair()
    res = run_detection(before_p, after_p)
    assert (res["flood"] & river).sum() / river.sum() < 0.02
    assert (res["permanent_water"] & river).sum() / river.sum() > 0.8


def test_no_change_means_no_flood():
    # Same scene on both dates, only the speckle differs
    before_p, after_p, _, _ = synthetic_pair(flooded=False)
    res = run_detection(before_p, after_p)
    assert res["flood"].mean() < 0.005


def test_lee_filter_reduces_speckle_on_flat_area():
    flat = speckle(np.full((200, 200), -10.0), looks=4)
    filtered = proc.lee_filter(flat, window=7, enl=4)
    assert filtered.std() < 0.5 * flat.std()
    assert abs(filtered.mean() - flat.mean()) / flat.mean() < 0.05


def test_block_mean_ignores_nodata():
    p = np.ones((6, 6), np.float32)
    p[:3, :3] = np.nan
    out = proc.block_mean(p, 3)
    assert np.isnan(out[0, 0]) and np.allclose(out[1, 1], 1.0)


def test_mercator_area_matches_geodesic_estimate():
    # 0.1 deg box at 15 N: ~ 11.1 km (lat) x 10.75 km (lon) ~ 119 km^2
    bbox = (100.0, 15.0, 100.1, 15.1)
    grid = proc.mercator_grid(bbox, 50)
    total = proc.pixel_area_km2(grid).sum() * len(grid["xs"])
    assert abs(total - 119.4) / 119.4 < 0.02, total
