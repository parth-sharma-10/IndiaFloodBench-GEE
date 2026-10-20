"""IndiaFloodBench quickstart: load the table, make a leakage-safe split, open one raster patch,
and score the simplest possible flood map (dark radar = water) per event.

Run from the repository root:  python examples/quickstart.py
Needs: pandas, pyarrow, numpy; rasterio only for the raster part (skipped if rasters are absent).
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

# 1. The table: one row = one 20 m pixel.
df = pd.read_parquet(ROOT / "data/tabular/indiafloodbench.parquet")
print(f"{len(df):,} rows, {df.event_id.nunique()} events, {df.patch_id.nunique()} patches")
print(df.groupby("event_id").flood_label.agg(rows="size", flood_share="mean").round(2), "\n")

# 2. A leakage-safe split: hold out one whole event (never split rows of one patch).
splits = json.loads((ROOT / "data/metadata/splits.json").read_text())
test_events = splits["leave_one_event_out"]["IFB-001"]["test_events"]
train, test = df[~df.event_id.isin(test_events)], df[df.event_id.isin(test_events)]
assert not set(train.patch_id) & set(test.patch_id)
print(f"train {len(train):,} rows from {train.event_id.nunique()} events; "
      f"test {len(test):,} rows from {test_events} (hand-labelled)\n")

# 3. The simplest flood map: calm water reflects radar away, so a pixel darker than -18 dB in VV
#    after the flood is called water. Score it per event with IoU, weighting rows back to the
#    real flood share with 1 / inclusion_prob.
def iou(pred, truth, w):
    inter = np.sum(w * (pred & truth))
    union = np.sum(w * (pred | truth))
    return inter / union if union else np.nan


ok = df.VV_after.notna()
pred = (df.VV_after < -18) & ~(df.permanent_water == 1)
print("IoU of 'VV_after < -18 dB and not permanent water' against the label:")
for eid, g in df[ok].groupby("event_id"):
    print(f"  {eid} {g.label_source.iloc[0]:18s} {iou(pred[g.index], g.flood_label == 1, 1 / g.inclusion_prob):.2f}")
print("GFM events score higher partly because GFM is computed from the same radar image "
      "(circularity); IFB-001 is the honest check.\n")

# 4. One raster patch (download the release zip first; see README).
pdir = ROOT / "data/raster" / df.event_id.iloc[0] / df.patch_id.iloc[0]
if pdir.exists():
    import rasterio

    with rasterio.open(pdir / "labels.tif") as ds:
        lab = ds.read(1)
        print(f"{pdir.name}: {ds.width}x{ds.height} px, {ds.res[0]:.0f} m, {ds.crs}, bands {ds.descriptions}")
    print(f"  flood px {(lab == 1).sum()}, dry px {(lab == 0).sum()}, unlabelled px {(lab == 255).sum()}")
else:
    print(f"rasters not found at {pdir.parent.parent}; download them to try step 4")
