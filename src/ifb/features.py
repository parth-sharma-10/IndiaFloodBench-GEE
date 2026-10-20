"""Feature extraction from Google Earth Engine onto one patch grid:
Sentinel-1, Sentinel-2, CHIRPS, Copernicus DEM, ESA WorldCover and JRC surface water.

Every layer is computed server-side and fetched as a NumPy array with ee.data.computePixels on the
exact 20 m UTM grid of the patch, so all layers stay pixel-aligned with the labels.
"""
import os
import socket
from datetime import date, datetime, timedelta, timezone

import ee
import numpy as np

from .sources import retry

EE_PROJECT = os.environ.get("EE_PROJECT", "indiafloodbench")  # your registered Cloud project
S2_BANDS = ["B2", "B3", "B4", "B8", "B11", "B12"]
NODATA = -9999.0


@retry(tries=6, wait=10)
def init():
    # Reads on this link can stall forever; a socket timeout turns a stall into a retryable error.
    socket.setdefaulttimeout(120)
    # The high-volume endpoint is the one Google recommends for many small computePixels calls.
    ee.Initialize(project=EE_PROJECT, opt_url="https://earthengine-highvolume.googleapis.com")


def d(s):
    return date.fromisoformat(s[:10])


def _proj(grid):
    return ee.Projection(grid.crs, [grid.res, 0, grid.x0, 0, -grid.res, grid.y0])


def footprint(grid):
    x0, y0, x1, y1 = grid.bounds
    return ee.Geometry.Rectangle([x0, y0, x1, y1], ee.Projection(grid.crs), False)


@retry(tries=6, wait=10)
def pixels(image, grid):
    """Fetch every band of `image` on `grid`; masked pixels come back as NaN."""
    arr = ee.data.computePixels({
        "expression": image.toFloat().unmask(NODATA, False),
        "fileFormat": "NUMPY_NDARRAY",
        "grid": {"dimensions": {"width": grid.width, "height": grid.height},
                 "affineTransform": {"scaleX": grid.res, "shearX": 0, "translateX": grid.x0,
                                     "shearY": 0, "scaleY": -grid.res, "translateY": grid.y0},
                 "crsCode": grid.crs},
    })
    out = {}
    for name in arr.dtype.names:
        a = arr[name].astype("float32")
        a[a == NODATA] = np.nan
        out[name] = a
    return out


# ---------------- Sentinel-1 (COPERNICUS/S1_GRD: sigma0 dB, 10 m) ----------------

def s1_collection(region, start, end, rel_orbit=None, orbit_pass=None):
    c = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(region).filterDate(start, end)
         .filter(ee.Filter.eq("instrumentMode", "IW"))
         .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
         .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH")))
    if rel_orbit is not None:
        c = c.filter(ee.Filter.eq("relativeOrbitNumber_start", rel_orbit))
    if orbit_pass is not None:
        c = c.filter(ee.Filter.eq("orbitProperties_pass", orbit_pass.upper()))
    return c.select(["VV", "VH"])


@retry(tries=6, wait=10)
def s1_after_scenes(bbox, label_dt):
    """S1 GRD scenes within 1 h of the GFM label pass: ids, relative orbit, pass direction."""
    t = ee.Date(label_dt.isoformat())
    c = s1_collection(ee.Geometry.Rectangle(bbox), t.advance(-1, "hour"), t.advance(1, "hour"))
    info = c.aggregate_array("system:index").getInfo()
    if not info:
        return [], None, None
    first = c.first()
    return info, first.get("relativeOrbitNumber_start").getInfo(), \
        first.get("orbitProperties_pass").getInfo().lower()


@retry(tries=6, wait=10)
def s1_last_pass(bbox, day):
    """Time of the last S1 GRD acquisition over bbox on `day` (used when there is no GFM pass)."""
    c = s1_collection(ee.Geometry.Rectangle(bbox), str(day), str(day + timedelta(days=1)))
    ms = c.aggregate_max("system:time_start").getInfo()
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


@retry(tries=6, wait=10)
def s1_dates(region, window, rel_orbit, orbit_pass):
    """Distinct acquisition dates (UTC) on this orbit and direction inside the window."""
    end = str(d(window[1]) + timedelta(days=1))
    c = s1_collection(region, window[0], end, rel_orbit, orbit_pass)
    ms = c.aggregate_array("system:time_start").getInfo()
    return sorted({datetime.fromtimestamp(m / 1000, tz=timezone.utc).date().isoformat() for m in ms})


def _linear(img):
    return ee.Image(10).pow(img.divide(10))


def s1_stack(grid, dates, rel_orbit, orbit_pass, max_dates):
    """Most recent `max_dates` dates that cover the patch: per-date mosaic, 2x2 multilook (mean in
    linear power), temporal median in linear power, then dB. Returns VV, VH (dB) and dates used."""
    region = footprint(grid)
    proj = _proj(grid)
    used, imgs = [], []
    for day in sorted(dates, reverse=True):
        nxt = str(d(day) + timedelta(days=1))
        col = s1_collection(region, day, nxt, rel_orbit, orbit_pass)
        # mosaic() drops the native 10 m projection that reduceResolution needs
        lin = _linear(col.mosaic().setDefaultProjection(col.first().select("VV").projection()))
        look = lin.reduceResolution(ee.Reducer.mean(), maxPixels=64).reproject(proj)
        valid = look.select("VV").mask().reduceRegion(ee.Reducer.max(), region, grid.res,
                                                      crs=grid.crs).get("VV")
        if not ee.Number(valid).getInfo():
            continue
        used.append(day)
        imgs.append(look)
        if len(used) == max_dates:
            break
    if not used:
        return None, None, []
    med = ee.ImageCollection(imgs).median()
    a = pixels(med.log10().multiply(10).rename(["VV", "VH"]), grid)
    return a["VV"], a["VH"], sorted(used)


# ---------------- Sentinel-2 (COPERNICUS/S2_SR_HARMONIZED) ----------------

def s2_product(window):
    """Earth Engine has almost no Sentinel-2 L2A (surface reflectance) over India before late 2018
    (Kerala Aug 2018: 0 L2A vs 10 L1C scenes; Assam Apr-Jun 2016: 0 vs 68). Windows ending before
    2019 therefore use L1C top-of-atmosphere reflectance with an s2cloudless cloud mask."""
    return "L2A_SR" if d(window[1]) >= date(2019, 1, 1) else "L1C_TOA"


S2_CLOUD_PROB_MAX = 40  # s2cloudless probability above which an L1C pixel counts as cloud


def _s2_collection(region, start, end, max_cloud, product, valid_scl):
    """Cloud-masked Sentinel-2 bands, 0-1 reflectance, from the harmonized collection of `product`
    (harmonized = the baseline-04.00 +1000 offset is already removed)."""
    if product == "L2A_SR":
        def mask(img):
            scl = img.select("SCL")
            ok = scl.eq(valid_scl[0])
            for v in valid_scl[1:]:
                ok = ok.Or(scl.eq(v))
            return img.updateMask(ok)
        col = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    else:
        prob = ee.ImageCollection("COPERNICUS/S2_CLOUD_PROBABILITY").filterBounds(region).filterDate(start, end)

        def mask(img):
            p = ee.Image(img.get("cloud")).select("probability")
            return img.updateMask(p.lt(S2_CLOUD_PROB_MAX))
        col = ee.Join.saveFirst("cloud").apply(
            ee.ImageCollection("COPERNICUS/S2_HARMONIZED").filterBounds(region).filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud)),
            prob, ee.Filter.equals(leftField="system:index", rightField="system:index"))
        return ee.ImageCollection(col).map(mask)
    return (col.filterBounds(region).filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud)).map(mask))


def s2_closest(grid, window, ref_day, valid_scl, max_cloud):
    """Per pixel, the clear observation closest in time to ref_day.

    Returns bands (6,H,W) as reflectance 0-1, the signed day offset per pixel, the candidate scene
    ids, and the product used (see s2_product)."""
    region = footprint(grid)
    ref = ee.Date(str(ref_day))
    end = str(d(window[1]) + timedelta(days=1))
    product = s2_product(window)

    def prep(img):
        ok = img.select("B2").mask()
        day = ee.Date(img.date().format("YYYY-MM-dd"))
        dt = day.difference(ref, "day")
        refl = img.select(S2_BANDS).divide(10000).updateMask(ok)
        return (refl.addBands(ee.Image.constant(dt.abs().multiply(-1)).rename("closeness").toFloat())
                .addBands(ee.Image.constant(dt).rename("offset").toFloat())
                .updateMask(ok))

    col = _s2_collection(region, window[0], end, max_cloud, product, valid_scl)
    ids = retry(tries=6, wait=10)(lambda: col.aggregate_array("system:index").getInfo())()
    if not ids:
        nan = np.full((grid.height, grid.width), np.nan, "float32")
        return np.stack([nan] * 6), nan.copy(), [], product
    mos = col.map(prep).qualityMosaic("closeness").select(S2_BANDS + ["offset"])
    a = pixels(mos, grid)
    return np.stack([a[b] for b in S2_BANDS]), a["offset"], ids, product


def nd(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (a - b) / (a + b)
    r[~np.isfinite(r)] = np.nan
    return r.astype("float32")


def indices(b):
    """b = (B2,B3,B4,B8,B11,B12). NDWI = McFeeters (green-NIR); MNDWI = Xu (green-SWIR1)."""
    _, g, r, n, s1, _ = b
    return {"NDVI": nd(n, r), "NDWI": nd(g, n), "MNDWI": nd(g, s1), "NDBI": nd(s1, n)}


# ---------------- CHIRPS rainfall (UCSB-CHG/CHIRPS/DAILY) ----------------

def rainfall(grid, label_day, windows):
    """rainfall_Nd = sum of CHIRPS daily totals for days [label-N, label-1]. The acquisition day is
    excluded because CHIRPS days are full-day totals while S1 passes over India at ~00:30 or
    ~12:30 UTC. filterDate's end is exclusive, so ending at label_day stops at label-1."""
    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").select("precipitation")
    end = ee.Date(str(label_day))
    img = ee.Image.cat([chirps.filterDate(end.advance(-n, "day"), end).sum()
                        .setDefaultProjection("EPSG:4326", None, 5566).resample("bilinear")
                        .rename(f"rainfall_{n}d") for n in windows])
    return pixels(img, grid)


# ---------------- Terrain, land cover, permanent water ----------------

def static_layers(grid):
    """Elevation (Copernicus GLO-30, bilinear), WorldCover 2021 class (mode of 10 m pixels) and JRC
    GSW occurrence %. JRC is NaN where JRC has no data (open sea); 0 where land was never water."""
    proj = _proj(grid)
    dem = (ee.ImageCollection("COPERNICUS/DEM/GLO30_2024_1").select("DEM").mosaic()
           .setDefaultProjection("EPSG:4326", None, 30).resample("bilinear").rename("elevation"))
    wc = (ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")
          .reduceResolution(ee.Reducer.mode(), maxPixels=64).reproject(proj).rename("landcover"))
    a = pixels(ee.Image.cat([dem, wc, _jrc()]), grid)
    a["landcover"][a["landcover"] == 0] = np.nan
    return a


def _jrc():
    """JRC occurrence %, 0 where never water, masked (NaN) over open sea.

    Earth Engine's GSW does not mark the sea as no-data (max_extent is 0 offshore), and without a
    sea mask open sea is "water that is not permanent water" and gets labelled flood. The sea mask
    is the Copernicus DEM water-body band: WBM 1 = ocean, and no WBM tile at all = open sea."""
    gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater")
    wbm = ee.ImageCollection("COPERNICUS/DEM/GLO30_2024_1").select("WBM").mosaic() \
        .setDefaultProjection("EPSG:4326", None, 30)
    return gsw.select("occurrence").unmask(0).updateMask(wbm.unmask(1).neq(1)) \
        .rename("jrc_occurrence")


def jrc_occurrence(grid):
    return pixels(_jrc(), grid)["jrc_occurrence"]


def slope(elev, res):
    gy, gx = np.gradient(elev, res)
    return np.degrees(np.arctan(np.hypot(gx, gy))).astype("float32")
