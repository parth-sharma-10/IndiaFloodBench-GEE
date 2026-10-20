# Glossary

Plain-language definitions of every technical term used in this repository, in alphabetical order.

**AOI (area of interest).** The rectangle drawn around one flood event. Patches are cut from it.
Stored as `bbox` (west, south, east, north in degrees) in `configs/events.json`.

**Ascending / descending.** Sentinel-1 passes over a place either flying north (ascending) or
south (descending). The two look at the ground from opposite sides, so before and after images
must use the same direction.

**Backscatter.** The fraction of a radar pulse that bounces back to the satellite. Rough surfaces
(crops, buildings) send a lot back; calm water sends almost nothing back, so it looks dark.

**Band.** One layer of a raster image, for example the red light layer of an optical image or the
VV layer of a radar image. A GeoTIFF file can hold several bands.

**Bounding box (bbox).** See AOI.

**CHIRPS.** A daily rainfall dataset (Climate Hazards Group InfraRed Precipitation with Station
data) at about 5.5 km resolution, made by UC Santa Barbara from satellite and rain-gauge data.

**Circularity.** When a label was made from the same data a model is given as input, the model can
score well by copying the labelling method rather than learning about floods. See
[using_the_data.md](using_the_data.md).

**COG (Cloud-Optimised GeoTIFF).** A GeoTIFF arranged so a program can download one small window
over the internet without fetching the whole file. GFM labels are read this way.

**CRS (coordinate reference system).** The rule that turns a pixel position into a place on Earth.
This dataset uses UTM zones (EPSG:32643 to 32646), which measure distance in metres.

**dB (decibel).** A logarithmic unit. Radar backscatter is stored as `10 x log10(power)`, so -20 dB
is 100 times weaker than 0 dB. Water is typically below about -18 dB in VV.

**DEM (digital elevation model).** A raster of ground height. Here: Copernicus GLO-30, in metres.

**Earth Engine (Google Earth Engine, GEE).** Google's platform that hosts petabytes of satellite
data and runs computations on it on Google's servers. This dataset's features are computed there.

**EODC.** Earth Observation Data Centre (Vienna), which hosts the Copernicus GFM archive.

**Event.** One flood (for example Bihar, July 2020). Each has an ID `IFB-001` to `IFB-010`.

**Feature.** Any input value a model can use: radar, optical bands, indices, rainfall, elevation,
land cover. The label is not a feature.

**GeoTIFF.** An image file that also stores where on Earth each pixel is. Opened with `rasterio`,
QGIS or GDAL.

**GFM (Global Flood Monitoring).** A Copernicus service that automatically maps water and floods on
every Sentinel-1 image worldwide, using three independent algorithms and a majority vote. Source of
9 of the 10 events' labels.

**gamma0 / sigma0.** Two ways of normalising radar backscatter. sigma0 (used here, from Earth
Engine) is per unit of ground area; gamma0 also corrects for terrain slope. On flat floodplains
they differ by a roughly constant 1-3 dB.

**Ground truth / label.** The answer a model is trained to predict: `flood_label` = 1 (flood) or 0
(not flood). 255 in rasters means "no label".

**Hand label.** A label drawn by a human analyst. Only event IFB-001 (Sen1Floods11) has them.

**Inclusion probability.** The chance that a pixel was picked when the table was sampled. Use
`1 / inclusion_prob` as a weight to undo the deliberate class balancing.

**IoU (intersection over union).** Overlap between two maps: pixels both call flood, divided by
pixels either calls flood. 1 is perfect agreement.

**JRC Global Surface Water.** A map of how often each 30 m pixel was water between 1984 and 2021,
from Landsat. `occurrence >= 50%` is treated as permanent water (rivers, lakes).

**Leakage.** When test data is too similar to training data (for example neighbouring pixels of
the same patch), so a model's score is better than it would be on genuinely new floods.

**Multilook.** Averaging neighbouring radar pixels (here 2 x 2, 10 m to 20 m) to reduce speckle.

**NaN.** "Not a number": the value is missing, for example optical data under cloud.

**NDVI, NDWI, MNDWI, NDBI.** Simple ratios of optical bands that highlight vegetation (NDVI),
water (NDWI, MNDWI) and built-up land (NDBI). Formulas in [`schema.md`](../schema.md).

**Optical imagery.** Images of reflected sunlight, like a camera but with more colour bands
(Sentinel-2). Blocked by clouds.

**Patch.** A 256 x 256 pixel square (5.12 km at 20 m) cut from an event's AOI. The unit the rasters
are stored in, and the smallest unit to keep together when splitting data.

**Permanent water.** See JRC Global Surface Water.

**Precision / recall.** For flood labels: precision = of the pixels marked flood, the share that
really are flood; recall = of the real flood pixels, the share that were marked.

**Raster.** A grid of pixels with values, like an image. The opposite of a table.

**Region group.** Events whose AOIs overlap (for example Assam 2016 and Assam 2024) share a
`region_group`, so a held-out region never shares ground with training data.

**Relative orbit.** Sentinel-1 flies a fixed set of numbered tracks. Images from the same relative
orbit see the ground from the same angle.

**SAR (synthetic aperture radar).** A radar imaging technique. Sentinel-1 is a SAR satellite. It
works day and night and through cloud.

**SCL (scene classification layer).** A per-pixel class (cloud, shadow, water, vegetation...)
supplied with Sentinel-2 Level-2A images, used here to drop cloudy pixels.

**Sen1Floods11.** A public flood dataset (Bonafilia et al., 2020) with hand-labelled chips for
11 floods, one of which is Assam 2016 (IFB-001 here).

**Sentinel-1 / Sentinel-2.** European Space Agency satellites. Sentinel-1 carries radar (VV, VH
bands); Sentinel-2 carries an optical camera with 13 bands (6 are used here).

**STAC.** A standard search interface for satellite data catalogues. Used here only to find GFM
label files at EODC.

**Stratified sampling.** Picking a fixed number from each class (here up to 150 flood and 150
non-flood pixels per patch) instead of picking at random from all pixels.

**UTM.** Universal Transverse Mercator: a set of flat map projections, each 6 degrees of longitude
wide, measured in metres.

**VV / VH.** Radar polarisations. VV: sent and received vertically. VH: sent vertically, received
horizontally. Water is dark in both; VH is more sensitive to vegetation.

**WorldCover.** ESA's global 10 m land-cover map for 2021 (tree, crop, built-up, water...).
