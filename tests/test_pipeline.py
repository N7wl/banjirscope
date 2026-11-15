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


def vegetation_pair(veg_rise_db=6.0, crop_rise_db=0.0):
    """Flood scene plus a tree belt (-6 dB) right next to the flood box.

    The belt brightens by `veg_rise_db` (double bounce) and the open fields
    by `crop_rise_db` (crop growth). Returns before/after linear power and
    masks of the belt and the town.
    """
    n = 300
    before = np.full((n, n), -11.0)               # fields
    town = np.zeros((n, n), bool)
    town[30:70, 190:270] = True
    belt = np.zeros((n, n), bool)
    belt[184:208, 40:130] = True                  # 24 x 90 = 2160 px
    flood = np.zeros((n, n), bool)
    flood[100:180, 40:130] = True
    before[town] = -1.0
    before[belt] = -6.0
    before[220:290, 160:280] = -6.0               # forest far from the flood
    before[:, 140:150] = -23.0                    # river
    after = before.copy()
    after[before == -11.0] += crop_rise_db
    after[flood] = -22.0
    after[belt] += veg_rise_db
    return speckle(before), speckle(after), belt, town


def run_vegetation(before_p, after_p, **kwargs):
    b = proc.to_db(proc.lee_filter(before_p, window=5, enl=9))
    a = proc.to_db(proc.lee_filter(after_p, window=5, enl=9))
    res = detection.detect_flood(b, a, min_pixels=20, edge_px=0)
    return res, detection.detect_flooded_vegetation(b, a, res, min_pixels=20, **kwargs)


def test_flooded_vegetation_next_to_flood_is_detected():
    before_p, after_p, belt, _ = vegetation_pair(veg_rise_db=6.0)
    res, veg = run_vegetation(before_p, after_p)
    detected = veg["flooded_vegetation"]
    overlap = (detected & belt).sum()
    assert overlap / detected.sum() > 0.95
    assert overlap / belt.sum() > 0.85
    assert veg["rise_threshold_db"] >= 3.0
    # the two layers never overlap, and the open-flood result is unchanged
    assert not (detected & res["flood"]).any()


def test_crop_growth_is_not_flooded_vegetation():
    # Every field brightens by 2 dB (growing rice); the tree belt is stable
    before_p, after_p, _, _ = vegetation_pair(veg_rise_db=0.0, crop_rise_db=2.0)
    _, veg = run_vegetation(before_p, after_p)
    assert veg["flooded_vegetation"].mean() < 0.001
    # still nothing when the proximity rule is switched off
    _, veg = run_vegetation(before_p, after_p, near_flood_px=None)
    assert veg["flooded_vegetation"].mean() < 0.001


def test_stable_town_and_distant_forest_are_not_flooded_vegetation():
    before_p, after_p, belt, town = vegetation_pair(veg_rise_db=6.0)
    _, veg = run_vegetation(before_p, after_p)
    assert not (veg["flooded_vegetation"] & town).any()
    assert (veg["flooded_vegetation"] & ~belt).mean() < 0.001


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
