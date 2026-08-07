import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

const root = process.env.NAUTIKOS_DATA_DIR ?? "D:\\CaspianTwinData\\cube";
const bbox = [25, 25, 75, 60];
const minZoom = 3;
const maxZoom = 8;
const concurrency = 14;

function lonToX(lon, zoom) {
  return Math.floor((lon + 180) / 360 * 2 ** zoom);
}

function latToY(lat, zoom) {
  const rad = lat * Math.PI / 180;
  return Math.floor((1 - Math.asinh(Math.tan(rad)) / Math.PI) / 2 * 2 ** zoom);
}

const jobs = [];
for (let z = minZoom; z <= maxZoom; z++) {
  const minX = lonToX(bbox[0], z);
  const maxX = lonToX(bbox[2], z);
  const minY = latToY(bbox[3], z);
  const maxY = latToY(bbox[1], z);
  for (let x = minX; x <= maxX; x++) {
    for (let y = minY; y <= maxY; y++) jobs.push({ z, x, y });
  }
}

let cursor = 0;
let saved = 0;
let existing = 0;
let failed = 0;

async function worker() {
  while (cursor < jobs.length) {
    const job = jobs[cursor++];
    const target = path.join(root, "tiles", "basemap", String(job.z), String(job.x), `${job.y}.jpg`);
    if (existsSync(target)) {
      existing++;
      continue;
    }
    try {
      const response = await fetch(`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${job.z}/${job.y}/${job.x}`, {
        headers: { "user-agent": "Nautikos-Caspian-Hackathon/1.0" },
        signal: AbortSignal.timeout(20_000),
      });
      if (!response.ok) throw new Error(String(response.status));
      const bytes = Buffer.from(await response.arrayBuffer());
      if (bytes.length < 100) throw new Error("empty tile");
      mkdirSync(path.dirname(target), { recursive: true });
      writeFileSync(target, bytes);
      saved++;
    } catch {
      failed++;
    }
    const done = saved + existing + failed;
    if (done % 100 === 0) console.log(`${done}/${jobs.length} · saved ${saved} · existing ${existing} · failed ${failed}`);
  }
}

await Promise.all(Array.from({ length: concurrency }, () => worker()));
console.log(`ready ${jobs.length} · saved ${saved} · existing ${existing} · failed ${failed}`);
