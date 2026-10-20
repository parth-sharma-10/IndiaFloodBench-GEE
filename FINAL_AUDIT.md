# FINAL AUDIT

Every number below was computed by re-reading the output files (`scripts/generate_report.py report`).

- **Number of flood events:** 10 built (10 configured; 0 not built: -)
- **States covered:** Andhra Pradesh, Arunachal Pradesh, Assam, Bihar, Gujarat, Haryana, Kerala, Odisha, Punjab, West Bengal (10 with samples; event catalogue states: Andhra Pradesh, Assam, Bihar, Gujarat, Kerala, Odisha, Punjab, West Bengal)
- **Date range:** 2016-07-24 to 2024-09-10 (labels 2016-08-12 to 2024-09-01)
- **Total samples:** 49,619 (Parquet rows) / 49,619 (CSV rows)
- **Flood samples:** 20,219 (40.7%)
- **Non-flood samples:** 29,400 (59.3%)
- **Number of raster patches:** 196 (256x256 px at 20 m), 1764 GeoTIFFs, 0 unreadable; CRS used: EPSG:32643, EPSG:32644, EPSG:32645, EPSG:32646
- **Total storage:** data/ 1016 MB (raster 875 MB, tabular 46 MB, external inputs 95 MB)
- **Sentinel-1 coverage:** before 99.9%, after 99.9% of samples
- **Sentinel-2 coverage:** before 97.3%, after 51.6% of samples
- **Rainfall coverage:** 100.0% (CHIRPS v2.0 daily, 0.05 deg)
- **DEM coverage:** 100.0% (Copernicus GLO-30)
- **Label sources:** gfm: 31,996 samples; sen1floods11_hand: 17,623 samples

## Missing data

MNDWI_change 50.7%; NDWI_change 50.6%; NDVI_change 50.6%; B11_after 48.5%; NDBI_after 48.5%; MNDWI_after 48.5%; B12_after 48.5%; B8_after 48.4%; NDWI_after 48.4%; NDVI_after 48.4%; B3_after 48.4%; B4_after 48.4%; s2_after_offset_days 48.4%; B2_after 48.4%; flood_confidence 35.5%; gfm_flood_extent 17.9%; B11_before 2.7%; B12_before 2.7%; MNDWI_before 2.7%; NDBI_before 2.7%; B3_before 2.7%; s2_before_offset_days 2.7%; NDVI_before 2.7%; B8_before 2.7%; B4_before 2.7%; NDWI_before 2.7%; B2_before 2.7%; VV_change 0.1%; VH_before 0.1%; VH_change 0.1%; VV_before 0.1%; VH_after 0.1%; VV_after 0.1%

## Duplicate data

sample_id duplicates 0, full-row duplicates 0, (event, lat, lon) duplicates 0.

## Known limitations

- Features come from Google Earth Engine; Earth Engine's Sentinel-1 (`COPERNICUS/S1_GRD`) is sigma0, not terrain-flattened gamma0, so radar values differ from v1.0 (built from Planetary Computer RTC) by a few dB.
- Labels do not come from Earth Engine: GFM is read from EODC and Sen1Floods11 hand labels from its bucket; neither is in the Earth Engine catalogue.
- 9 of 10 configured events use GFM labels, which are automated and derived from the SAME Sentinel-1 acquisition as `VV_after`/`VH_after`. Models using after-SAR features against GFM labels partly learn the GFM algorithm. See dataset_card.md.
- Urban flooding is essentially unlabelled: GFM excludes built-up pixels (Hyderabad 2020 and Mumbai 2024 probes had ~0 GFM flood pixels and were dropped). Vadodara/Vijayawada labels are mostly peri-urban water.
- GFM-based labels are conservative: on IFB-001 they recover 35% of hand-labelled flood (precision 0.76).
- Label = observed non-permanent water; in July paddy and urban fringes part of it is seasonal water, not flood.
- Sentinel-2 after-imagery is mostly cloud-covered for monsoon events.
- Land cover is ESA WorldCover 2021 (static); Dynamic World probabilities are not included.

## Files generated

- `README.md`: 1 file(s) - README.md
- `dataset_card.md`: 1 file(s) - dataset_card.md
- `schema.md`: 1 file(s) - schema.md
- `data_quality_report.md`: 1 file(s) - data_quality_report.md
- `events.csv`: 1 file(s) - events.csv
- `LICENSE`: 1 file(s) - LICENSE
- `configs/*.json`: 3 file(s) - dataset_config.json, events.json, label_sources.json
- `src/ifb/*.py`: 5 file(s) - __init__.py, features.py, labels.py, sources.py, writer.py
- `scripts/*.py`: 4 file(s) - build_dataset.py, generate_report.py, report_md.py, validate_dataset.py
- `state/*.json`: 10 file(s) - IFB-001.json, IFB-002.json, IFB-003.json, IFB-004.json, IFB-005.json, IFB-006.json, IFB-007.json, IFB-008.json, IFB-009.json, IFB-010.json
- `data/metadata/*.json`: 11 file(s) - IFB-001.json, IFB-002.json, IFB-003.json, IFB-004.json, IFB-005.json, IFB-006.json, IFB-007.json, IFB-008.json, IFB-009.json, IFB-010.json, splits.json
- `data/tabular/*.parquet`: 1 file(s) - indiafloodbench.parquet
- `data/tabular/*.csv`: 1 file(s) - indiafloodbench.csv
- `data/tabular/events/*.parquet`: 10 file(s) - IFB-001.parquet, IFB-002.parquet, IFB-003.parquet, IFB-004.parquet, IFB-005.parquet, IFB-006.parquet, IFB-007.parquet, IFB-008.parquet, IFB-009.parquet, IFB-010.parquet
- `reports/*.json`: 1 file(s) - validation.json
- `data/raster/<event>/<patch>/*.tif`: 1764 files in 196 patch folders
