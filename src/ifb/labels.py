"""Flood reference labels. Encoding everywhere: 1 = flood, 0 = not flood, 255 = no label.

One definition for every event, whatever the source:
    flood = observed (non-permanent) water = observed water AND NOT JRC permanent water.
GFM's own flood extent subtracts a *monthly* reference water mask, which in Indian monsoon
floodplains already contains the seasonal inundation (on IFB-001 it recovered 2.8% of the
hand-labelled flood). It is kept as an extra layer (`gfm_flood_extent`), not used as the label.
"""
import numpy as np
from rasterio.warp import Resampling

from .sources import EODC_STAC, mosaic, search

NOLABEL = 255


def gfm_items(bbox, label_day):
    """GFM (Copernicus Global Flood Monitoring) items for every Equi7 tile of the label-day pass(es)."""
    return search(EODC_STAC, collections=["GFM"], bbox=bbox,
                  datetime=f"{label_day}T00:00:00Z/{label_day}T23:59:59Z")


def gfm_layers(items, grid, permanent, ocean):
    """Label from GFM ensemble observed water minus JRC permanent water.

    `ocean` = pixels outside the JRC Global Surface Water land/inland-water mapping (JRC no data).
    Without it open sea is "observed water, not permanent" and would be labelled flood.
    Pixels in the GFM exclusion mask (radar shadow/layover, dense urban, permanently low backscatter,
    no S1 sensitivity) or outside the scene footprint get no label (255).
    Returns label, GFM ensemble likelihood (0-100, NaN where none), GFM flood extent (0/1/255).
    """
    obs = mosaic([i.assets["ensemble_water_extent"].href for i in items], grid, src_nodata=255)
    flood = mosaic([i.assets["ensemble_flood_extent"].href for i in items], grid, src_nodata=255)
    excl = mosaic([i.assets["exclusion_mask"].href for i in items], grid, src_nodata=255)
    lik = mosaic([i.assets["ensemble_likelihood"].href for i in items], grid, src_nodata=255)
    ok = ~np.isnan(obs) & (excl != 1) & ~ocean
    lab = np.full(obs.shape, NOLABEL, dtype="uint8")
    lab[ok] = ((obs[ok] == 1) & ~permanent[ok]).astype("uint8")
    gfm_flood = np.full(obs.shape, NOLABEL, dtype="uint8")
    okf = ok & ~np.isnan(flood)
    gfm_flood[okf] = flood[okf].astype("uint8")
    return lab, lik, gfm_flood


def hand_label(path, grid, permanent, ocean):
    """Sen1Floods11 hand label (-1 no data, 0 dry, 1 water) -> flood = water outside JRC permanent water."""
    water = mosaic([path], grid, Resampling.nearest, src_nodata=-1)
    lab = np.full(water.shape, NOLABEL, dtype="uint8")
    ok = ~np.isnan(water) & ~ocean
    lab[ok] = ((water[ok] == 1) & ~permanent[ok]).astype("uint8")
    return lab
