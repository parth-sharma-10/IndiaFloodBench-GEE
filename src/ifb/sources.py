"""Label data access: EODC STAC search for GFM and reprojection of remote COGs onto a patch grid.

Features come from Google Earth Engine (features.py); only labels are read here."""
import functools
import os
import time
from dataclasses import dataclass

import numpy as np
import rasterio
from pystac_client import Client
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
from rasterio.warp import Resampling, reproject

os.environ.update(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MAX_RETRY="3",
    GDAL_HTTP_RETRY_DELAY="3",
    GDAL_HTTP_TIMEOUT="60",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF,.tiff,.cog",
    GDAL_CACHEMAX="512",
    # This link truncates transfers mid-body. /vsicurl caches downloaded blocks per URL, so a
    # truncated block would be served again on every retry in the same process: disable that
    # cache, retry on any curl error, and abort stalled transfers so the retry happens quickly.
    CPL_VSIL_CURL_NON_CACHED="/vsicurl/https://",
    GDAL_HTTP_RETRY_CODES="429,500,502,503,504",
    GDAL_HTTP_CONNECTTIMEOUT="20",
    GDAL_HTTP_LOW_SPEED_TIME="30",
    GDAL_HTTP_LOW_SPEED_LIMIT="2000",
    GDAL_HTTP_TCP_KEEPALIVE="YES",
)

EODC_STAC = "https://stac.eodc.eu/api/v1"


def retry(tries=6, wait=10):
    """Retry on any exception; the network here drops DNS and connections intermittently."""
    def deco(fn):
        @functools.wraps(fn)
        def inner(*a, **k):
            for i in range(tries):
                try:
                    return fn(*a, **k)
                except Exception as e:  # noqa: BLE001 - re-raised after the last attempt
                    if i == tries - 1:
                        raise
                    root = e
                    while root.__cause__ is not None:
                        root = root.__cause__
                    print(f"  retry {fn.__name__} ({type(e).__name__}: {str(root)[:160]})", flush=True)
                    time.sleep(wait * (i + 1))
        return inner
    return deco


@dataclass(frozen=True)
class Grid:
    crs: str
    x0: float  # upper-left x
    y0: float  # upper-left y
    res: float
    width: int
    height: int

    @property
    def transform(self):
        return from_origin(self.x0, self.y0, self.res, self.res)

    @property
    def bounds(self):
        return (self.x0, self.y0 - self.height * self.res, self.x0 + self.width * self.res, self.y0)

    def sub(self, row, col, size):
        return Grid(self.crs, self.x0 + col * self.res, self.y0 - row * self.res, self.res, size, size)


def utm_epsg(lon, lat):
    return f"EPSG:{(32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1}"


@functools.lru_cache(maxsize=None)
def client(url):
    return Client.open(url)


@retry(tries=8, wait=5)
def search(url, **kw):
    return list(client(url).search(**kw).items())


@retry(tries=4, wait=3)
def read_to_grid(href, grid, resampling=Resampling.nearest, band=1, src_nodata=None):
    """Read one band of a remote raster onto `grid` as float32; pixels with no data are NaN.

    Same-CRS sources use a direct windowed read, which is much cheaper over
    HTTP than a warp; everything else is warped."""
    out = np.full((grid.height, grid.width), np.nan, dtype="float32")
    with rasterio.open(href) as src:
        nd = src_nodata if src_nodata is not None else src.nodata
        if src.crs == CRS.from_user_input(grid.crs):
            win = from_bounds(*grid.bounds, transform=src.transform)
            fill = nd if nd is not None else 0
            a = src.read(band, window=win, out_shape=out.shape, resampling=resampling,
                         boundless=True, fill_value=fill).astype("float32")
            if nd is not None:
                a[a == nd] = np.nan
            return a
        reproject(rasterio.band(src, band), out, src_nodata=nd, dst_nodata=np.nan,
                  dst_transform=grid.transform, dst_crs=grid.crs, resampling=resampling)
    return out


def mosaic(hrefs, grid, resampling=Resampling.nearest, src_nodata=None):
    """First-valid mosaic of several rasters (e.g. GFM Equi7 tiles) onto `grid`."""
    out = np.full((grid.height, grid.width), np.nan, dtype="float32")
    for h in hrefs:
        if not np.isnan(out).any():
            break
        a = read_to_grid(h, grid, resampling, src_nodata=src_nodata)
        fill = np.isnan(out) & ~np.isnan(a)
        out[fill] = a[fill]
    return out
