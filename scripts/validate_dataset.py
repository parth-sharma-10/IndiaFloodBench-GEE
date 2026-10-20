"""Validate IndiaFloodBench. `validate_event` runs per event inside the build; running this file validates
the whole packaged dataset and writes reports/validation.json.

Errors fail an event; warnings are reported (e.g. cloud gaps in Sentinel-2 are expected in monsoon)."""
import glob
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

ROOT = Path(__file__).resolve().parents[1]
RASTERS = ["s1_before", "s1_after", "s2_before", "s2_after", "derived", "rainfall", "terrain", "landcover", "labels"]
INDIA = (68.0, 6.0, 98.0, 37.5)
REQUIRED = ["VV_before", "VH_before", "VV_after", "VH_after", "elevation", "slope", "rainfall_1d", "flood_label"]


def check_patch_rasters(pdir):
    """CRS, transform, dimensions, resolution and nodata must agree across every file of a patch."""
    errs, ref = [], None
    for name in RASTERS:
        f = pdir / f"{name}.tif"
        if not f.exists():
            errs.append(f"{pdir.name}: missing {name}.tif")
            continue
        with rasterio.open(f) as ds:
            sig = (str(ds.crs), tuple(round(v, 6) for v in ds.transform[:6]), ds.width, ds.height)
            if ds.nodata is None:
                errs.append(f"{pdir.name}/{name}: nodata not set")
            if abs(ds.res[0] - 20) > 1e-6 or abs(ds.res[1] - 20) > 1e-6:
                errs.append(f"{pdir.name}/{name}: resolution {ds.res}")
            ds.read(1, window=((0, 8), (0, 8)))  # readable
        ref = ref or sig
        if sig != ref:
            errs.append(f"{pdir.name}/{name}: grid {sig} != {ref}")
    return errs


def check_temporal(ev, st):
    errs = []
    start, lab = date.fromisoformat(ev["event_start"]), date.fromisoformat(ev["label_date"])
    b0, b1 = map(date.fromisoformat, ev["before_window"])
    for p in glob.glob(str(ROOT / "data/raster" / ev["event_id"] / "*/patch.json")):
        m = json.loads(Path(p).read_text())
        for dstr in m["s1_before_dates"]:
            if not (b0 <= date.fromisoformat(dstr) <= b1 < start):
                errs.append(f"{m['patch_id']}: S1 before date {dstr} outside {b0}..{b1} / not before {start}")
        for dstr in m["s1_after_dates"]:
            if date.fromisoformat(dstr) != lab:
                errs.append(f"{m['patch_id']}: S1 after date {dstr} != label date {lab}")
        if not m["s1_before_dates"] or not m["s1_after_dates"]:
            errs.append(f"{m['patch_id']}: missing Sentinel-1 before/after")
    return errs


def check_table(df, ev):
    errs, warns = [], []
    w, s, e, n = ev["bbox"]
    tol = 0.1
    bad = ~df["latitude"].between(s - tol, n + tol) | ~df["longitude"].between(w - tol, e + tol)
    if bad.any():
        errs.append(f"{int(bad.sum())} samples outside event bbox")
    if not df["latitude"].between(INDIA[1], INDIA[3]).all() or not df["longitude"].between(INDIA[0], INDIA[2]).all():
        errs.append("samples outside India lat/lon range")
    for k in (1, 0):
        if not (df["flood_label"] == k).any():
            errs.append(f"no samples with flood_label={k}")
    if not df["flood_label"].isin([0, 1]).all():
        errs.append("flood_label has values other than 0/1")
    dup = df.duplicated(["event_id", "latitude", "longitude"]).sum()
    if dup:
        errs.append(f"{int(dup)} duplicate (event, lat, lon) samples")
    for c in REQUIRED:
        frac = df[c].isna().mean()
        if frac > 0.2:
            errs.append(f"{c}: {frac:.1%} missing (>20%)")
        elif frac > 0:
            warns.append(f"{c}: {frac:.1%} missing")
    for c in ("NDVI_before", "NDVI_after"):
        frac = df[c].isna().mean()
        if frac > 0:
            warns.append(f"{c}: {frac:.1%} missing (cloud / no clear Sentinel-2 in window)")
    for c in ("label_source", "event_id", "region", "state"):
        if df[c].isna().any():
            errs.append(f"{c} not recorded for every sample")
    return errs, warns


def validate_event(ev, st):
    eid = ev["event_id"]
    errs, warns = [], []
    pdirs = [Path(p).parent for p in glob.glob(str(ROOT / "data/raster" / eid / "*/patch.json"))]
    if not pdirs:
        errs.append("no patches written")
    for pdir in pdirs:
        errs += check_patch_rasters(pdir)
    errs += check_temporal(ev, st)
    tab = ROOT / f"data/tabular/events/{eid}.parquet"
    if tab.exists():
        e2, w2 = check_table(pd.read_parquet(tab), ev)
        errs, warns = errs + e2, warns + w2
    else:
        errs.append("event parquet missing")
    if not ev.get("label_source"):
        errs.append("label_source missing in config")
    return {"passed": not errs, "errors": errs, "warnings": warns, "patches_checked": len(pdirs)}


def validate_all():
    sys.path.insert(0, str(ROOT / "src"))
    events = json.loads((ROOT / "configs/events.json").read_text())
    out = {}
    for ev in events:
        stp = ROOT / "state" / f"{ev['event_id']}.json"
        st = json.loads(stp.read_text()) if stp.exists() else {}
        out[ev["event_id"]] = (validate_event(ev, st) if st.get("status") in ("complete", "validated", "exported")
                               else {"passed": False, "errors": [f"not built (state: {st.get('status')}, "
                                                                  f"error: {st.get('error')})"], "warnings": []})
    full = ROOT / "data/tabular/indiafloodbench.parquet"
    if full.exists():
        df = pd.read_parquet(full)
        out["_dataset"] = dataset_checks(df)
    (ROOT / "reports/validation.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def dataset_checks(df):
    """Whole-dataset checks: duplicates, IDs, admin-region agreement, NaN report, label value set."""
    res = {"rows": len(df), "duplicate_sample_id": int(df["sample_id"].duplicated().sum()),
           "duplicate_rows_all_columns": int(df.duplicated().sum()),
           "duplicate_event_lat_lon": int(df.duplicated(["event_id", "latitude", "longitude"]).sum()),
           "cross_event_same_lat_lon": int(df.duplicated(["latitude", "longitude"]).sum()),
           "pixel_state_differs_from_event_state": int((df["state"] != df["event_state"]).sum()),
           "pixel_state_by_event": {f"{e} -> {s}": int(n) for (e, s), n in
                                    df.groupby("event_id")["state"].value_counts(dropna=False).items()},
           "samples_without_state": int(df["state"].isna().sum()),
           # open sea labelled as flood (the Yaas bug) shows up as flood samples outside every state
           "flood_samples_without_state": int((df["state"].isna() & (df["flood_label"] == 1)).sum()),
           "flood_label_values": sorted(df["flood_label"].unique().tolist()),
           "missing_fraction": {c: round(float(v), 4) for c, v in df.isna().mean().items() if v > 0},
           "nonfinite_numeric": {c: int(np.isinf(df[c]).sum()) for c in df.select_dtypes("number")
                                 if np.isinf(df[c]).any()}}
    return res


if __name__ == "__main__":
    r = validate_all()
    for k, v in r.items():
        if k != "_dataset":
            print(k, "PASS" if v["passed"] else "FAIL", v["errors"][:5], f"warnings={len(v['warnings'])}")
    print(json.dumps(r.get("_dataset", {}), indent=1, default=str)[:3000])
