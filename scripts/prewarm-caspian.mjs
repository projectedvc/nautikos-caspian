const CASPIAN_BBOX = [46.0, 36.0, 55.8, 47.4];
const YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026];
const BASE_URL = process.env.CASPIAN_APP_URL ?? "http://127.0.0.1:4180";
const MIN_ZOOM = 4;
const MAX_ZOOM = 8;
const CONCURRENCY = Number(process.env.CASPIAN_CACHE_CONCURRENCY ?? 3);

function tileX(longitude, zoom) {
  return Math.floor((longitude + 180) / 360 * 2 ** zoom);
}

function tileY(latitude, zoom) {
  const radians = latitude * Math.PI / 180;
  return Math.floor((1 - Math.asinh(Math.tan(radians)) / Math.PI) / 2 * 2 ** zoom);
}

const jobs = [];
for (const year of YEARS) {
  for (let zoom = MIN_ZOOM; zoom <= MAX_ZOOM; zoom++) {
    const minX = tileX(CASPIAN_BBOX[0], zoom);
    const maxX = tileX(CASPIAN_BBOX[2], zoom);
    const minY = tileY(CASPIAN_BBOX[3], zoom);
    const maxY = tileY(CASPIAN_BBOX[1], zoom);
    for (let x = minX; x <= maxX; x++) {
      for (let y = minY; y <= maxY; y++) jobs.push({ year, zoom, x, y });
    }
  }
}

let cursor = 0;
let completed = 0;
let failed = 0;

async function fetchTile(job) {
  const query = new URLSearchParams({
    year: String(job.year),
    layer: "shoreline",
    z: String(job.zoom),
    x: String(job.x),
    y: String(job.y),
    width: "512",
    height: "512",
    v: "9",
  });
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const response = await fetch(`${BASE_URL}/api/sentinel/process?${query}`);
      if (response.ok) {
        await response.arrayBuffer();
        return true;
      }
    } catch {
      // The next pass retries temporary local-server or Copernicus failures.
    }
    await new Promise((resolve) => setTimeout(resolve, attempt * 800));
  }
  return false;
}

async function worker() {
  while (cursor < jobs.length) {
    const job = jobs[cursor++];
    if (await fetchTile(job)) completed++;
    else failed++;
    const done = completed + failed;
    if (done % 25 === 0 || done === jobs.length) {
      process.stdout.write(`\rКэш обзора Каспия: ${done}/${jobs.length}, ошибок: ${failed}`);
    }
  }
}

console.log(`Прогрев ${jobs.length} тайлов Water Bodies: 2020–2026, zoom ${MIN_ZOOM}–${MAX_ZOOM}.`);
await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()));
console.log(`\nГотово. Успешно: ${completed}; ошибок: ${failed}.`);
if (failed > 0) process.exitCode = 1;
