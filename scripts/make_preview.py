"""Draw docs/patch_preview.png: one flood patch per event, showing what each layer looks like.

Columns: Sentinel-2 true colour before the flood, Sentinel-1 VV before, Sentinel-1 VV on the label
date, and the flood label. Picks, per event, the flood patch with the most flood pixels.
Usage: python scripts/make_preview.py [EVENT_ID ...]   (default: IFB-001 IFB-003 IFB-007 IFB-010)
"""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import rasterio

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EVENTS = {e["event_id"]: e for e in json.loads((ROOT / "configs/events.json").read_text())}


def read(pdir, name):
    with rasterio.open(pdir / f"{name}.tif") as ds:
        return ds.read().astype("float32")


def best_patch(eid):
    metas = [json.loads(p.read_text()) for p in (ROOT / "data/raster" / eid).glob("*/patch.json")]
    m = max(metas, key=lambda m: m["label_px"]["flood"])
    return ROOT / "data/raster" / eid / m["patch_id"]


def main(eids):
    fig, axes = plt.subplots(len(eids), 4, figsize=(13, 3.3 * len(eids)))
    label_cmap = ListedColormap(["#d9d9d9", "#1f5fbf"])
    for row, eid in zip(np.atleast_2d(axes), eids):
        pdir = best_patch(eid)
        ev = EVENTS[eid]
        s2 = read(pdir, "s2_before")[[2, 1, 0]] / 10000  # B4, B3, B2
        rgb = np.clip(np.moveaxis(s2, 0, -1) / 0.25, 0, 1)
        rgb[np.any(s2 == 0, axis=0)] = 1  # no clear observation -> white
        vv_b, vv_a = read(pdir, "s1_before")[0], read(pdir, "s1_after")[0]
        lab = read(pdir, "labels")[0]
        lab = np.ma.masked_equal(lab, 255)
        panels = [(rgb, None, "Sentinel-2 before (true colour)"),
                  (vv_b, "gray", "Sentinel-1 VV before (dB)"),
                  (vv_a, "gray", f"Sentinel-1 VV on {ev['label_date']} (dB)"),
                  (lab, label_cmap, "label: blue flood, grey not, white none")]
        for ax, (img, cmap, title) in zip(row, panels):
            kw = {"vmin": -25, "vmax": 0} if cmap == "gray" else {"vmin": 0, "vmax": 1} if cmap else {}
            ax.imshow(img, cmap=cmap, interpolation="nearest", **kw)
            ax.set_title(title, fontsize=9)
            ax.set_xticks([])
            ax.set_yticks([])
        row[0].set_ylabel(f"{eid} {ev['slug']}\n{ev['flood_type']}", fontsize=9)
    fig.suptitle("IndiaFloodBench-GEE: one 5.12 km patch per event (20 m pixels)", fontsize=11)
    fig.tight_layout()
    out = ROOT / "docs/patch_preview.png"
    fig.savefig(out, dpi=110)
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["IFB-001", "IFB-003", "IFB-007", "IFB-010"])
