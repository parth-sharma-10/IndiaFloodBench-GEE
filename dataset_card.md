# Dataset card: IndiaFloodBench

> A multi-event, multi-sensor Indian flood dataset for flood mapping and change detection,
> built so that cross-event and cross-region generalisation can be tested honestly.

Numbers in this card come from `FINAL_AUDIT.md` and `data_quality_report.md`, which are generated
from the files; if they disagree, the generated files are right.

## Motivation

Public flood-segmentation benchmarks contain very little India. Sen1Floods11 has one Indian event
(Assam, 12 Aug 2016). Most other benchmarks are built from Copernicus EMS activations, which rarely
cover India. Indian floods differ from the European and US cases that dominate those sets:
monsoon cloud cover blocks optical sensors for weeks, the floodplains are huge and agricultural,
paddy is seasonally flooded, and cyclone surges hit mangrove coasts. IndiaFloodBench collects
several Indian events in one consistent format so a model can be trained on some events and tested
on events it has never seen.

It is a modest dataset: about ten events, a few hundred 5 km patches, and labels that are mostly
automated. It is useful as an Indian test bed, not as a replacement for global benchmarks.

## Related datasets (what exists, and the gap)

| dataset | sensors | labels | India coverage |
|---|---|---|---|
| Sen1Floods11 (Bonafilia et al., CVPR-W 2020) | S1, S2 | 446 hand-labelled chips + weak S1/S2 labels, 11 events | 1 event (Assam 2016); reused here as IFB-001 |
| WorldFloods (Mateo-Garcia et al., Sci. Rep. 2021) | S2 | CEMS / UNOSAT / other mapped flood extents | few or none |
| Kuro Siwo (Bountos et al., 2023) | S1 multi-temporal | manual annotation of CEMS events | mostly Europe |
| MMFlood (Montello et al., 2022) | S1 + DEM + hydrography | CEMS delineations | limited |
| Global Flood Database (Tellman et al., Nature 2021) | MODIS 250 m | automated, 913 events 2000-2018 | many events, but 250 m and pre-2019 |
| FloodNet (Rahnemoonfar et al., 2021) | UAV RGB | manual, Hurricane Harvey (USA) | none; different modality |
| Copernicus GFM (2021-, archive from 2015) | S1 | automated ensemble of 3 algorithms | global, every S1 pass; used here |
| NRSC / Bhuvan flood maps | various | analyst products | authoritative, but not openly downloadable as rasters here, so not used |

## What it contains

- **Events:** see `events.csv` (10 configured; built ones are listed in `FINAL_AUDIT.md`). Types:
  riverine (Assam x3, Bihar, Punjab), riverine/pluvial (Kerala), coastal-cyclonic (Amphan, Yaas),
  urban-riverine (Vijayawada) and urban (Vadodara).
- **Rasters:** 256 x 256 patches at 20 m in local UTM. Each patch holds S1 before/after (VV, VH),
  S2 before/after (6 bands), indices, rainfall, terrain, land cover, permanent water and labels.
- **Table:** stratified pixel samples from those patches with every feature, the label, coordinates,
  admin regions and sampling probabilities.

## Data sources

| layer | source | access |
|---|---|---|
| Sentinel-1 | ESA Sentinel-1 GRD, sigma0 dB (`COPERNICUS/S1_GRD`) | Google Earth Engine |
| Sentinel-2 | L2A surface reflectance (`COPERNICUS/S2_SR_HARMONIZED`, SCL cloud mask); for windows before 2019, where Earth Engine has no L2A over India, L1C top-of-atmosphere (`COPERNICUS/S2_HARMONIZED`, s2cloudless mask) | Google Earth Engine |
| Rainfall | CHIRPS v2.0 daily, 0.05 deg (`UCSB-CHG/CHIRPS/DAILY`) | Google Earth Engine |
| DEM | Copernicus GLO-30 (`COPERNICUS/DEM/GLO30_2024_1`) | Google Earth Engine |
| Land cover | ESA WorldCover 2021 v200 (`ESA/WorldCover/v200`) | Google Earth Engine |
| Permanent water | JRC Global Surface Water v1.4 occurrence (`JRC/GSW1_4/GlobalSurfaceWater`) | Google Earth Engine |
| Flood labels | Copernicus GFM ensemble flood extent; Sen1Floods11 hand labels | EODC STAC; Sen1Floods11 GCS bucket |
| Admin regions | geoBoundaries gbOpen IND ADM1/ADM2 | GitHub release |

**Earth Engine.** Every feature layer is computed in Google Earth Engine and fetched with
`ee.data.computePixels` directly onto each patch's 20 m UTM grid (`src/ifb/features.py`). The
labels are the exception: GFM and Sen1Floods11 are not in the Earth Engine catalogue, so they are
read from their own archives. Using them inside Earth Engine would only mean uploading the same
files as assets.

**v1.0 vs v2.0.** v1.0 (GitHub release `v1.0`) was built from Microsoft Planetary Computer because
Earth Engine credentials were not available at the time. v2.0 is built from Earth Engine. Their
Sentinel-1 products differ: v1.0 used terrain-flattened gamma0 RTC, v2.0 uses Earth Engine's sigma0
GRD, which reads 2-3 dB lower on these floodplains. Do not mix radar values from the two versions.

## Methodology

1. **Event selection.** Candidates came from news and situation reports. Each was kept only if GFM
   showed flooding over the AOI on a pass within the event window and a matching Sentinel-1 pass
   existed. Chennai 2023 failed this check: no Sentinel-1 pass in the window, because Sentinel-1B
   failed in Dec 2021. Hyderabad 2020 and Mumbai 2024 had about 0 GFM flood pixels and were dropped.
2. **Label pass.** The GFM pass with the most flood pixels over the AOI. That date is `label_date`.
3. **Sentinel-1.** The after image is that same pass. The before image is the median of the 3 most
   recent scenes on the **same relative orbit and direction** in a pre-monsoon window. That window is
   chosen before the season's first flood wave, so the reference is dry. Earth Engine's 10 m GRD (dB)
   is converted to linear power, averaged to 20 m (2 x 2 multilook), median-composited, and stored
   in dB.
4. **Sentinel-2.** Per pixel, the closest clear observation to the reference date, using every
   scene under 80% cloud in the window (Earth Engine `qualityMosaic`). No compositing across the
   flood: a median over a flood would blur flooded and dry states. Gaps stay NaN. Assam 2016 and
   Kerala 2018 use L1C top-of-atmosphere reflectance, because Earth Engine has no L2A for India
   then (columns `s2_before_product`, `s2_after_product`).
5. **Grid and patches.** The AOI is tiled into 5.12 km patches. Patch selection uses a coarse (80 m)
   read of the label: patches with >= 60% labelled pixels, then up to 10 random "flood" patches
   (>= 2% flood) and 5 random "dry" patches (<= 0.1% flood), with a fixed seed. For IFB-001 there is
   one patch per Sen1Floods11 chip.
6. **Sampling.** On a 4-pixel (80 m) lattice in each patch, up to 150 flood and 150 non-flood pixels
   are drawn at random. This is deliberately balanced. The true AOI flood share is recorded per event
   (`aoi_flood_fraction_of_labelled_px`) and each row carries `inclusion_prob` for reweighting.

## Label methodology (read this before training anything)

- **One label definition for all events:** `flood_label = 1` where the pixel is **observed water
  that is not permanent water**. Permanent water means JRC Global Surface Water occurrence
  (1984-2021) >= 50%; it comes from Landsat and is independent of Sentinel-1.
- **GFM events (IFB-002..010).** Observed water is the GFM ensemble water extent: a majority vote of
  three independently developed Sentinel-1 algorithms (DLR, LIST, TU Wien). Pixels in the GFM
  exclusion mask (radar shadow/layover, dense urban, permanently low backscatter) are **not
  labelled** and never sampled. `flood_confidence` is the GFM ensemble likelihood. These are
  **automated (weak) labels** and have not been manually verified.
- **Why not GFM's own flood layer.** GFM defines flood as observed water minus a *monthly* reference
  water mask (2017-2021). In Indian monsoon floodplains that August reference already contains the
  seasonal inundation, so the GFM flood layer recovered only **2.9%** of the hand-labelled flood on
  IFB-001 (precision 0.94). GFM's observed water minus JRC permanent water matches the hand-label
  definition, so that is the label. The GFM flood layer is kept as `gfm_flood_extent` for anyone who
  wants the "unusual for this month" definition. This decision was made after the first (v1.0) build.
- **Measured quality (IFB-001, 68 chips, same Sentinel-1 pass).** The GFM-based label scores
  precision **0.77**, recall **0.35**, IoU **0.32** against the hand labels (GFM's own flood layer:
  precision 0.94, recall 0.029). GFM-based labels are therefore **conservative**: a pixel labelled
  1 is usually water, but many pixels labelled 0 are hand-labelled flood (flooded vegetation,
  shallow or wind-roughened water). Expect false negatives in the GFM events. This is one event in
  one region; quality elsewhere is unmeasured.
- **Circularity warning.** GFM is computed from the same Sentinel-1 acquisition as `VV_after` /
  `VH_after` (and therefore `*_change`). A model given after-SAR features is partly learning to
  reproduce GFM. To test this honestly:
  (a) train on GFM events and evaluate on IFB-001 (hand labels); and/or
  (b) report results without after-SAR features (optical + rainfall + terrain), and with them.
  Sentinel-2, rainfall, DEM and land cover are independent of the GFM label.
- **IFB-001 (Assam 2016).** Sen1Floods11 hand labels: analysts drew water on Sentinel-1 and
  Sentinel-2 of 12 Aug 2016. Here flood = hand water outside JRC permanent water
  (occurrence >= 50%). The labels are resampled 10 m -> 20 m (nearest). This is the only manually
  labelled event and the dataset's check on GFM. `data_quality_report.md` reports GFM-vs-hand
  agreement on the same pass.

## Limitations

- Mostly automated labels; see above. GFM's known failure modes apply: flooded vegetation, wind-
  roughened water, and the urban areas it excludes.
- **Urban flooding is not really covered.** SAR cannot see water between buildings and GFM excludes
  built-up pixels. Vadodara and Vijayawada contribute mainly peri-urban and riverine pixels.
- **Rainfall resolution:** CHIRPS is 0.05 deg (~5.5 km), so rainfall is nearly constant within a
  patch. It has no data over open sea; no samples lie there.
- **Monsoon clouds:** Sentinel-2 after-imagery is missing for many samples (see coverage table).
- **Single date per event:** each event is one Sentinel-1 pass, not the full flood progression. For
  Kerala the first GFM-detected pass (21 Aug) is after the 15-17 Aug peak.
- **Paddy:** Punjab (July) and Bihar labels may include deliberately flooded paddy, which SAR cannot
  distinguish from flood water on a single date.
- **Land cover is static** (WorldCover 2021) and post-dates the 2016 and 2018 events. Dynamic World
  probabilities were not included.
- **Overlap:** IFB-001 and IFB-009 overlap spatially (same `region_group`); random splits across
  them leak geography.
- **Admin boundaries** come from geoBoundaries and may not match official Survey of India boundaries
  in disputed areas.
- **Narrow or migrating rivers:** channels narrower than JRC's 30 m resolution, or that have shifted
  since the JRC record, are not 'permanent water', so their water is labelled flood. This includes
  braided main channels: in `docs/patch_preview.png`, row 1, most of the Brahmaputra is labelled
  flood because JRC saw water there only 24-46% of the time (below the 50% cut-off). The hand
  labels have the same issue, since they are converted with the same rule.
- **Open sea is never labelled.** Pixels that the Copernicus DEM water-body mask calls ocean get
  no label, so no sample lies at sea (`flood_samples_without_state` = 0 in `reports/validation.json`).
  Coastal lagoons, estuaries and creeks are not ocean and are labelled.
- **Sentinel-2 L1C for 2016 and 2018.** Assam 2016 and Kerala 2018 use top-of-atmosphere
  reflectance with an s2cloudless mask that has no cloud-shadow or haze step. Their optical
  coverage is higher than with the L2A mask (Assam 2016 after-flood: 94% of samples, v1.0: 59%),
  and some haze passes: 4.9% of Kerala after-flood samples have blue reflectance > 0.2, against
  0.1% in v1.0.
- **Seasonal water is not always flood:** the label is observed water that is not permanent water. In
  urban/peri-urban AOIs (Vadodara, Vijayawada) and in July paddy, part of it is seasonal ponds, reservoirs
  or flooded fields rather than flood inundation.
- **Sampling** is balanced by design, so class frequencies are not natural prevalence.

## Intended future ML use

- Segmentation on patches: `labels.tif` with any subset of the other rasters.
- Pixel classification on the table, with **group splits** by `event_id` or `region_group`
  (`data/metadata/splits.json`); `patch_id` as the minimum spatial block.
- Generalisation studies: riverine -> coastal, Assam -> elsewhere, GFM-trained -> hand-labelled.
- **Not intended** for operational flood alerts or damage estimation.

## Licences of inputs

Copernicus Sentinel data (free, full and open), CHIRPS (CC0-like, cite Funk et al. 2015),
Copernicus DEM GLO-30 (Copernicus licence), ESA WorldCover (CC BY 4.0), JRC GSW (Copernicus/EC
reuse, cite Pekel et al. 2016), Sen1Floods11 (CC BY 4.0; cite Bonafilia et al. 2020), GFM
(Copernicus Emergency Management Service, free use with attribution), geoBoundaries (CC BY 4.0).
