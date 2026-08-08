# Nautikos data architecture v2

Nautikos v2 treats the satellite image and the analytical product as two
different datasets. The RGB image is never recoloured to imitate a result. An
analytical layer is drawn above the RGB image only where the underlying
algorithm has valid observations.

## Fixed comparison contract

Every annual comparison uses the same spatial grid, projection, resolution and
season:

- years: 2020, 2021, 2022, 2023, 2024, 2025 and 2026;
- source: Copernicus Sentinel-2 Level-2A COG archive (Earth Search/AWS);
- period: July (1 July through 31 July) of every year;
- observations: the three least-cloudy scenes per MGRS grid;
- native bands: B02, B03, B04 and B08 at 10 m;
- display projection: EPSG:3857;
- analysis projection: equal-area EPSG:6933;
- extent: the Caspian Sea, its coastline buffer and the lower reaches of its
  tributaries;
- 2026 uses actual July 2026 observations, not an alias of 2025 and not a
  forecast;
- RGB is rendered from raw B04/B03/B02 reflectance with one fixed display
  transform. Per-scene TCI images are not mixed because their independent
  stretches create visible coloured strips.

The UI must reject a comparison if either side does not have a completed
manifest with a different `scene_set_id`. Both MapLibre maps use the same
camera. Moving the swipe divider never changes a year, scene, source or zoom.

## Products

| Product | Observation | Resolution | Valid mask | Purpose |
| --- | --- | ---: | --- | --- |
| `rgb` | Sentinel-2 L2A July B04/B03/B02 | 10 m | SCL valid-pixel mask | Stable photographic base |
| `water_extent` | Sentinel-2 L2A July NDWI | 10 m | SCL valid optical pixels | Shoreline and exposed seabed |
| `vegetation` | Sentinel-2 L2A July NDVI | 10 m | land and coastal buffer | Vegetation loss/recovery |
| `water_colour` | Sentinel-3 OLCI L2 WFR | 300 m | inland-water pixels and quality flags | Large water masses |
| `chlorophyll` | Sentinel-3 OLCI L2 WFR CHL | 300 m | water + quality flags | Bloom screening |
| `suspended_matter` | Sentinel-3 OLCI L2 WFR TSM | 300 m | water + quality flags | River and discharge plumes |
| `oil_candidates` | Sentinel-1 GRD/RTC | 10-20 m | water + wind/AIS checks | Dark-spot candidates, not proof of oil |
| `rivers` | HydroRIVERS plus annual NDWI | vector/10 m | Caspian catchment | Tributaries and water-presence change |
| `terrain_runoff` | Copernicus DEM GLO-30 | 30 m | coastal catchment | Runoff and erosion routing |

Water products are transparent outside water. Land products are transparent
outside land or the configured coastal buffer. No product may fill the full
rectangular image footprint merely because a source scene intersects it.

## Server layout

The authoritative data lives on the Jupyter server, not in Vercel and not in
the Git repository.

The fixed catalogue contains 3124 real Sentinel-2 observations for 2020–2026
(444–447 scenes per year). Only B02, B03, B04, B08 and SCL are localized. The
expected raw footprint is 1.1–1.8 TiB depending on COG compression. Derived
COGs and the precomputed tile cache are budgeted separately; keep at least
2 TiB available while the initial build is running.

```text
/home/jovyan/work/caspiansea/data-v2/
  raw/{source}/{year}/{product}/...
  catalog/{source}/{year}.json
  vrt/{product}/{year}.vrt
  cog/{product}/{year}.tif
  tiles-v4/{product}/{year}/{z}/{x}/{y}.png
  vectors/rivers.pmtiles
  manifests/{product}/{year}.json
  exports/{job_id}.png
```

Vercel serves the React interface. The public data origin serves immutable
tiles and the small analysis API:

```text
GET  /v2/manifest
GET  /v2/tiles/{product}/{year}/{z}/{x}/{y}.webp
POST /v2/aoi/statistics
POST /v2/aoi/export
POST /v2/aoi/solutions
GET  /v2/exports/{job_id}.png
```

All historical filters are precomputed. RGB and analytical tiles share the
same Web Mercator bounds and pixel grid, so changing a product cannot shift or
resize it. Normal users never call Copernicus or Earth Search; Vercel requests
immutable tiles from the Jupyter data origin. On-demand rendering is only a
fallback for an uncached detailed tile and the result is cached permanently.

## Provenance and quality gates

Every output has a JSON manifest with source item IDs, acquisition interval,
processing version, checksums, nodata share, valid-pixel share, grid, colour
settings and build timestamp. A year is published only when:

1. all expected source items are downloaded and checksum-verified;
2. the output intersects the Caspian mask and has an acceptable valid-pixel
   share;
3. neighbouring MGRS tiles pass a seam test;
4. the RGB and each analytical raster have the same bounds and pixel grid;
5. 2020 and 2026 resolve to different source item sets;
6. a visual regression screenshot passes at overview and detailed zoom.

## Monitoring and Solutions are separate workspaces

Monitoring contains observations: RGB, water, coast and river layers, years,
swipe comparison and inspection tools.

Solutions contains no monitoring filter list. It starts with an AOI and a
problem type, then produces measurable interventions such as a riparian buffer,
wetland restoration zone, discharge inspection route, erosion-control strip or
SAR re-flight priority. Proposed geometry is stored separately from observed
geometry and is visibly labelled as a scenario.
