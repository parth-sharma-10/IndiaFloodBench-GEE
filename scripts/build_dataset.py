"""Build IndiaFloodBench: per-event, resumable. Usage: python scripts/build_dataset.py [EVENT_ID ...]

Stages per event (recorded in state/<event_id>.json):
  discovered -> configured -> imagery_checked -> label_checked -> exported -> validated -> complete
A failed event is marked `failed` with the error and the run continues with the next event.
Patches already written (patch.json present) are not regenerated.
"""
import glob
import hashlib
import json
import sys
import faulthandler
import pickle
import subprocess
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ee
import numpy as np
import pandas as pd
import rasterio
import shapely
from pyproj import Transformer
from rasterio.warp import transform_bounds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ifb import features as F  # noqa: E402
from ifb import labels as L  # noqa: E402
from ifb.sources import Grid, utm_epsg  # noqa: E402
from ifb.writer import write_patch  # noqa: E402

CFG = json.loads((ROOT / "configs/dataset_config.json").read_text())
EVENTS = json.loads((ROOT / "configs/events.json").read_text())
S1F11 = ROOT / "data/external/sen1floods11_india"
STAGES = ["discovered", "configured", "imagery_checked", "label_checked", "exported", "validated", "complete"]


# ---------------- state ----------------

def load_state(eid):
    p = ROOT / "state" / f"{eid}.json"
    return json.loads(p.read_text()) if p.exists() else {"event_id": eid, "status": "discovered", "history": []}


def save_state(st, status=None, **extra):
    if status:
        st["status"] = status
        st["history"].append({"status": status, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    st.update(extra)
    (ROOT / "state" / f"{st['event_id']}.json").write_text(json.dumps(st, indent=1, default=str))


def reached(st, stage):
    return st["status"] in STAGES and STAGES.index(st["status"]) >= STAGES.index(stage)


# ---------------- stage: configured ----------------

def check_config(ev):
    req = ["event_id", "state", "flood_type", "event_start", "event_end", "label_date", "before_window", "bbox", "label_source"]
    missing = [k for k in req if not ev.get(k)]
    assert not missing, f"missing config keys {missing}"
    b0, b1 = map(date.fromisoformat, ev["before_window"])
    s, lab = date.fromisoformat(ev["event_start"]), date.fromisoformat(ev["label_date"])
    assert b0 < b1 < s <= lab, f"temporal order violated: before {b0}..{b1}, start {s}, label {lab}"
    w, so, e, n = ev["bbox"]
    assert 68 <= w < e <= 98 and 6 <= so < n <= 37, f"bbox outside India: {ev['bbox']}"


# ---------------- stage: imagery_checked ----------------

def check_imagery(ev):
    bbox, lab = ev["bbox"], date.fromisoformat(ev["label_date"])
    gfm = L.gfm_items(bbox, lab)
    if gfm:
        label_dt = max(i.datetime for i in gfm)  # one pass; drop tiles from any other pass that day
        gfm = [i for i in gfm if abs((i.datetime - label_dt).total_seconds()) < 3600]
    else:
        assert ev["label_source"] != "gfm", f"no GFM scene on {lab}"
        label_dt = F.s1_last_pass(bbox, lab)
    after, orbit, state = F.s1_after_scenes(bbox, label_dt)
    assert after, f"no Sentinel-1 GRD scene in Earth Engine matching GFM pass {label_dt}"
    before = F.s1_dates(ee.Geometry.Rectangle(bbox), ev["before_window"], orbit, state)
    assert before, f"no Sentinel-1 before scenes on rel. orbit {orbit} {state} in {ev['before_window']}"
    a0, a1 = CFG["s2_after_window_days"]
    s2a_win = [str(lab + timedelta(days=a0)), str(lab + timedelta(days=a1))]
    return dict(gfm=gfm, after=after, before_dates=before, label_dt=label_dt, orbit=orbit,
                orbit_state=state, s2_after_window=s2a_win)


def imagery_summary(ctx):
    return {"gfm_items": [i.id for i in ctx["gfm"]], "label_datetime": ctx["label_dt"].isoformat(),
            "s1_after_scenes": ctx["after"], "s1_relative_orbit": ctx["orbit"],
            "s1_orbit_state": ctx["orbit_state"], "s1_before_dates_available": ctx["before_dates"],
            "s2_after_window": ctx["s2_after_window"], "feature_source": "Google Earth Engine"}


# ---------------- stage: label_checked (patch selection) ----------------

def aoi_grid(bbox, res):
    crs = utm_epsg((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", crs, *bbox)
    step = CFG["pixel_size_m"] * CFG["patch_size_px"]
    x0, y1 = np.floor(x0 / step) * step, np.ceil(y1 / step) * step
    w, h = int(np.ceil((x1 - x0) / step)), int(np.ceil((y1 - y0) / step))
    k = step / res
    return Grid(crs, x0, y1, res, int(w * k), int(h * k)), w, h


def select_gfm_patches(ev, ctx):
    """Score every 256x256 patch of the AOI on a coarse (80 m) label read, then draw flood and dry patches."""
    coarse, nx, ny = aoi_grid(ev["bbox"], 80.0)
    occ = F.jrc_occurrence(coarse)
    perm = np.nan_to_num(occ) >= CFG["permanent_water_occurrence_pct"]
    lab = L.gfm_layers(ctx["gfm"], coarse, perm, np.isnan(occ))[0]
    k = CFG["pixel_size_m"] * CFG["patch_size_px"] // 80
    t = lab[: ny * k, : nx * k].reshape(ny, k, nx, k).transpose(0, 2, 1, 3).reshape(ny, nx, -1)
    valid = (t != L.NOLABEL).mean(-1)
    flood = (t == 1).mean(-1)
    ok = valid >= CFG["patch_min_valid_frac"]
    rng = np.random.default_rng(CFG["seed"] + int(ev["event_id"][-3:]))
    fl = np.argwhere(ok & (flood >= CFG["flood_patch_min_frac"]))
    dry = np.argwhere(ok & (flood <= CFG["dry_patch_max_frac"]))
    pick = lambda a, n: a[rng.choice(len(a), min(n, len(a)), replace=False)] if len(a) else a  # noqa: E731
    chosen = [(r, c, "flood") for r, c in pick(fl, CFG["max_flood_patches"])]
    chosen += [(r, c, "dry") for r, c in pick(dry, CFG["max_dry_patches"])]
    stats = {"aoi_patches": int(nx * ny), "aoi_patches_valid": int(ok.sum()),
             "aoi_patches_flood_candidates": int(len(fl)), "aoi_patches_dry_candidates": int(len(dry)),
             "aoi_flood_fraction_of_labelled_px": float((lab == 1).sum() / max(1, (lab != L.NOLABEL).sum()))}
    size = CFG["patch_size_px"]
    fine, _, _ = aoi_grid(ev["bbox"], CFG["pixel_size_m"])
    patches = [{"patch_id": f"{ev['event_id']}_r{r:03d}c{c:03d}", "kind": kind,
                "grid": fine.sub(int(r) * size, int(c) * size, size).__dict__} for r, c, kind in chosen]
    return patches, stats


def select_hand_patches(ev):
    """One patch per Sen1Floods11 chip, centred on the chip and snapped to the 20 m grid."""
    crs = utm_epsg((ev["bbox"][0] + ev["bbox"][2]) / 2, (ev["bbox"][1] + ev["bbox"][3]) / 2)
    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    res, size = CFG["pixel_size_m"], CFG["patch_size_px"]
    patches = []
    for f in sorted(glob.glob(str(S1F11 / "India_*_LabelHand.tif"))):
        with rasterio.open(f) as ds:
            b = ds.bounds
        cx, cy = tr.transform((b.left + b.right) / 2, (b.bottom + b.top) / 2)
        x0 = round((cx - size * res / 2) / res) * res
        y0 = round((cy + size * res / 2) / res) * res
        chip = Path(f).name.split("_")[1]
        patches.append({"patch_id": f"{ev['event_id']}_chip{chip}", "kind": "hand", "chip_file": Path(f).name,
                        "grid": Grid(crs, x0, y0, res, size, size).__dict__})
    return patches, {"sen1floods11_chips": len(patches)}


# ---------------- per-patch build ----------------

def footprint(grid):
    return transform_bounds(grid.crs, "EPSG:4326", *grid.bounds)


def overlapping(items, bbox):
    box = shapely.box(*bbox)
    return [i for i in items if shapely.geometry.shape(i.geometry).intersects(box)]


def log(p, msg):
    print(f"  [{datetime.now().strftime('%H:%M:%S')}] {p['patch_id']}: {msg}", flush=True)


def patch_layers(ev, ctx, p, grid):
    bbox = list(footprint(grid))
    log(p, "start")
    out = F.static_layers(grid)
    occ = out["jrc_occurrence"]
    perm = np.nan_to_num(occ) >= CFG["permanent_water_occurrence_pct"]
    ocean = np.isnan(occ)
    gfm = overlapping(ctx["gfm"], bbox)
    if p["kind"] == "hand":
        out["label"] = L.hand_label(str(S1F11 / p["chip_file"]), grid, perm, ocean)
        out["confidence"] = np.full(out["label"].shape, np.nan, "float32")
        empty = np.full(out["label"].shape, L.NOLABEL, "uint8")
        out["gfm_water_label"], _, out["gfm_flood"] = L.gfm_layers(gfm, grid, perm, ocean) if gfm else (empty, None, empty)
    else:
        out["label"], out["confidence"], out["gfm_flood"] = L.gfm_layers(gfm, grid, perm, ocean)
    log(p, "label done")
    orbit, state = ctx["orbit"], ctx["orbit_state"]
    out["VV_after"], out["VH_after"], out["s1_after_dates"] = F.s1_stack(
        grid, [ctx["label_dt"].date().isoformat()], orbit, state, 1)
    out["VV_before"], out["VH_before"], out["s1_before_dates"] = F.s1_stack(
        grid, ctx["before_dates"], orbit, state, CFG["s1_before_max_scenes"])
    log(p, f"S1 done (before dates {out['s1_before_dates']})")
    start, lab = date.fromisoformat(ev["event_start"]), date.fromisoformat(ev["label_date"])
    for tag, window, ref in (("before", ev["before_window"], start - timedelta(days=1)),
                             ("after", ctx["s2_after_window"], lab)):
        b, off, ids, product = F.s2_closest(grid, window, ref, CFG["s2_valid_scl"], CFG["s2_max_cloud_pct"])
        out[f"s2_{tag}"], out[f"s2_{tag}_dt"], out[f"s2_{tag}_items"] = b, off, ids
        for k, v in F.indices(b).items():
            out[f"{k}_{tag}"] = v
        log(p, f"S2 {tag} done ({len(ids)} candidate {product} scenes)")
    out.update(F.rainfall(grid, lab, CFG["rainfall_windows_days"]))
    out["slope"] = F.slope(out["elevation"], grid.res)
    log(p, "static layers done")
    return out


def sample_rows(ev, p, grid, lay):
    """Stratified lattice sample: every 4th pixel, up to N flood + N non-flood per patch, with inclusion prob."""
    s, n = CFG["sample_stride_px"], CFG["samples_per_stratum_per_patch"]
    seed = int(hashlib.sha256(p["patch_id"].encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    lat_rows, lat_cols = np.mgrid[s // 2: grid.height: s, s // 2: grid.width: s]
    rr, cc = lat_rows.ravel(), lat_cols.ravel()
    lab = lay["label"][rr, cc]
    picks = []
    for value, name in ((1, "flood"), (0, "non_flood")):
        idx = np.flatnonzero(lab == value)
        take = rng.choice(idx, min(n, len(idx)), replace=False) if len(idx) else idx
        picks.append((take, name, len(take) / len(idx) if len(idx) else 0.0))
    rows = []
    for take, name, prob in picks:
        r, c = rr[take], cc[take]
        rows.append(pd.DataFrame({"row": r, "col": c, "sample_stratum": name, "inclusion_prob": prob}))
    df = pd.concat(rows, ignore_index=True)
    xs = grid.x0 + (df["col"] + 0.5) * grid.res
    ys = grid.y0 - (df["row"] + 0.5) * grid.res
    lon, lat = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True).transform(xs.values, ys.values)
    df["longitude"], df["latitude"] = np.round(lon, 6), np.round(lat, 6)
    return df


def build_patch(ev, ctx, p):
    out_dir = ROOT / "data/raster" / ev["event_id"] / p["patch_id"]
    done = out_dir / "patch.json"
    tab = out_dir / "samples.parquet"
    if done.exists() and tab.exists():
        return pd.read_parquet(tab)
    grid = Grid(**p["grid"])
    lay = patch_layers(ev, ctx, p, grid)
    df = sample_rows(ev, p, grid, lay)
    meta = write_patch(out_dir, grid, lay, df, ev, p)
    df = attach_features(df, lay, ev, p, meta)
    df.to_parquet(tab, index=False)
    done.write_text(json.dumps(meta, indent=1, default=str))
    return df


FEATURE_KEYS = ["VV_before", "VH_before", "VV_after", "VH_after", "NDVI_before", "NDVI_after", "NDWI_before",
                "NDWI_after", "MNDWI_before", "MNDWI_after", "NDBI_before", "NDBI_after", "rainfall_1d",
                "rainfall_3d", "rainfall_7d", "rainfall_14d", "elevation", "slope", "jrc_occurrence"]


def attach_features(df, lay, ev, p, meta):
    r, c = df.pop("row").values, df.pop("col").values
    for k in FEATURE_KEYS:
        df[k] = lay[k][r, c] if lay[k] is not None else np.nan
    df["VV_change"] = df["VV_after"] - df["VV_before"]
    df["VH_change"] = df["VH_after"] - df["VH_before"]
    for k in ("NDVI", "NDWI", "MNDWI"):
        df[f"{k}_change"] = df[f"{k}_after"] - df[f"{k}_before"]
    for i, b in enumerate(["B2", "B3", "B4", "B8", "B11", "B12"]):
        df[f"{b}_before"] = lay["s2_before"][i][r, c]
        df[f"{b}_after"] = lay["s2_after"][i][r, c]
    df["s2_before_offset_days"] = lay["s2_before_dt"][r, c]
    df["s2_after_offset_days"] = lay["s2_after_dt"][r, c]
    df["landcover_class"] = lay["landcover"][r, c]
    df["permanent_water"] = (np.nan_to_num(df["jrc_occurrence"]) >= CFG["permanent_water_occurrence_pct"]).astype("uint8")
    df["flood_label"] = lay["label"][r, c].astype("uint8")
    df["flood_confidence"] = lay["confidence"][r, c]
    gf = lay["gfm_flood"][r, c].astype("float32")
    gf[gf == 255] = np.nan
    df["gfm_flood_extent"] = gf
    df["patch_id"], df["patch_kind"] = p["patch_id"], p["kind"]
    df["s1_before_dates"] = ";".join(meta["s1_before_dates"])
    df["s1_after_date"] = ";".join(meta["s1_after_dates"])
    return df


# ---------------- event driver ----------------

def run_event(ev):
    eid = ev["event_id"]
    st = load_state(eid)
    if st["status"] == "complete":
        print(f"{eid}: complete, skipping")
        return
    print(f"== {eid} {ev['slug']} (state: {st['status']})", flush=True)
    try:
        check_config(ev)
        if not reached(st, "configured"):
            save_state(st, "configured")
        ctx = check_imagery(ev)
        if not reached(st, "imagery_checked"):
            save_state(st, "imagery_checked", imagery=imagery_summary(ctx))
        if not reached(st, "label_checked"):
            patches, stats = select_hand_patches(ev) if ev["label_source"] == "sen1floods11_hand" else select_gfm_patches(ev, ctx)
            kinds = {k: sum(p["kind"] == k for p in patches) for k in ("flood", "dry", "hand")}
            assert patches and (kinds["flood"] or kinds["hand"]), f"no usable flood patches: {stats}"
            save_state(st, "label_checked", patches=patches, label_stats=stats)
        if not reached(st, "exported"):
            export_event(ev, st, ctx)
            save_state(st, "exported")
        from validate_dataset import validate_event  # noqa: PLC0415 - scripts dir import
        res = validate_event(ev, st)
        save_state(st, "validated", validation=res)
        save_state(st, "complete" if res["passed"] else "failed",
                   error=None if res["passed"] else "validation failed: " + "; ".join(res["errors"]))
    except Exception as e:  # noqa: BLE001 - record and continue with the next event
        traceback.print_exc()
        save_state(st, "failed", error=f"{type(e).__name__}: {e}")


def run_patch_subprocess(ev, p, ctx_path):
    """One patch per child process with a hard timeout: GDAL HTTP reads can hang indefinitely on
    this link and a hung C call cannot be interrupted from a Python thread."""
    tab = ROOT / "data/raster" / ev["event_id"] / p["patch_id"] / "samples.parquet"
    for attempt in range(1, 4):
        if tab.exists():
            return pd.read_parquet(tab)
        plog = ROOT / "reports/patch_logs" / f"{p['patch_id']}.log"
        plog.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(plog, "a") as fh:
                fh.write(f"--- attempt {attempt} {datetime.now().isoformat(timespec='seconds')}\n")
                fh.flush()
                subprocess.run([sys.executable, "-u", __file__, "--patch", ev["event_id"], p["patch_id"], str(ctx_path)],
                               timeout=CFG.get("patch_timeout_s", 1200), stdout=fh, stderr=subprocess.STDOUT)
        except subprocess.TimeoutExpired:
            print(f"  {p['patch_id']}: attempt {attempt} timed out (see {plog.name})", flush=True)
    if tab.exists():
        print(f"  {p['patch_id']}: written", flush=True)
        return pd.read_parquet(tab)
    raise RuntimeError(f"{p['patch_id']} failed after 3 attempts")


def export_event(ev, st, ctx):
    eid = ev["event_id"]
    ctx_path = ROOT / "state" / f"{eid}_ctx.pkl"
    ctx_path.write_bytes(pickle.dumps(ctx))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {p["patch_id"]: pool.submit(run_patch_subprocess, ev, p, ctx_path) for p in st["patches"]}
    frames, failed = [], {}
    for pid, f in futs.items():
        try:
            frames.append(f.result())
            print(f"  {pid} ok", flush=True)
        except Exception as e:  # noqa: BLE001 - a single bad patch must not sink the event
            failed[pid] = f"{type(e).__name__}: {e}"
            print(f"  {pid} FAILED {failed[pid][:200]}", flush=True)
    assert frames, f"all patches failed: {failed}"
    df = pd.concat(frames, ignore_index=True)
    for k in ("event_id", "slug", "state", "region", "flood_type", "event_start", "event_end", "label_date", "label_source"):
        df[k] = ev[k]
    df["s1_relative_orbit"], df["s1_orbit_state"] = ctx["orbit"], ctx["orbit_state"]
    (ROOT / "data/tabular/events").mkdir(parents=True, exist_ok=True)
    df.to_parquet(ROOT / f"data/tabular/events/{eid}.parquet", index=False)
    st["failed_patches"] = failed
    write_event_metadata(ev, st, ctx, df)


def write_event_metadata(ev, st, ctx, df):
    lab = df["flood_label"]
    meta = {**ev, "imagery": imagery_summary(ctx), "label_stats": st["label_stats"],
            "patches_requested": len(st["patches"]), "patches_failed": st.get("failed_patches", {}),
            "total_samples": int(len(df)), "positive_samples": int((lab == 1).sum()),
            "negative_samples": int((lab == 0).sum()),
            "positive_percentage": round(100 * float((lab == 1).mean()), 2),
            "sampling": "stratified per patch: <=%d flood + <=%d non-flood lattice pixels (stride %d px); "
                        "inclusion_prob column gives per-stratum selection probability" % (
                            CFG["samples_per_stratum_per_patch"], CFG["samples_per_stratum_per_patch"], CFG["sample_stride_px"]),
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (ROOT / f"data/metadata/{ev['event_id']}.json").write_text(json.dumps(meta, indent=1, default=str))


def patch_main(eid, pid, ctx_path):
    faulthandler.dump_traceback_later(300, repeat=True)  # stack dump in the log if a read stalls
    F.init()
    ev = next(e for e in EVENTS if e["event_id"] == eid)
    st = load_state(eid)
    p = next(x for x in st["patches"] if x["patch_id"] == pid)
    ctx = pickle.loads(Path(ctx_path).read_bytes())
    build_patch(ev, ctx, p)
    print(f"  {pid}: written", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "scripts"))
    if sys.argv[1:2] == ["--patch"]:
        patch_main(*sys.argv[2:5])
        sys.exit(0)
    F.init()
    want = set(sys.argv[1:])
    for ev in EVENTS:
        if not want or ev["event_id"] in want or ev["slug"] in want:
            run_event(ev)
