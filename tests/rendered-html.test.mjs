import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Nautikos Caspian workspace", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<html lang="ru">/i);
  assert.match(html, /<title>Nautikos — экологическая разведка Каспия<\/title>/i);
  assert.match(html, /Экологический интеллект Каспия/);
  assert.match(html, /Реки и водотоки/);
  assert.match(html, /Sentinel.?2 L2A/);
  assert.match(html, /2020/);
  assert.match(html, /2026/);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site/);
});

test("uses the local Jupyter COG service, six real Copernicus filters and AOI tools", async () => {
  const [component, basemapRoute, trendRoute, aiRoute, server, localProducts, builder, productConfig] = await Promise.all([
    readFile(new URL("../app/CaspianTwin.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/basemap/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/sentinel/trend/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/analyze/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../scripts/start-windows.mjs", import.meta.url), "utf8"),
    readFile(new URL("../server/nautikos_server/local_products.py", import.meta.url), "utf8"),
    readFile(new URL("../server/scripts/build_cog_products.py", import.meta.url), "utf8"),
    readFile(new URL("../server/config/products.json", import.meta.url), "utf8"),
  ]);

  assert.match(component, /2020, 2021, 2022, 2023, 2024, 2025, 2026/);
  assert.match(component, /compare-divider/);
  assert.match(component, /compareEnabled/);
  assert.match(component, /NEXT_PUBLIC_NAUTIKOS_DATA_URL/);
  assert.match(component, /PRODUCT_BY_LAYER/);
  assert.match(component, /annualTileUrl/);
  assert.match(component, /\/v2\/tiles/);
  assert.doesNotMatch(component, /annual-filter-overview|overviews\/copernicus|\/api\/filters/);
  assert.match(component, /exportAoiImage/);
  assert.match(component, /\/v2\/aoi\/export/);
  assert.match(component, /SOLUTIONS/);
  assert.match(component, /selection-rectangle/);
  assert.match(component, /type: "raster"/);
  assert.doesNotMatch(component, /year === 2026[^\n]*2025/);
  assert.doesNotMatch(component, /monthlyOverviewUrl/);
  assert.doesNotMatch(component, /GROQ_API_KEY/);

  assert.match(basemapRoute, /REGIONAL-SATELLITE-CONTEXT/);
  assert.match(basemapRoute, /upstream\.arrayBuffer\(\)/);
  assert.doesNotMatch(basemapRoute, /next:\s*\{\s*revalidate/);
  assert.doesNotMatch(basemapRoute, /writeFile|mkdir/);
  const products = JSON.parse(productConfig).products;
  assert.deepEqual(Object.keys(products).sort(), [
    "coastal_vegetation", "oil_candidates", "rivers", "water_colour", "water_extent", "water_temperature",
  ]);
  assert.match(localProducts, /mode.*local-only|local-only/s);
  assert.match(localProducts, /sentinel-2-l2a/);
  assert.match(localProducts, /sentinel-1-grd/);
  assert.match(localProducts, /sentinel-3-slstr-l2-wst/);
  assert.match(localProducts, /sentinel-3-olci-l2-water/);
  assert.doesNotMatch(localProducts, /import requests|process\/v1/);
  assert.match(builder, /NDWI = \(B03-B08\)\/\(B03\+B08\)|NDWI.*B03.*B08/s);
  assert.match(builder, /TSM_NN/);
  assert.match(builder, /schema.*3/s);
  assert.match(trendRoute, /metrics/);
  assert.match(trendRoute, /regression/);
  assert.match(aiRoute, /api\.groq\.com\/openai\/v1\/chat\/completions/);
  assert.match(aiRoute, /image_url/);
  assert.match(server, /\/health/);
  assert.match(server, /Access-Control-Allow-Origin/);
});
