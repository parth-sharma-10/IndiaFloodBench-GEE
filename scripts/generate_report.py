"""Package and report.

  python scripts/generate_report.py package   merge event tables -> data/tabular/indiafloodbench.{parquet,csv},
                                              events.csv, data/metadata/splits.json
  python scripts/validate_dataset.py          -> reports/validation.json
  python scripts/generate_report.py report    -> data_quality_report.md, FINAL_AUDIT.md (re-reads every file)
"""
import glob
import json
import unicodedata
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import shapely

ROOT = Path(__file__).resolve().parents[1]
EVENTS = json.loads((ROOT / "configs/events.json").read_text())
CFG = json.loads((ROOT / "configs/dataset_config.json").read_text())
ADMIN = ROOT / "data/external/admin"
sys.path.insert(0, str(ROOT / "src"))
from ifb.features import s2_product  # noqa: E402

COLUMNS = [
    "sample_id", "event_id", "slug", "patch_id", "patch_kind", "region_group", "event_state", "state",
    "district", "region", "flood_type", "latitude", "longitude", "event_start", "event_end", "label_date",
    "s1_before_dates", "s1_after_date", "s1_relative_orbit", "s1_orbit_state",
    "VV_before", "VH_before", "VV_after", "VH_after", "VV_change", "VH_change",
    "B2_before", "B3_before", "B4_before", "B8_before", "B11_before", "B12_before",
    "B2_after", "B3_after", "B4_after", "B8_after", "B11_after", "B12_after",
    "s2_before_offset_days", "s2_after_offset_days", "s2_before_product", "s2_after_product",
    "NDVI_before", "NDVI_after", "NDVI_change", "NDWI_before", "NDWI_after", "NDWI_change",
    "MNDWI_before", "MNDWI_after", "MNDWI_change", "NDBI_before", "NDBI_after",
    "rainfall_1d", "rainfall_3d", "rainfall_7d", "rainfall_14d", "elevation", "slope",
    "landcover_class", "jrc_occurrence", "permanent_water",
    "flood_label", "flood_confidence", "gfm_flood_extent", "label_source", "sample_stratum", "inclusion_prob",
]
LABEL_META = {
    "gfm": dict(label_source="Copernicus GFM ensemble flood extent (EODC archive)", label_resolution_m=20,
                label_method="GFM ensemble observed water (majority of 3 independent Sentinel-1 algorithms: DLR, "
                             "LIST, TU Wien) AND NOT JRC GSW occurrence >= 50%; GFM exclusion mask -> no label. "
                             "GFM's own flood extent (minus monthly reference water) kept as gfm_flood_extent",
                label_confidence="medium (automated operational product, not manually verified)",
                label_type="automated/weak", label_derived_from_feature="VV_after (same Sentinel-1 acquisition)"),
    "sen1floods11_hand": dict(label_source="Sen1Floods11 v1.1 hand labels (Bonafilia et al. 2020)",
                              label_resolution_m=10,
                              label_method="Manual water delineation by analysts on S1 + S2 of the same day; "
                                           "flood = hand water AND NOT JRC GSW occurrence >= 50%",
                              label_confidence="high (manual), resampled 10 m -> 20 m nearest",
                              label_type="manual", label_derived_from_feature="none directly (analyst used S1/S2 imagery)"),
}


def load_events_done():
    out = []
    for ev in EVENTS:
        st = json.loads((ROOT / "state" / f"{ev['event_id']}.json").read_text()) if (
            ROOT / "state" / f"{ev['event_id']}.json").exists() else {"status": "not_started"}
        out.append((ev, st))
    return out


def region_groups(events):
    """Union of events whose AOIs intersect: splits by region_group never share ground."""
    parent = {e["event_id"]: e["event_id"] for e in events}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            if shapely.box(*a["bbox"]).intersects(shapely.box(*b["bbox"])):
                parent[find(b["event_id"])] = find(a["event_id"])
    roots = sorted({find(e["event_id"]) for e in events})
    return {e["event_id"]: f"RG{roots.index(find(e['event_id'])) + 1:02d}" for e in events}


def _plain(name):
    """geoBoundaries uses transliteration marks (Bihār, Gujarāt); compare and publish plain ASCII."""
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()


def admin_lookup(df):
    """Per-pixel state (ADM1) and district (ADM2). Pixels on rivers/estuaries that fall just outside the
    simplified polygons take the nearest polygon within 0.05 deg (~5 km); beyond that they stay null."""
    pts = shapely.points(df["longitude"].values, df["latitude"].values)
    for col, level in (("state", "ADM1"), ("district", "ADM2")):
        gj = json.loads((ADMIN / f"geoBoundaries-IND-{level}.geojson").read_text())
        geoms = [shapely.geometry.shape(f["geometry"]) for f in gj["features"]]
        names = np.array([_plain(f["properties"]["shapeName"]) for f in gj["features"]], dtype=object)
        tree = shapely.STRtree(geoms)
        vals = np.full(len(df), None, dtype=object)
        hit = tree.query(pts, predicate="within")
        vals[hit[0]] = names[hit[1]]
        miss = np.flatnonzero(vals == None)  # noqa: E711 - elementwise on object array
        if len(miss):
            near, dist = tree.query_nearest(pts[miss], max_distance=0.05, return_distance=True)
            vals[miss[near[0]]] = names[near[1]]
        df[col] = vals
    return df


def package():
    events_done = load_events_done()
    rg = region_groups(EVENTS)
    frames = []
    for ev, st in events_done:
        f = ROOT / f"data/tabular/events/{ev['event_id']}.parquet"
        if st.get("status") == "complete" and f.exists():
            frames.append(pd.read_parquet(f))
    df = pd.concat(frames, ignore_index=True)
    df["region_group"] = df["event_id"].map(rg)
    df = df.rename(columns={"state": "event_state"})
    df["sample_id"] = df["patch_id"] + "_" + df.groupby("patch_id").cumcount().map("{:04d}".format)
    df["label_source"] = df["label_source"].map(lambda s: "gfm" if s == "gfm" else "sen1floods11_hand")
    s2a = CFG["s2_after_window_days"]
    for ev in EVENTS:
        lab = date.fromisoformat(ev["label_date"])
        rows = df["event_id"] == ev["event_id"]
        df.loc[rows, "s2_before_product"] = s2_product(ev["before_window"])
        df.loc[rows, "s2_after_product"] = s2_product([str(lab + timedelta(days=s2a[0])), str(lab + timedelta(days=s2a[1]))])
    df = admin_lookup(df)
    df = df[COLUMNS]
    df.to_parquet(ROOT / "data/tabular/indiafloodbench.parquet", index=False)
    df.to_csv(ROOT / "data/tabular/indiafloodbench.csv", index=False)
    write_events_csv(events_done, rg)
    write_splits(df)
    print(f"packaged {len(df)} rows from {len(frames)} events")


def write_events_csv(events_done, rg):
    rows = []
    for ev, st in events_done:
        meta_p = ROOT / f"data/metadata/{ev['event_id']}.json"
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
        im = st.get("imagery", {})
        rows.append({
            "event_id": ev["event_id"], "slug": ev["slug"], "status": st.get("status"), "state": ev["state"],
            "region": ev["region"], "region_group": rg[ev["event_id"]], "event_type": ev["flood_type"],
            "event_start": ev["event_start"], "event_end": ev["event_end"], "label_date": ev["label_date"],
            "before_window": "/".join(ev["before_window"]), "bbox": " ".join(map(str, ev["bbox"])),
            "s1_relative_orbit": im.get("s1_relative_orbit"), "s1_orbit_state": im.get("s1_orbit_state"),
            "label_source": LABEL_META[ev["label_source"]]["label_source"],
            "label_resolution_m": LABEL_META[ev["label_source"]]["label_resolution_m"],
            "label_type": LABEL_META[ev["label_source"]]["label_type"],
            "label_confidence": LABEL_META[ev["label_source"]]["label_confidence"],
            "patches": meta.get("patches_requested", 0) - len(meta.get("patches_failed", {})) if meta else 0,
            "total_samples": meta.get("total_samples", 0), "positive_samples": meta.get("positive_samples", 0),
            "negative_samples": meta.get("negative_samples", 0),
            "positive_percentage": meta.get("positive_percentage"),
            "aoi_flood_fraction_of_labelled_px": st.get("label_stats", {}).get("aoi_flood_fraction_of_labelled_px"),
            "failure_reason": st.get("error") if st.get("status") != "complete" else "",
            "notes": ev["notes"],
        })
    pd.DataFrame(rows).to_csv(ROOT / "events.csv", index=False)
    (ROOT / "configs/label_sources.json").write_text(json.dumps(LABEL_META, indent=1))


def write_splits(df):
    ev_ids = sorted(df["event_id"].unique())
    groups = sorted(df["region_group"].unique())
    splits = {
        "note": "Suggested leakage-safe protocols. Never split samples of one patch across train/test; "
                "patches of one event share a date and often a river system, so event- or region-level "
                "holdout is the honest generalisation test.",
        "leave_one_event_out": {e: {"test_events": [e]} for e in ev_ids},
        "leave_one_region_group_out": {g: {"test_events": sorted(df.loc[df.region_group == g, "event_id"].unique())}
                                       for g in groups},
        "by_flood_type": {t: sorted(df.loc[df.flood_type == t, "event_id"].unique())
                          for t in sorted(df["flood_type"].unique())},
        "spatial_block_column": "patch_id",
    }
    (ROOT / "data/metadata/splits.json").write_text(json.dumps(splits, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "package"
    if cmd == "package":
        package()
    else:
        sys.path.insert(0, str(ROOT / "scripts"))
        from report_md import write_reports  # noqa: PLC0415
        write_reports(ROOT)
