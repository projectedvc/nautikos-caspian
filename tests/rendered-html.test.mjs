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
  assert.match(html, /Вода: спектральный снимок/);
  assert.match(html, /Кандидаты утечки нефти/);
  assert.match(html, /2020/);
  assert.match(html, /2026/);
  assert.doesNotMatch(html, /Your site is taking shape|Building your site/);
});

test("uses local deterministic imagery, optional AOI and server-side vision", async () => {
  const [component, processRoute, basemapRoute, trendRoute, aiRoute, server] = await Promise.all([
    readFile(new URL("../app/CaspianTwin.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/api/sentinel/process/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/basemap/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/sentinel/trend/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/ai/analyze/route.ts", import.meta.url), "utf8"),
    readFile(new URL("../scripts/start-windows.mjs", import.meta.url), "utf8"),
  ]);

  assert.match(component, /2020, 2021, 2022, 2023, 2024, 2025, 2026/);
  assert.match(component, /compare-divider/);
  assert.match(component, /compareEnabled/);
  assert.match(component, /annualOverviewUrl/);
  assert.match(component, /annualTileUrl/);
  assert.match(component, /regionalBasemapTileUrl/);
  assert.match(component, /monthlyOverviewUrl/);
  assert.match(component, /selection-rectangle/);
  assert.match(component, /type: "raster"/);
  assert.doesNotMatch(component, /GROQ_API_KEY/);

  assert.match(basemapRoute, /tiles.*basemap/s);
  assert.match(processRoute, /NAUTIKOS_DATA_DIR/);
  assert.match(processRoute, /process\/v1/);
  assert.match(processRoute, /sentinel-1-grd/);
  assert.match(processRoute, /sentinel-2-l2a/);
  assert.match(processRoute, /sentinel-3-olci-l2/);
  assert.match(processRoute, /annualWindow/);
  assert.match(processRoute, /max-age=31536000/);
  assert.match(trendRoute, /metrics/);
  assert.match(trendRoute, /regression/);
  assert.match(aiRoute, /api\.groq\.com\/openai\/v1\/chat\/completions/);
  assert.match(aiRoute, /image_url/);
  assert.match(server, /\/health/);
  assert.match(server, /Access-Control-Allow-Origin/);
});
