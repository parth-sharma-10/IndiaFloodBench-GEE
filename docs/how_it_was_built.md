# How IndiaFloodBench was built, from scratch

This is a walkthrough of what was built, in the order it was built, and why each choice was made.
It assumes no remote-sensing background. The reference documents are separate:
[`dataset_card.md`](../dataset_card.md) (what the data is), [`schema.md`](../schema.md) (every
column and band), and [`FINAL_AUDIT.md`](../FINAL_AUDIT.md) (the numbers).

## 1. The problem

A flood-mapping model takes satellite images of a place and says, pixel by pixel, "flooded" or
"not flooded". To train or test one you need images of real floods with the right answer drawn on
them (labels).

Public flood datasets have almost no India. Sen1Floods11, the best-known one, has a single Indian
event (Assam, 12 Aug 2016). Indian floods are also unlike the European and US floods most datasets
cover:

- monsoon cloud hides the ground from ordinary cameras for weeks;
- the floodplains are huge and agricultural, and paddy fields are flooded on purpose;
- cyclones push sea water into mangrove coasts.

The goal was a small dataset of several Indian floods in one consistent format, so a model can be
trained on some floods and tested on floods it has never seen. Ten events, 196 patches and 49,619
pixel samples came out of it. It is a modest test bed, not a replacement for global benchmarks.
No model was trained.

## 2. Background you need

**Two kinds of satellite image.**

| | Sentinel-1 (radar, SAR) | Sentinel-2 (optical) |
|---|---|---|
| how it sees | sends microwaves and measures the echo | photographs reflected sunlight in 13 colour bands |
| clouds | sees straight through them | blocked by them |
| water looks | dark: calm water reflects the pulse away, so little echo comes back | distinctive in green vs infrared bands |
| units here | backscatter in decibels (dB), VV and VH polarisations | surface reflectance, 0 to 1 |

Radar is the main tool in a monsoon because it works through cloud. Optical is used where it
happens to be clear.

**Before and after.** A dark radar pixel could be a flood, a lake, a smooth road or a radar
shadow. Comparing the flood-day image with a dry-season image of the same place tells them apart:
a pixel that *became* dark is probably new water. That is why every patch has a `before` and an
`after`.

**Same orbit.** A radar's echo depends on the angle it looks from. Sentinel-1 revisits the same
place from different tracks ("relative orbits", ascending or descending). A fair before/after
comparison uses images from the **same** track, so only the ground changed, not the viewing angle.

**Other layers.** Rainfall (CHIRPS), elevation and slope (Copernicus DEM), land cover (ESA
WorldCover) and how often each pixel has historically been water (JRC Global Surface Water). These
help a model and also let us separate rivers and lakes from flood.

**Permanent water.** A river is water but not a flood. JRC's `occurrence` says what percentage of
1984-2021 Landsat observations saw water at each 30 m pixel. Here, `occurrence >= 50%` means
"permanent water". Flood = water that is *not* permanent water.

**How the data is fetched.** Every input layer (radar, optical, rainfall, elevation, land cover,
permanent water) comes from **Google Earth Engine**, Google's server-side catalogue and compute
engine for satellite data. The pipeline describes each layer as an Earth Engine computation
(filter the collection, mask clouds, composite, resample) and asks Earth Engine to return the
result as an array on exactly the patch's grid (`ee.data.computePixels`). Nothing is downloaded
scene by scene.

The labels are the one exception, because Earth Engine does not host them: the GFM flood product
is read from EODC through STAC (a standard search API for satellite data) as Cloud-Optimised
GeoTIFFs, and the Sen1Floods11 hand labels are local files from their public bucket.

**Grid.** Every layer arrives at a different resolution and map projection. Everything is
resampled onto one grid: 20 m pixels in the local UTM zone (a flat metric projection), cut into
256 x 256 pixel patches (5.12 km square).

## 3. Where the labels come from

There is no money or time to hand-draw ten floods, so labels came from two places:

1. **Sen1Floods11 hand labels** for Assam 2016 (IFB-001). Analysts drew water on 68 chips. This
   is the only human-checked event and the yardstick for everything else.
2. **Copernicus GFM** (Global Flood Monitoring) for the other nine events. GFM runs three
   independent radar water-detection algorithms (DLR, LIST, TU Wien) on every Sentinel-1 pass and
   takes the majority vote. It is automated, so these are "weak" labels.

### The decision that mattered most

GFM publishes a ready-made "flood extent" layer. The first build used it. Checked against the
Assam hand labels on the same radar pass, it found only **2.9%** of the hand-labelled flood
(precision 0.94, recall 0.029).

The reason: GFM defines flood as water minus a *monthly* reference water map. In an Indian
floodplain, the August reference already contains the monsoon inundation, so the flood itself is
subtracted away.

The fix was to build the label from GFM's *observed water* layer minus JRC permanent water, which
is the same definition the hand labels use. On the same 68 chips that scores precision **0.77**,
recall **0.35**, IoU **0.32**. GFM's own layer is still kept as the `gfm_flood_extent` column.

What 0.77 / 0.35 means in practice: when the label says flood it is usually right, but it misses
about two thirds of the flooded pixels a human would mark (flooded vegetation, shallow or
wind-rippled water). Treat GFM labels as conservative. This was measured on one event in one
region; quality elsewhere is unknown.

### Pixels that get no label

Labels use three values: 1 flood, 0 not flood, 255 no label. A pixel gets 255 when:

- GFM's exclusion mask covers it (radar shadow or layover in hills, dense urban, surfaces that are
  always radar-dark);
- it is outside the radar scene; or
- JRC has no data there, which in practice means open sea.

The sea rule was added after a bug. In the first Cyclone Yaas build, open sea counted as "water
that is not permanent water" and was labelled flood: the AOI's flood share read 10.7%. With sea
excluded it is about 2%.

The same bug came back in the Earth Engine build. Earth Engine's copy of JRC does not mark the
sea as "no data" (it marks it as "never water", like dry land), so Yaas again read 10.4%. The
sea mask now comes from the Copernicus DEM's water-body band (ocean = 1), and Yaas reads 2.7%.
Validation now counts flood samples that fall outside every state (`flood_samples_without_state`);
it is 0.

## 4. Choosing the events

Candidate floods came from news and government situation reports. Each was kept only if:

- GFM detected flooding over the area on a Sentinel-1 pass inside the event window; and
- a matching Sentinel-1 radar scene existed to use as the `after` image.

Dropped: Chennai 2023 (no Sentinel-1 pass in the window; Sentinel-1B failed in Dec 2021),
Hyderabad 2020 and Mumbai 2024 (GFM saw almost no flood, because it excludes built-up areas).

Kept, in [`configs/events.json`](../configs/events.json):

| id | event | type | label date |
|---|---|---|---|
| IFB-001 | Assam 2016 | riverine | 2016-08-12 (hand labels) |
| IFB-002 | Kerala 2018 | riverine/pluvial | 2018-08-21 |
| IFB-003 | Bihar 2020 | riverine | 2020-07-29 |
| IFB-004 | Assam 2022 | riverine | 2022-06-16 |
| IFB-005 | Punjab 2023 | riverine | 2023-07-12 |
| IFB-006 | Vijayawada 2024 | urban/riverine | 2024-09-01 |
| IFB-007 | Cyclone Amphan 2020 | coastal | 2020-05-22 |
| IFB-008 | Cyclone Yaas 2021 | coastal | 2021-05-29 |
| IFB-009 | Assam 2024 | riverine | 2024-07-06 |
| IFB-010 | Vadodara 2024 | urban | 2024-08-28 |

Each entry has a bounding box, the event dates, the label date (the GFM pass with the most flood
over the area), and a **before window**: a stretch of dry season before the first flood wave of
that year, so the `before` image really is dry.

## 5. The pipeline, step by step

```mermaid
flowchart LR
  A[events.json] --> B[check config]
  B --> C[find scenes<br/>GFM, S1, S2]
  C --> D[pick patches<br/>on a coarse label]
  D --> E[per patch, in a subprocess:<br/>read every layer onto the 20 m grid]
  E --> F[write 9 GeoTIFFs<br/>+ sample pixels]
  F --> G[validate event]
  G --> H[package: merge tables,<br/>admin regions, splits]
  H --> I[reports]
```

Code: [`scripts/build_dataset.py`](../scripts/build_dataset.py) drives it;
[`src/ifb/`](../src/ifb/) holds the pieces. All tunable numbers are in
[`configs/dataset_config.json`](../configs/dataset_config.json).

### Step 1: check the config (`check_config`)

Required fields exist; dates are in order (before window < event start <= label date); the box
is inside India.

### Step 2: find the imagery (`check_imagery`)

- Search EODC for GFM tiles on the label date and keep one pass.
- In Earth Engine, find the Sentinel-1 scenes (`COPERNICUS/S1_GRD`) taken within an hour of that
  pass. This is the same acquisition GFM analysed. Read its relative orbit and direction.
- List the Sentinel-1 dates in the before window on that **same** orbit and direction.
- Fix the Sentinel-2 after window: 2 days before to 10 days after the label date.

If anything essential is missing, the event fails with a clear message and the run moves on.

### Step 3: pick patches (`select_gfm_patches`, `select_hand_patches`)

Reading everything at 20 m for a whole area would be slow, so patches are chosen first:

1. Read the label for the whole area at a coarse 80 m.
2. Cut it into 5.12 km tiles. Drop tiles with under 60% labelled pixels.
3. Randomly pick up to 10 "flood" tiles (>= 2% flood) and 5 "dry" tiles (<= 0.1% flood), with a
   fixed seed so the build is repeatable.

Dry tiles matter: without them a model never sees what normal ground looks like at full scale.
For Assam 2016 there is instead one patch per hand-labelled chip (68).

### Step 4: build each patch (`patch_layers`)

For each patch, every layer is read onto the same 20 m grid:

| layer | how |
|---|---|
| label | as in section 3; plus GFM likelihood (0-100) and GFM's own flood layer |
| S1 after | the label pass from Earth Engine (dB), converted to linear power and averaged from 10 m to 20 m, then back to dB |
| S1 before | median of the 3 most recent dry-season dates on the same orbit that cover the patch; median taken in linear power, then converted to dB |
| S2 before / after | per pixel, the cloud-free observation closest in date to the reference day (cloud-free per the scene classification layer), from every scene under 80% cloud in the window (`qualityMosaic`). No averaging across dates: averaging over a flood blurs wet and dry. Cloudy pixels stay empty (NaN). The harmonized collections already remove the +1000 offset of newer scenes. Windows before 2019 use L1C top-of-atmosphere reflectance with an s2cloudless cloud mask, because Earth Engine has no surface-reflectance (L2A) Sentinel-2 over India then |
| indices | NDVI (vegetation), NDWI and MNDWI (water), NDBI (built-up), before and after |
| rainfall | CHIRPS daily totals summed over the 1, 3, 7 and 14 days **before** the label day. The label day is excluded because the satellite passes partway through it |
| terrain | elevation (bilinear), slope computed from it |
| land cover | WorldCover class (most common value per 20 m pixel) |
| JRC occurrence | average per 20 m pixel |

### Step 5: write and sample (`write_patch`, `sample_rows`)

Each patch folder gets 9 compressed GeoTIFFs with named bands, plus `patch.json` (scenes used,
label counts, coverage) and `samples.parquet`.

The table is a sample of pixels, not every pixel. On every 4th pixel (an 80 m lattice, so samples
are not near-duplicates of their neighbours), up to 150 flood and 150 non-flood pixels are drawn
at random. This makes the table roughly 40% flood, far more than reality. Each row stores its
`inclusion_prob` so you can reweight back to the true share, which is recorded per event
(`aoi_flood_fraction_of_labelled_px`, 2.6% to 23.4%).

### Step 6: validate (`scripts/validate_dataset.py`)

Per event, as part of the build:

- all 9 rasters of a patch share CRS, transform, size and 20 m resolution, have nodata set and are
  readable;
- every `before` date is inside the before window and before the event; every `after` date equals
  the label date;
- samples lie inside the event box and inside India; both classes are present; labels are only 0
  or 1; no duplicate locations;
- required columns are under 20% missing (radar, terrain, rainfall). Sentinel-2 gaps are only
  warnings, since monsoon cloud is expected.

An event only becomes `complete` if this passes.

### Step 7: package (`scripts/generate_report.py package`)

- Merge the ten event tables into `data/tabular/indiafloodbench.parquet` and `.csv`.
- Look up each pixel's state and district from geoBoundaries. A pixel on a river just outside the
  simplified polygons takes the nearest one within ~5 km. This is why some Punjab samples are in
  Haryana: the area crosses the border.
- Group events whose areas overlap into a `region_group` (Assam 2016 and Assam 2024 share RG01).
- Write `data/metadata/splits.json` with leakage-safe splits (see section 7).
- Write `events.csv`.

### Step 8: report (`scripts/generate_report.py report`)

`data_quality_report.md` and `FINAL_AUDIT.md` are generated by re-reading the output files, so
every number in them is computed, not typed.

## 6. Engineering problems hit along the way

**The network (v1.0 build).** Reads of remote COGs failed with truncated tiles ("got N bytes, expected M").
Retrying inside the same Python process failed identically every time, because GDAL caches each
downloaded block per URL and kept serving the broken one. Some reads also hung forever inside
GDAL's C code, where a Python thread cannot interrupt them. One test patch sat idle for over two
hours before this was understood. Fixes, all in [`src/ifb/sources.py`](../src/ifb/sources.py) and
`build_dataset.py`:

- turn off GDAL's HTTP block cache (`CPL_VSIL_CURL_NON_CACHED`);
- abort transfers slower than 2 KB/s for 30 s, retry on 429/5xx;
- run **each patch in its own child process** with a 20-minute timeout and 3 attempts, 4 patches
  in parallel; `faulthandler` dumps a stack trace into the patch log if it stalls;
- use a direct windowed read instead of a reprojection when the source is already in the patch's
  projection, which is much cheaper over HTTP.

In v2.0 the same link also stalled Earth Engine requests: one worker hung for over 5 minutes
inside `ee.Initialize`. A 120 s socket timeout plus retries turns a stall into an error that is
retried (`features.init`).

**Resumability.** Each event's progress is saved in `state/<event_id>.json`:
`configured -> imagery_checked -> label_checked -> exported -> validated -> complete`. A crash or
restart picks up where it stopped; finished patches are skipped. One failing event is marked
`failed` and does not stop the others.

**Google Earth Engine: v1.0 and v2.0.** The brief asked for Earth Engine. The first build (v1.0,
GitHub release `v1.0`) had no Earth Engine credentials and was built from the same public
archives through Microsoft Planetary Computer instead. That deviation should have been raised
before building, not recorded afterwards. v2.0 is the Earth Engine build: an Earth Engine
Cloud project (`indiafloodbench`, non-commercial Community tier) was registered and every
feature read was moved to Earth Engine. The label definition, the patch selection rule, sampling
and validation did not change.

One real difference: Earth Engine's Sentinel-1 is sigma0, not the terrain-flattened gamma0 RTC
used in v1.0. On a Bihar test patch the radar values were about 1.3 dB lower on average while the
before/after change was almost the same (mean VV change -4.58 dB in v2.0 vs -4.45 dB in v1.0). Do
not mix radar values from the two versions.

A second difference was found during the v2.0 build: four Kerala patches got no Sentinel-2 at all.
Earth Engine's surface-reflectance collection has almost nothing over India before late 2018
(Kerala, Aug 2018: 0 scenes, against 10 in the top-of-atmosphere L1C collection; Assam,
Apr-Jun 2016: 0 against 68). Rather than leave the only hand-labelled event without optical data,
windows ending before 2019 use L1C with Earth Engine's s2cloudless cloud probability as the mask.
Each row records which product it used (`s2_before_product`, `s2_after_product`). Patch selection also shifted slightly, because the
JRC permanent-water layer from Earth Engine differs a little from the Planetary Computer copy.

## 7. Traps when using the data

**Leakage.** Pixels from the same patch are neighbours on the same day; pixels from the same
event share a river and a date. A random row split puts near-copies in train and test and makes a
model look better than it is. Split by whole events (`leave_one_event_out`) or whole
`region_group`s. Never split one `patch_id` across train and test.

**Circularity.** GFM labels were computed from the same radar image as `VV_after` / `VH_after`. A
model given those features against GFM labels partly learns to copy GFM's algorithm. Honest tests:
train on GFM events and test on Assam 2016 (hand labels), and/or report results with and without
the after-radar features. Optical, rainfall, terrain and land cover are independent of the label.

**Balanced classes.** The table is ~40% flood by design. Reweight with `inclusion_prob` for real
prevalence.

## 8. What came out

From [`FINAL_AUDIT.md`](../FINAL_AUDIT.md):

| | |
|---|---|
| events | 10, all `complete`, labels 2016-08-12 to 2024-09-01 |
| raster patches | 196 (1,764 GeoTIFFs, 0 unreadable), 875 MB, in the v2.0 GitHub Release |
| table rows | 49,619 (20,219 flood, 29,400 non-flood) |
| radar coverage | 99.9% before and after |
| optical coverage | 97.3% before, **51.6% after** (monsoon cloud) |
| rainfall coverage | 100% (no samples at sea, where CHIRPS has no data) |
| label sources | GFM 31,996 rows; hand labels 17,623 rows |

## 9. Known weaknesses

- 9 of 10 events use automated labels, checked against humans on only one event.
- Urban flooding is barely covered: radar cannot see water between buildings and GFM excludes them.
- "Not permanent water" is not always flood: deliberately flooded paddy (Punjab in July, Bihar) and
  seasonal ponds count too.
- Rivers narrower than 30 m, or that moved since the JRC record, are labelled flood.
- Each event is one radar pass, not the whole flood. Kerala's pass (21 Aug) is after the
  15-17 Aug peak.
- Land cover is from 2021, after the 2016 and 2018 floods.

## 10. Open items

- Find an independent label check outside Assam 2016.
- Cover urban floods with a source that can see them.
