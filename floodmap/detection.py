"""
Flood detection by log-ratio change detection with Otsu thresholding.

Method
------
1. Log-ratio: LR = dB_after - dB_before (= 10*log10(P_after / P_before)).
   Using a ratio rather than a difference cancels the multiplicative speckle
   and calibration effects that are common to both dates. Newly flooded land
   drops sharply (calm water reflects the radar pulse away from the
   satellite, like a mirror), so floods show up as strongly negative LR.
2. Water threshold (Otsu on the flood-date image): Otsu picks thresholds that
   best separate the modes of a histogram, with no hand tuning. Farmland
   scenes usually have *three* modes: open water (darkest), fields, and
   bright towns/trees. A plain two-class Otsu then splits fields from towns
   and labels ordinary fields as water, so we use three-class (multi-level)
   Otsu and take the lowest threshold as the water/land boundary.
3. Change threshold (Otsu on LR, restricted to dark pixels): among pixels
   that are water on the flood date, permanent water barely changes
   (LR ~ 0 dB) while new flood water darkened a lot. This subset is
   strongly bimodal, which is exactly the situation where Otsu works well.
4. A pixel is flooded if it is water after, was NOT water before, and its
   LR is below the change threshold. Small speckle blobs are removed with
   a morphological opening plus a minimum-area filter.

Known limitations (L-band specifics)
------------------------------------
* Flooded forest and flooded towns look *brighter*, not darker, because the
  signal bounces off the water and then off trunks or walls back to the
  satellite ("double bounce"). This method maps open flood water only.
* Rice paddies flooded on purpose for planting can be detected as floods.
"""

import cv2
import numpy as np
from skimage.filters import threshold_multiotsu, threshold_otsu


def otsu(values, fallback, classes=2):
    """Lowest Otsu threshold of finite values (2 or 3 classes).

    Falls back to `fallback` if there are too few samples, and to two-class
    Otsu if the histogram cannot support three classes.
    """
    values = values[np.isfinite(values)]
    if values.size < 100 or np.ptp(values) == 0:
        return float(fallback)
    if classes > 2:
        try:
            return float(threshold_multiotsu(values, classes=classes)[0])
        except ValueError:
            pass
    return float(threshold_otsu(values))


def clean_mask(mask, min_pixels):
    """Remove speckle noise: 3x3 opening, then drop blobs below min_pixels."""
    m = mask.astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if min_pixels > 1:
        n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        keep = np.zeros(n, dtype=bool)
        keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_pixels
        m = keep[labels].astype(np.uint8)
    return m.astype(bool)


def detect_flood(before_db, after_db, min_pixels=10,
                 water_db_range=None, max_change_db=-3.0, edge_px=3):
    """Map new flood water and permanent water from two co-registered dB images.

    Parameters
    ----------
    before_db, after_db : 2-D float arrays on the same grid (NaN = no data)
    min_pixels : smallest connected flood patch kept, in pixels
    water_db_range : optional (lo, hi) clamp for the water threshold; useful
        for calibrated data where open water is known to sit around -20 dB
    max_change_db : the change threshold is never allowed above this value,
        so tiny brightness wobbles can never count as flooding
    edge_px : pixels trimmed from the border of the valid-data area; swath
        edges are dark and ragged and would otherwise look like water

    Returns
    -------
    dict with boolean masks 'flood', 'permanent_water', 'valid', the
    log-ratio image 'log_ratio' and the two thresholds used.
    """
    valid = np.isfinite(before_db) & np.isfinite(after_db)
    if edge_px > 0:
        kernel = np.ones((2 * edge_px + 1, 2 * edge_px + 1), np.uint8)
        valid = cv2.erode(valid.astype(np.uint8), kernel,
                          borderType=cv2.BORDER_REPLICATE).astype(bool)
    log_ratio = np.where(valid, after_db - before_db, np.nan).astype(np.float32)

    t_water = otsu(after_db[valid], fallback=-18.0, classes=3)
    if water_db_range is not None:
        t_water = float(np.clip(t_water, *water_db_range))

    water_after = valid & (after_db < t_water)
    water_before = valid & (before_db < t_water)

    t_change = otsu(log_ratio[water_after], fallback=max_change_db)
    t_change = min(t_change, max_change_db)

    flood = water_after & ~water_before & (log_ratio < t_change)
    flood = clean_mask(flood, min_pixels)
    permanent = clean_mask(water_after & water_before, min_pixels)

    return {
        "flood": flood,
        "permanent_water": permanent,
        "valid": valid,
        "log_ratio": log_ratio,
        "water_threshold_db": t_water,
        "change_threshold_db": t_change,
    }


def area_km2(mask, row_area_km2):
    """Sum true ground area of True pixels, given per-row pixel areas."""
    return float((mask.sum(axis=1) * row_area_km2).sum())
