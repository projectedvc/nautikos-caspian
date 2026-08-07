# Nautikos local Caspian data

Data root: `D:\CaspianTwinData\cube` (override with `NAUTIKOS_DATA_DIR`).

The app always checks this directory first. Only a missing product may fall
back to a remote provider. Files are immutable and keyed by product, year and
XYZ coordinate, so panning and zooming never change the acquisition date.

Layout:

```text
cube/
  manifest.json
  overviews/annual/{year}/{layer}.webp
  overviews/monthly/{year}/{month}.webp
  tiles/{layer}/{year}/{z}/{x}/{y}.webp
```

Build the annual offline products for the Caspian bbox only:

```powershell
python scripts/build_local_caspian_cube.py --years all
```

The scientific source is Sentinel-2 L2A public COG data. The `manifest.json`
records every scene ID and date used. Water/soil colors are derived products,
not chemical diagnoses. OLCI, Sentinel-1 and temperature layers are stored as
separate products and must not be fabricated from Sentinel-2 RGB.

Storage plan for the available 232.6 GB on D:

- annual whole-Caspian previews and derived layers: below 1 GB;
- monthly 2020-2026 slideshow previews: about 2-5 GB;
- annual 10/20 m Sentinel-2 coastal corridor, source COGs + pyramids: about 80-130 GB;
- Sentinel-1 oil-candidate and Sentinel-3 water products: about 15-35 GB;
- reserve for indexes, manifests and regeneration: at least 40 GB.

Raw global archives are deliberately excluded.
