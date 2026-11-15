"""
Tests for multi-date events: config parsing, the shared display stretch, the
per-date output layout and GCOV subset fetching. Everything runs on small
synthetic arrays and mock files, with no network access.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import fetch_subset  # noqa: E402
import run_pipeline  # noqa: E402
from floodmap import processing as proc, timeline  # noqa: E402
from test_gcov_reader import make_mock_gcov  # noqa: E402

RNG = np.random.default_rng(7)
PREVIEW_SETTINGS = {"res_m": 150, "lee_window": 3, "enl": 4.0, "water_db_range": None}


def make_event(extra_dates=()):
    """Minimal event config: a pair, plus optional extra flood dates."""
    event = {
        "id": "test-event", "title": "Test flood", "location": "Nowhere",
        "description": "Synthetic.", "aoi_bbox": [100.0, 14.5, 100.1, 14.6],
        "stack": {"track": 1, "frame": 1, "direction": "DESCENDING"},
        "frequency": "A", "polarization": "HHHH",
        "before": {"date": "2026-08-13", "scene": "SCENE_0813"},
        "after": {"date": "2026-09-30", "scene": "SCENE_0930"},
        "processing": {"multilook": 3, "target_res_m": 30, "lee_window": 5,
                       "min_flood_area_ha": 1.0},
    }
    if extra_dates:
        event["timeline"] = [{"date": d, "scene": f"SCENE_{d[5:7]}{d[8:]}"} for d in extra_dates]
    return event


def synthetic_stack(grid, flood_rows):
    """Baseline of fields plus a river, and one flood-date image per entry of
    `flood_rows`, each with the top `rows` rows under water."""
    shape = (len(grid["ys"]), len(grid["xs"]))
    before = np.full(shape, -11.0, np.float32) + RNG.normal(0, 0.4, shape).astype(np.float32)
    before[:, 30:34] = -23.0                       # permanent river
    afters = []
    for rows in flood_rows:
        after = before + RNG.normal(0, 0.4, shape).astype(np.float32)
        after[:rows, :25] = -22.0
        afters.append(after)
    return before, afters


# ------------------------------------------------------------ config parsing
def test_single_pair_config_is_a_one_entry_timeline():
    dates = timeline.flood_dates(make_event())
    assert dates == [{"date": "2026-09-30", "scene": "SCENE_0930", "default": True}]
    assert timeline.subset_stem(dates[0]) == "after"
    assert timeline.layer_prefix(dates[0], len(dates)) == ""


def test_timeline_config_is_sorted_with_after_as_default():
    event = make_event(["2026-10-24", "2026-09-18", "2026-10-12"])
    dates = timeline.flood_dates(event)
    assert [d["date"] for d in dates] == ["2026-09-18", "2026-09-30", "2026-10-12", "2026-10-24"]
    assert [d["default"] for d in dates] == [False, True, False, False]
    assert [timeline.subset_stem(d) for d in dates] == [
        "after_2026-09-18", "after", "after_2026-10-12", "after_2026-10-24"]
    assert timeline.layer_prefix(dates[0], len(dates)) == "2026-09-18/"


def test_timeline_config_tolerates_repeating_the_after_scene():
    event = make_event()
    event["timeline"] = [dict(event["after"]), {"date": "2026-10-12", "scene": "SCENE_1012"}]
    dates = timeline.flood_dates(event)
    assert [d["date"] for d in dates] == ["2026-09-30", "2026-10-12"]
    assert dates[0]["default"] and not dates[1]["default"]


@pytest.mark.parametrize("entry", [
    {"date": "2026-10-12"},                              # no scene
    {"date": "12 Oct 2026", "scene": "SCENE_1012"},      # not an ISO date
    {"date": "2026-08-13", "scene": "SCENE_0813"},       # the baseline itself
    {"date": "2026-09-30", "scene": "SOME_OTHER_SCENE"}, # clashes with "after"
])
def test_timeline_config_rejects_bad_entries(entry):
    event = make_event()
    event["timeline"] = [entry]
    with pytest.raises(ValueError):
        timeline.flood_dates(event)


# ------------------------------------------------- stretch and output layout
def test_shared_stretch_spans_all_images_and_ignores_nodata():
    dark = np.linspace(-30, -20, 1000, dtype=np.float32)
    bright = np.linspace(-10, 0, 1000, dtype=np.float32)
    bright[:10] = np.nan
    vmin, vmax = timeline.shared_stretch([dark, bright])
    assert -30 <= vmin < -29 and -1 < vmax <= 0
    # the range of the whole set, not of any single image
    assert vmin < timeline.shared_stretch([bright])[0]
    assert vmax > timeline.shared_stretch([dark])[1]


def test_multi_date_outputs_and_meta(tmp_path):
    event = make_event(["2026-09-18", "2026-10-12"])
    grid = proc.mercator_grid(event["aoi_bbox"], 150)
    before, afters = synthetic_stack(grid, flood_rows=[20, 50, 35])   # rise, then recede
    afters[2] = afters[2] + 6.0          # a brighter date must not get its own stretch
    meta = run_pipeline.process_event(event, "preview", before, afters, grid,
                                      PREVIEW_SETTINGS, tmp_path, figures=False)

    out = tmp_path / "test-event"
    assert (out / "before.png").exists()
    layer_names = {"after", "flood", "permanent_water", "log_ratio"}
    assert [t["date"] for t in meta["timeline"]] == ["2026-09-18", "2026-09-30", "2026-10-12"]
    for entry in meta["timeline"]:
        assert set(entry["layers"]) == layer_names
        for rel in entry["layers"].values():
            assert rel.startswith(entry["date"] + "/") and (out / rel).exists()
        assert entry["thresholds"].keys() == {"water_db", "change_db"}
        assert entry["url"].endswith(entry["scene"])
    assert [t["days_between"] for t in meta["timeline"]] == [36, 48, 60]

    # the flood rises to the main date and recedes afterwards
    km2 = [t["stats"]["flood_km2"] for t in meta["timeline"]]
    assert km2[0] < km2[1] > km2[2] > 0

    # top-level fields describe the default date, for single-pair readers
    main = next(t for t in meta["timeline"] if t["default"])
    assert sum(t["default"] for t in meta["timeline"]) == 1
    assert main["date"] == meta["after"]["date"] == "2026-09-30"
    assert meta["stats"] == main["stats"] and meta["thresholds"] == main["thresholds"]
    assert meta["days_between"] == 48
    assert meta["layers"] == {"before": "before.png", **main["layers"]}
    assert json.loads((out / "meta.json").read_text()) == meta
    index = json.loads((tmp_path / "events.json").read_text())
    assert index[0]["id"] == "test-event" and index[0]["after_date"] == "2026-09-30"

    # one stretch for every date: the +6 dB date is drawn visibly brighter
    gray = [np.asarray(Image.open(out / t["layers"]["after"]))[..., 0].astype(float)
            for t in meta["timeline"]]
    land = (slice(60, None), slice(40, None))                 # never flooded
    step = 255 / np.subtract(*meta["processing"]["display_db_range"][::-1])
    assert abs(gray[0][land].mean() - gray[1][land].mean()) < 0.5 * step
    assert gray[2][land].mean() - gray[1][land].mean() > 4 * step


def test_single_pair_keeps_flat_layout(tmp_path):
    event = make_event()
    grid = proc.mercator_grid(event["aoi_bbox"], 150)
    before, afters = synthetic_stack(grid, flood_rows=[40])
    meta = run_pipeline.process_event(event, "preview", before, afters, grid,
                                      PREVIEW_SETTINGS, tmp_path, figures=False)
    assert meta["layers"] == {"before": "before.png", "after": "after.png", "flood": "flood.png",
                              "permanent_water": "permanent_water.png",
                              "log_ratio": "log_ratio.png"}
    assert all((tmp_path / "test-event" / f).exists() for f in meta["layers"].values())
    assert len(meta["timeline"]) == 1 and meta["timeline"][0]["default"]


# ------------------------------------------------------------- GCOV fetching
def test_fetch_event_saves_baseline_once_plus_every_date(tmp_path):
    event = make_event(["2026-09-18", "2026-10-12"])
    event["aoi_bbox"] = [100.02, 14.54, 100.06, 14.58]       # inside the mock grid
    files = []
    for i in range(4):                                       # baseline + 3 flood dates
        files.append(tmp_path / f"mock{i}.h5")
        make_mock_gcov(files[-1])

    out_dir = tmp_path / "processed"
    saved = fetch_subset.fetch_event(event, out_dir, local=files)
    assert [p.name for p in saved] == ["before.npz", "after_2026-09-18.npz", "after.npz",
                                       "after_2026-10-12.npz"]
    assert sorted(p.name for p in out_dir.iterdir()) == sorted(p.name for p in saved)
    dates = [str(np.load(p)["date"]) for p in saved]
    assert dates == ["2026-08-13", "2026-09-18", "2026-09-30", "2026-10-12"]
    assert str(np.load(out_dir / "after.npz")["scene"]) == "SCENE_0930"

    # resuming leaves finished subsets alone
    stamp = saved[0].stat().st_mtime_ns
    fetch_subset.fetch_event(event, out_dir, local=files, skip_existing=True)
    assert saved[0].stat().st_mtime_ns == stamp

    with pytest.raises(ValueError, match="needs 4 files"):
        fetch_subset.fetch_event(event, out_dir, local=files[:2])


def test_gcov_inputs_load_in_timeline_order(tmp_path, monkeypatch):
    event = make_event(["2026-10-12"])
    event["aoi_bbox"] = [100.02, 14.54, 100.06, 14.58]
    files = []
    for i in range(3):
        files.append(tmp_path / f"mock{i}.h5")
        make_mock_gcov(files[-1])
    monkeypatch.setattr(run_pipeline, "ROOT", tmp_path)
    fetch_subset.fetch_event(event, tmp_path / "data/processed" / event["id"], local=files)

    (before, afters), settings = run_pipeline.load_inputs(event, "gcov")
    assert str(before["date"]) == "2026-08-13"
    assert [str(a["date"]) for a in afters] == ["2026-09-30", "2026-10-12"]
    assert settings["res_m"] == 30
    grid = proc.mercator_grid(event["aoi_bbox"], settings["res_m"])
    assert np.isfinite(run_pipeline.prepare(afters[1], grid, settings)).any()
