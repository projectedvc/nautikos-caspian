const CASPIAN_BBOX = [46, 36, 55.8, 47.4];
const YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026];
const DEFAULT_LAYERS = [
  "true-color",
  "olci-true-color",
  "chlorophyll",
  "suspended-matter",
  "water-temperature",
  "shoreline",
  "water-quality",
  "oil-roughness",
  "vegetation",
  "coast-moisture",
  "soil-stress",
  "erosion-risk",
];

const BASE_URL = process.env.CASPIAN_APP_URL ?? "http://127.0.0.1:4180";
const MIN_ZOOM = Number(process.env.CASPIAN_TILE_MIN_ZOOM ?? 6);
const MAX_ZOOM = Number(process.env.CASPIAN_TILE_MAX_ZOOM ?? 8);
const CONCURRENCY = Number(process.env.CASPIAN_CACHE_CONCURRENCY ?? 3);
const LAYERS = (process.env.CASPIAN_TILE_LAYERS ?? DEFAULT_LAYERS.join(",")).split(",").map((value) => value.trim()).filter(Boolean);

function tileX(lon, zoom) {
  return Math.floor((lon + 180) / 360 * 2 ** zoom);
}

function tileY(lat, zoom) {
  const radians = lat * Math.PI / 180;
  return Math.floor((1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2 * 2 ** zoom);
}

const jobs = [];
for (const layer of LAYERS) {
  const years = layer === "erosion-risk" ? [2026] : YEARS;
  for (const year of years) {
    for (let z = MIN_ZOOM; z <= MAX_ZOOM; z++) {
      const minX = tileX(CASPIAN_BBOX[0], z);
      const maxX = tileX(CASPIAN_BBOX[2], z);
      const minY = tileY(CASPIAN_BBOX[3], z);
      const maxY = tileY(CASPIAN_BBOX[1], z);
      for (let x = minX; x <= maxX; x++) {
        for (let y = minY; y <= maxY; y++) jobs.push({ layer, year, z, x, y });
      }
    }
  }
}

let cursor = 0;
let completed = 0;
const failures = [];

async function fetchTile(job) {
  const query = new URLSearchParams({
    year: String(job.year),
    layer: job.layer,
    z: String(job.z),
    x: String(job.x),
    y: String(job.y),
    width: "512",
    height: "512",
    v: "fixed-pyramid-21",
  });
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const response = await fetch(`${BASE_URL}/api/sentinel/process?${query}`);
      if (response.ok && response.headers.get("content-type")?.startsWith("image/")) {
        await response.arrayBuffer();
        return true;
      }
    } catch {
      // Retry a temporary local server or Copernicus failure.
    }
    await new Promise((resolve) => setTimeout(resolve, attempt * 900));
  }
  return false;
}

async function worker() {
  while (cursor < jobs.length) {
    const job = jobs[cursor++];
    const ok = await fetchTile(job);
    completed++;
    if (!ok) failures.push(job);
    if (completed % 25 === 0 || completed === jobs.length) {
      console.log(`${completed}/${jobs.length} · ошибок ${failures.length}`);
    }
  }
}

console.log(`Фиксированная пирамида Каспия: z${MIN_ZOOM}–z${MAX_ZOOM}, ${jobs.length} тайлов, ${LAYERS.length} слоёв.`);
await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()));
console.log(`Готово: ${jobs.length - failures.length}; ошибок: ${failures.length}.`);
if (failures.length) process.exitCode = 1;
