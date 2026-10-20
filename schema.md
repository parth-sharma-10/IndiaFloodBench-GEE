# IndiaFloodBench schema

## Tabular: `data/tabular/indiafloodbench.parquet` (and identical `.csv`)

One row = one 20 m pixel sampled from a raster patch. Rows keep their coordinates, patch and event,
so any split can be made at patch, event or region level.

### Identity and location
| column | type | description |
|---|---|---|
| sample_id | str | `<patch_id>_<nnnn>`; unique |
| event_id | str | `IFB-001`..`IFB-010` (see `events.csv`) |
| slug | str | short event name, e.g. `bihar_2020` |
| patch_id | str | raster patch the pixel came from (`data/raster/<event_id>/<patch_id>/`); use as the spatial block |
| patch_kind | str | `flood` (patch flood fraction >= 2%), `dry` (<= 0.1%), `hand` (Sen1Floods11 chip) |
| region_group | str | events whose AOIs intersect share a group; hold out whole groups for spatial generalisation |
| event_state | str | main state of the event, from the catalogue |
| state | str | geoBoundaries ADM1 state at the pixel (ASCII, diacritics removed); differs from `event_state` where an AOI crosses a border (e.g. Punjab/Haryana, Odisha/West Bengal); pixels just off a polygon on water take the nearest state within ~5 km |
| district | str | geoBoundaries ADM2 name at the pixel (gbOpen IND, source lgdirectory.gov.in), same nearest-polygon rule |
| region | str | named sub-region of the event |
| flood_type | str | `riverine`, `riverine_pluvial`, `urban_riverine`, `coastal_cyclonic`, `urban` |
| latitude, longitude | float | WGS84 pixel centre, 6 decimals |

### Dates
| column | type | description |
|---|---|---|
| event_start, event_end | str | approximate event period from reports (see events.csv notes) |
| label_date | str | date of the Sentinel-1 pass that the label describes |
| s1_before_dates | str | `;`-separated dates of the before scenes (same relative orbit and direction as the after pass) |
| s1_after_date | str | date of the after scene (always equals `label_date`) |
| s1_relative_orbit, s1_orbit_state | int, str | geometry shared by before and after scenes |
| s2_before_offset_days | float | days between the S2 observation used and `event_start - 1` (negative = earlier) |
| s2_after_offset_days | float | days between the S2 observation used and `label_date` (window -2..+10 days) |

### Sentinel-1 (dB, sigma0 GRD from Earth Engine, 20 m)
`VV_before, VH_before` = median (linear power) of up to 3 most recent pre-event scenes;
`VV_after, VH_after` = the label pass; `VV_change = VV_after - VV_before` (dB), same for VH.

### Sentinel-2 (reflectance 0-1, 20 m)
`B2, B3, B4, B8, B11, B12` with `_before` / `_after` suffix. Per pixel, the clear observation
closest in time to the reference date. NaN = no clear observation in the window.

Earth Engine has almost no Sentinel-2 surface reflectance (L2A) over India before late 2018, so the
product depends on the window:

| column | values |
|---|---|
| s2_before_product, s2_after_product | `L2A_SR`: surface reflectance (`COPERNICUS/S2_SR_HARMONIZED`), clear = SCL in {4 vegetation, 5 bare, 6 water, 11 snow}. `L1C_TOA`: top-of-atmosphere reflectance (`COPERNICUS/S2_HARMONIZED`), clear = s2cloudless probability < 40% (`COPERNICUS/S2_CLOUD_PROBABILITY`). Windows ending before 2019 use L1C_TOA: IFB-001 (both) and IFB-002 (both) |

TOA reflectance includes the atmosphere, so blue/green bands read higher than surface reflectance
for the same ground; indices are less affected. Compare bands across products with care.

### Indices
| column | formula |
|---|---|
| NDVI_* | (B8 - B4) / (B8 + B4) |
| NDWI_* | (B3 - B8) / (B3 + B8) (McFeeters) |
| MNDWI_* | (B3 - B11) / (B3 + B11) (Xu) |
| NDBI_* | (B11 - B8) / (B11 + B8) |
| *_change | after - before (NDVI, NDWI, MNDWI only; NDBI change is not meaningful over days) |

### Rainfall (CHIRPS v2.0 daily, mm, bilinear from 0.05 deg)
`rainfall_Nd` = sum of daily totals over the N calendar days **[label_date - N, label_date - 1]**.
The acquisition day is excluded because a CHIRPS day is a full-day total while Sentinel-1 passes
over India at ~00:30 or ~12:30 UTC. CHIRPS at 0.05 deg (~5.5 km) is near-constant within a patch.

### Terrain and land cover
| column | description |
|---|---|
| elevation | Copernicus GLO-30 DEM, m, bilinear to 20 m |
| slope | degrees, from the 20 m DEM (central differences; one-sided at patch edges) |
| landcover_class | ESA WorldCover 2021 v200 class (10 tree, 20 shrub, 30 grass, 40 crop, 50 built, 60 bare, 70 snow, 80 water, 90 wetland, 95 mangrove, 100 moss), mode-resampled |
| jrc_occurrence | JRC Global Surface Water occurrence 1984-2021, % |
| permanent_water | 1 if `jrc_occurrence >= 50` |

Aspect is not included: it has no defensible mechanism for floodplain inundation at this scale.

### Label
| column | description |
|---|---|
| flood_label | 1 = observed water that is not JRC permanent water (occurrence >= 50%), 0 = otherwise; unlabelled pixels are never sampled |
| flood_confidence | GFM ensemble likelihood 0-100 for GFM events; NaN for hand labels |
| gfm_flood_extent | GFM's own flood layer (observed water minus GFM monthly reference water), 0/1, NaN if unavailable; not the label, see dataset_card.md |
| label_source | `gfm` or `sen1floods11_hand` (full provenance per event in `events.csv`, `configs/label_sources.json`) |
| sample_stratum | `flood` or `non_flood` |
| inclusion_prob | probability that a lattice pixel of this stratum in this patch was sampled; weight = 1/inclusion_prob |

## Raster: `data/raster/<event_id>/<patch_id>/`

256 x 256 px, 20 m, local UTM zone, DEFLATE-compressed tiled GeoTIFF. All files of a patch share
CRS, transform and size (checked by `scripts/validate_dataset.py`).

| file | bands | dtype / nodata |
|---|---|---|
| s1_before.tif, s1_after.tif | VV, VH (dB) | float32 / NaN |
| s2_before.tif, s2_after.tif | B2, B3, B4, B8, B11, B12 (reflectance x 10000) | uint16 / 0 |
| derived.tif | VV_change, VH_change, NDVI/NDWI/MNDWI/NDBI before+after, s2_before_offset_days, s2_after_offset_days | float32 / NaN |
| rainfall.tif | rainfall_1d, _3d, _7d, _14d | float32 / NaN |
| terrain.tif | elevation_m, slope_deg | float32 / NaN |
| landcover.tif | esa_worldcover_2021_class, jrc_gsw_occurrence_pct | uint8 / 255 |
| labels.tif | flood_label (0/1), flood_confidence (0-100), gfm_flood_extent (0/1) | uint8 / 255 |
| patch.json | grid, scenes used, label pixel counts, per-layer valid fractions, GFM-vs-hand agreement (IFB-001) | |
| samples.parquet | the tabular rows drawn from this patch | |

Band names are stored as GeoTIFF band descriptions.

## Metadata
- `configs/events.json`: event catalogue (input). `configs/dataset_config.json`: all build parameters.
- `data/metadata/<event_id>.json`: scenes used, label statistics, sample counts, sampling rule.
- `data/metadata/splits.json`: leave-one-event-out, leave-one-region-group-out, by-flood-type.
- `state/<event_id>.json`: build stage, history, errors, selected patches (resumability).
