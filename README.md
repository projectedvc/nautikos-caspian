# Nautikos — Caspian environmental intelligence

Nautikos is a working geospatial MVP for investigating environmental change in the Caspian Sea and its coastal corridor. It compares fixed annual observations from 2020–2026, runs water and coastal-land filters, plays a monthly archive as a timelapse, and analyses a user-selected area with a vision-capable AI assistant.

The product is designed for a live jury demo: the historical Caspian dataset is stored on the application server, so map navigation and filter switching do not depend on a new Copernicus request every time.

## What the MVP does

- one-year view or synchronized before/after swipe comparison;
- independent year selection with enforced chronological order;
- fixed imagery per year: zooming never silently switches to another acquisition;
- local low-resolution regional satellite backdrop plus detailed Caspian raster pyramid;
- water workspace: true colour, Sentinel-3 water view, water extent, oil-slick candidates, chlorophyll, suspended matter, water temperature and shoreline;
- coast workspace: shoreline, vegetation, moisture, soil stress and erosion risk;
- AOI tools: visible selection, area estimate, water share estimate, GeoJSON export and optional AI analysis;
- monthly 2020–2026 timelapse and a visual 2027 trend overlay;
- light/dark themes and collapsible inspector.

## Scientific limits

Every thematic layer is a screening signal, not a laboratory diagnosis. Sentinel-2 provides 10/20 m native observations, Sentinel-3 OLCI about 300 m and temperature products about 1 km. “Oil slick” means a Sentinel-1 SAR roughness anomaly candidate which still needs cross-checking against wind, ships and field observations. Nautikos does not invent extra spatial detail when zooming beyond the source resolution.

## Data architecture

Runtime checks the local Caspian data directory first:

```text
cube/
  manifest.json
  overviews/annual/{year}/{layer}.webp
  overviews/monthly/{year}/{month}.webp
  metrics/{year}.png
  tiles/basemap/{z}/{x}/{y}.jpg
  tiles/{layer}/{year}/{z}/{x}/{y}.webp
```

The local dataset contains only the Caspian and its surroundings, not a global archive. Scene identifiers and dates are recorded in the manifest for reproducibility.

## Local setup

Requirements: Node.js 22.13+ and the prepared data cube.

```bash
git clone https://github.com/projectedvc/nautikos-caspian.git
cd nautikos-caspian
npm ci
cp .env.example .env.local
npm run build
HOST=0.0.0.0 PORT=8765 npm run start:server
```

Windows:

```powershell
npm.cmd ci
npm.cmd run build
npm.cmd run start:windows
```

Environment variables:

```env
NAUTIKOS_DATA_DIR=/absolute/path/to/cube
CASPIAN_CACHE_DIR=/absolute/path/to/cache
GROQ_API_KEY=server-side-key
CDSE_CLIENT_ID=optional-fallback-client-id
CDSE_CLIENT_SECRET=optional-fallback-secret
NAUTIKOS_CORS_ORIGIN=*
```

Secrets are server-only and `.env.local` is excluded from Git.

## Verification

```bash
npm run build
npm run build:vercel
curl http://127.0.0.1:8765/health
```

Expected health response:

```json
{"status":"ok","service":"nautikos","dataMode":"local"}
```

## Deployment

The production layout separates a lightweight web frontend from the imagery server:

```text
Browser → Vercel Next.js frontend → /api rewrite → Nautikos imagery server
                                             └→ local Caspian data cube
```

Set `NAUTIKOS_API_BASE_URL` in Vercel to the public HTTPS address of the imagery server. `next.config.ts` keeps browser requests same-origin and forwards only `/api/*`.

## Main commands

```bash
npm run data:annual     # annual Caspian products
npm run data:products   # derived environmental products
npm run data:monthly    # 2020–2026 monthly frames
npm run data:basemap    # regional z3–z8 satellite backdrop
npm run data:tiles      # fixed detailed XYZ pyramid
```

## Stack

Next.js 16, React 19, MapLibre GL, local XYZ/WebP/JPEG tiles, Copernicus Sentinel-1/2/3 products, CLMS/ERA5-compatible derived indicators and Groq vision analysis.

## License and attribution

Application code is provided for the Caspian Hackathon MVP. Satellite products retain their original Copernicus/ESA terms. Keep scientific provenance in exported reports and field-verification workflows.
