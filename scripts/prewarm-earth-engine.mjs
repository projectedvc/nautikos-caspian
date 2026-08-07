import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";

const ROOT = process.env.NAUTIKOS_DATA_DIR ?? "D:\\CaspianTwinData\\cube";
const MANIFEST = process.env.NAUTIKOS_EE_MAPS ?? path.join(process.cwd(), "scripts", ".ee-map-urls.json");
const BBOX = [46, 36, 55.8, 47.4];
const minZoom = Number(process.env.NAUTIKOS_MIN_ZOOM ?? 5);
const maxZoom = Number(process.env.NAUTIKOS_MAX_ZOOM ?? 11);
const concurrency = Number(process.env.NAUTIKOS_CONCURRENCY ?? 24);

function tileX(lon, zoom) {
  return Math.floor((lon + 180) / 360 * 2 ** zoom);
}

function tileY(lat, zoom) {
  const radians = lat * Math.PI / 180;
  return Math.floor((1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2 * 2 ** zoom);
}

async function existsAndLooksLikeImage(filename) {
  try {
    return (await stat(filename)).size > 20;
  } catch {
    return false;
  }
}

const maps = JSON.parse(await readFile(MANIFEST, "utf8"));
const requestedLayers = (process.env.NAUTIKOS_LAYERS ?? "").split(",").filter(Boolean);
const requestedYears = (process.env.NAUTIKOS_YEARS ?? "").split(",").filter(Boolean).map(Number);
const selected = maps.filter((item) =>
  (!requestedLayers.length || requestedLayers.includes(item.layer))
  && (!requestedYears.length || requestedYears.includes(item.year))
);
const jobs = [];

for (const item of selected) {
  for (let z = minZoom; z <= maxZoom; z++) {
    const minX = tileX(BBOX[0], z);
    const maxX = tileX(BBOX[2], z);
    const minY = tileY(BBOX[3], z);
    const maxY = tileY(BBOX[1], z);
    for (let x = minX; x <= maxX; x++) {
      for (let y = minY; y <= maxY; y++) jobs.push({ ...item, z, x, y });
    }
  }
}

let cursor = 0;
let completed = 0;
let skipped = 0;
let failed = 0;

async function download(job) {
  const dir = path.join(ROOT, "tiles", job.layer, String(job.year), String(job.z), String(job.x));
  const filename = path.join(dir, `${job.y}.jpg`);
  if (await existsAndLooksLikeImage(filename)) {
    skipped++;
    return;
  }
  await mkdir(dir, { recursive: true });
  const url = job.url.replace("{z}", job.z).replace("{x}", job.x).replace("{y}", job.y);
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(45_000) });
      if (!response.ok) throw new Error(`${response.status}`);
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (bytes.length < 20) throw new Error("empty tile");
      await writeFile(filename, bytes);
      return;
    } catch (error) {
      if (attempt === 4) {
        failed++;
        console.error(`FAIL ${job.layer}/${job.year}/${job.z}/${job.x}/${job.y}: ${error.message}`);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, attempt * 700));
    }
  }
}

async function worker() {
  while (cursor < jobs.length) {
    const job = jobs[cursor++];
    await download(job);
    completed++;
    if (completed % 250 === 0 || completed === jobs.length) {
      console.log(`${completed}/${jobs.length} · cached ${completed - skipped - failed} · existing ${skipped} · failed ${failed}`);
    }
  }
}

console.log(`Nautikos local pyramid z${minZoom}–z${maxZoom}: ${jobs.length} tiles, ${selected.length} fixed products.`);
await Promise.all(Array.from({ length: concurrency }, () => worker()));
if (failed) process.exitCode = 1;
