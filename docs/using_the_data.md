# Using the data

A practical guide for someone opening the dataset for the first time. Terms are explained in the
[glossary](glossary.md).

## 1. Get it

```bash
git clone https://github.com/parth-sharma-10/IndiaFloodBench-GEE.git
cd IndiaFloodBench-GEE
pip install pandas pyarrow numpy rasterio          # rasterio only for the image patches
python examples/quickstart.py                      # works with the table alone
```

The table (`data/tabular/`) is in git. The image patches are large, so they are a GitHub Release
asset. Download and unzip them in the repository folder:

```bash
gh release download v2.0 -p 'indiafloodbench_gee_raster_v2.0.zip' && unzip -q indiafloodbench_gee_raster_v2.0.zip
```

(Without the `gh` tool: open the repository's Releases page in a browser and download the zip.)

## 2. Two ways to look at the same data

| | Table | Image patches |
|---|---|---|
| file | `data/tabular/indiafloodbench.parquet` (or `.csv`) | `data/raster/<event_id>/<patch_id>/*.tif` |
| one unit is | one 20 m pixel | one 5.12 km square, 256 x 256 pixels |
| good for | quick analysis, pixel classifiers (random forest, gradient boosting) | image models (U-Net etc.), maps, visual checks |
| open with | pandas, Excel (CSV), any spreadsheet tool | rasterio, GDAL, QGIS |

Every table row says which patch it came from (`patch_id`) and where it is (`latitude`,
`longitude`), so the two views can always be joined.

## 3. What is in each row

About 65 columns, grouped as:

- **who/where/when:** `event_id`, `patch_id`, `state`, `district`, `latitude`, `longitude`,
  `label_date`, the satellite dates used;
- **radar (Sentinel-1):** `VV_before`, `VV_after`, `VV_change`, and the same for `VH`, in dB;
- **optical (Sentinel-2):** six bands before and after, plus the indices NDVI, NDWI, MNDWI, NDBI;
  often empty (NaN) after the flood because of monsoon cloud;
- **rainfall:** `rainfall_1d` ... `rainfall_14d`, mm in the days before the label date;
- **terrain and land:** `elevation`, `slope`, `landcover_class`, `jrc_occurrence`,
  `permanent_water`;
- **the answer:** `flood_label` (1 flood, 0 not flood), `flood_confidence`, `label_source`;
- **sampling:** `sample_stratum`, `inclusion_prob`.

Every column is defined in [`schema.md`](../schema.md).

## 4. Three rules for honest results

**1. Split by event or region, never by row.** Neighbouring pixels of one patch, taken on the same
day, are near-copies. If some go to training and some to testing, the test score is inflated.
`data/metadata/splits.json` lists ready-made splits:

- `leave_one_event_out`: train on 9 events, test on the 10th; repeat for each.
- `leave_one_region_group_out`: the same, but events that overlap on the ground (Assam 2016 and
  Assam 2024) are held out together.
- `by_flood_type`: for questions like "does a model trained on river floods work on cyclones?".

**2. Watch out for circularity.** For 9 of the 10 events the label comes from GFM, which is
computed from the *same* Sentinel-1 image as `VV_after` and `VH_after`. A model that uses those
columns can score well by imitating GFM. Report results:

- tested on IFB-001 (Assam 2016), the only event with human labels, and
- with and without the after-flood radar columns.

**3. Undo the class balancing.** The table is about 40% flood because it was sampled that way. In
reality, 2% to 23% of an event's area was flooded. Weight each row by `1 / inclusion_prob` when you
need real proportions, for example when reporting accuracy or area.

## 5. What the labels can and cannot tell you

- A pixel labelled 1 is usually water (precision about 0.77 on the hand-labelled event).
- Many pixels labelled 0 are actually flooded: GFM misses flooded vegetation and shallow water
  (recall about 0.35 on the hand-labelled event). Expect a model trained on GFM labels to be
  conservative too.
- "Flood" here means water that is not normally there. Deliberately flooded paddy fields and
  seasonal ponds can count as flood, and so can rivers that move around: most of the braided
  Brahmaputra in [`patch_preview.png`](patch_preview.png) (row 1) is labelled flood.
- Cities are barely covered: radar cannot see water between buildings, and GFM skips built-up
  areas.

Full detail and measured numbers: [`dataset_card.md`](../dataset_card.md) and
[`data_quality_report.md`](../data_quality_report.md).

## 6. Opening a patch in QGIS (no code)

1. Install QGIS (free).
2. Drag `labels.tif` and `s1_after.tif` from one patch folder into the QGIS window.
3. In the layer properties, show `labels.tif` band 1 with a palette: 0 white, 1 blue, 255
   transparent.
4. Add a basemap (Web > QuickMapServices, or the XYZ "OpenStreetMap" tile) to see where it is.
