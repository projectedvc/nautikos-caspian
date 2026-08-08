# Nautikos data architecture v2

Nautikos v2 treats the satellite image and the analytical product as two
different datasets. The RGB image is never recoloured to imitate a result. An
analytical layer is drawn above the RGB image only where the underlying
algorithm has valid observations.

## Fixed comparison contract

Every annual comparison uses the same spatial grid, projection, resolution and
season:

- years: 2020, 2021, 2022, 2023, 2024, 2025 and 2026;
- source: Copernicus Sentinel-2 Level-3 Quarterly Mosaic;
- period: Q1 (1 January through 31 March) of every year;
- native bands: B02, B03, B04 and B08 at 10 m;
- display projection: EPSG:3857;
- analysis projection: equal-area EPSG:6933;
- extent: the Caspian Sea, its coastline buffer and the lower reaches of its
  tributaries;
- 2026 is Q1 2026, not an alias of 2025 and not a forecast. Q1 is used because
  the official Q2 2026 L3 mosaic is not yet published in the CDSE catalogue.

The UI must reject a comparison if either side does not have a completed
manifest with a different `scene_set_id`. Both MapLibre maps use the same
camera. Moving the swipe divider never changes a year, scene, source or zoom.

## Products

| Product | Observation | Resolution | Valid mask | Purpose |
| --- | --- | ---: | --- | --- |
| `rgb` | Sentinel-2 L3 Q1 B04/B03/B02 | 10 m | L3 data mask | Stable photographic base |
| `water_extent` | Sentinel-2 L3 Q1 NDWI | 10 m | valid optical pixels | Shoreline and exposed seabed |
| `vegetation` | Sentinel-2 L3 Q1 NDVI | 10 m | land and coastal buffer | Vegetation loss/recovery |
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

The public CDSE STAC catalogue resolves the Caspian extent to 149 MGRS tiles
per year. B02, B03, B04, B08 and the observation mask require 671.08 GiB in
total for 2020–2026 (Q1). Derived COGs, tile pyramids and validated water
products are budgeted separately; the deployment must reserve at least 1 TiB.

```text
/home/jovyan/work/caspiansea/data-v2/
  raw/{source}/{year}/{product}/...
  catalog/{source}/{year}.json
  vrt/{product}/{year}.vrt
  cog/{product}/{year}.tif
  tiles/{product}/{year}/{z}/{x}/{y}.webp
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
