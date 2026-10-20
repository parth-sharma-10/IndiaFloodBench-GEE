"""Write one patch as a set of aligned, DEFLATE-compressed GeoTIFFs plus patch metadata."""
import numpy as np
import rasterio

NAN = float("nan")


def _write(path, grid, bands, names, dtype, nodata):
    prof = dict(driver="GTiff", width=grid.width, height=grid.height, count=len(bands), dtype=dtype,
                crs=grid.crs, transform=grid.transform, nodata=nodata, compress="deflate",
                predictor=3 if dtype == "float32" else 2, tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(path, "w", **prof) as ds:
        for i, (b, n) in enumerate(zip(bands, names), 1):
            ds.write(b.astype(dtype), i)
            ds.set_band_description(i, n)


def _f32(a, shape):
    return np.full(shape, NAN, "float32") if a is None else a


def _refl_u16(b):
    """Reflectance (0-1) -> uint16 DN x10000 with 0 as nodata."""
    out = np.where(np.isnan(b), 0, np.clip(np.round(b * 10000), 1, 65535))
    return out.astype("uint16")


def _u8(a, nodata=255):
    return np.where(np.isnan(a), nodata, np.round(a)).astype("uint8")


def write_patch(out_dir, g, lay, df, ev, p):
    out_dir.mkdir(parents=True, exist_ok=True)
    shp = (g.height, g.width)
    s2n = ["B2", "B3", "B4", "B8", "B11", "B12"]
    for tag in ("before", "after"):
        _write(out_dir / f"s1_{tag}.tif", g, [_f32(lay[f"VV_{tag}"], shp), _f32(lay[f"VH_{tag}"], shp)],
               [f"VV_{tag}_dB", f"VH_{tag}_dB"], "float32", NAN)
        _write(out_dir / f"s2_{tag}.tif", g, [_refl_u16(b) for b in lay[f"s2_{tag}"]],
               [f"{n}_{tag}" for n in s2n], "uint16", 0)
    vvc = _f32(lay["VV_after"], shp) - _f32(lay["VV_before"], shp)
    vhc = _f32(lay["VH_after"], shp) - _f32(lay["VH_before"], shp)
    names = ["VV_change", "VH_change"]
    bands = [vvc, vhc]
    for k in ("NDVI", "NDWI", "MNDWI", "NDBI"):
        for tag in ("before", "after"):
            names.append(f"{k}_{tag}")
            bands.append(lay[f"{k}_{tag}"])
    names += ["s2_before_offset_days", "s2_after_offset_days"]
    bands += [lay["s2_before_dt"], lay["s2_after_dt"]]
    _write(out_dir / "derived.tif", g, bands, names, "float32", NAN)
    rk = sorted((k for k in lay if k.startswith("rainfall_")), key=lambda k: int(k.split("_")[1][:-1]))
    _write(out_dir / "rainfall.tif", g, [lay[k] for k in rk], rk, "float32", NAN)
    _write(out_dir / "terrain.tif", g, [lay["elevation"], lay["slope"]], ["elevation_m", "slope_deg"], "float32", NAN)
    _write(out_dir / "landcover.tif", g, [_u8(lay["landcover"]), _u8(lay["jrc_occurrence"])],
           ["esa_worldcover_2021_class", "jrc_gsw_occurrence_pct"], "uint8", 255)
    _write(out_dir / "labels.tif", g, [lay["label"], _u8(lay["confidence"]), lay["gfm_flood"]],
           ["flood_label", "flood_confidence", "gfm_flood_extent"], "uint8", 255)

    lab = lay["label"]
    meta = {"patch_id": p["patch_id"], "event_id": ev["event_id"], "kind": p["kind"], "crs": g.crs,
            "transform": list(g.transform)[:6], "width": g.width, "height": g.height, "pixel_size_m": g.res,
            "bounds_lonlat": None,
            "label_px": {"flood": int((lab == 1).sum()), "non_flood": int((lab == 0).sum()),
                         "no_label": int((lab == 255).sum())},
            "s1_before_dates": lay["s1_before_dates"], "s1_after_dates": lay["s1_after_dates"],
            "s2_before_items": lay["s2_before_items"], "s2_after_items": lay["s2_after_items"],
            "valid_fraction": {k: float(np.mean(~np.isnan(lay[k]))) if lay[k] is not None else 0.0
                               for k in ("VV_before", "VV_after", "NDVI_before", "NDVI_after", "elevation", "rainfall_1d")},
            "samples": int(len(df))}
    from rasterio.warp import transform_bounds  # noqa: PLC0415
    meta["bounds_lonlat"] = list(transform_bounds(g.crs, "EPSG:4326", *g.bounds))
    if p["kind"] == "hand":
        meta["chip_file"] = p["chip_file"]
        # same definition (observed non-permanent water) and GFM's own anomaly-based flood extent
        meta["gfm_vs_hand"] = _agreement(lay["gfm_water_label"], lab)
        meta["gfm_flood_extent_vs_hand"] = _agreement(lay["gfm_flood"], lab)
    return meta


def _agreement(gfm, hand):
    both = (gfm != 255) & (hand != 255)
    g, h = gfm[both] == 1, hand[both] == 1
    return {"pixels": int(both.sum()), "tp": int((g & h).sum()), "fp": int((g & ~h).sum()),
            "fn": int((~g & h).sum()), "tn": int((~g & ~h).sum())}
