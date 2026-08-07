const CASPIAN_BBOX = "46,36,55.8,47.4";
const DEFAULT_YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026];
const DEFAULT_LAYERS = [
  "true-color",
  "olci-true-color",
  "shoreline",
  "water-quality",
  "chlorophyll",
  "suspended-matter",
  "water-temperature",
  "vegetation",
  "coast-moisture",
  "soil-stress",
  "erosion-risk",
  "oil-roughness",
];
const BASE_URL = process.env.CASPIAN_APP_URL ?? "http://127.0.0.1:4180";
const CONCURRENCY = Number(process.env.CASPIAN_CACHE_CONCURRENCY ?? 2);
const YEARS = (process.env.CASPIAN_OVERVIEW_YEARS ?? DEFAULT_YEARS.join(",")).split(",").map(Number).filter(Number.isFinite);
const LAYERS = (process.env.CASPIAN_OVERVIEW_LAYERS ?? DEFAULT_LAYERS.join(",")).split(",").map((value) => value.trim()).filter(Boolean);

const jobs = LAYERS.flatMap((layer) => YEARS.map((year) => ({ layer, year })));
let cursor = 0;
let completed = 0;
const failures = [];

async function fetchOverview(job) {
  const isPhoto = job.layer === "true-color";
  const query = new URLSearchParams({
    year: String(job.year),
    layer: job.layer,
    bbox: CASPIAN_BBOX,
    width: isPhoto ? "1024" : "640",
    height: isPhoto ? "1280" : "800",
    v: "overview-16",
  });
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const response = await fetch(`${BASE_URL}/api/sentinel/process?${query}`);
      if (response.ok) {
        await response.arrayBuffer();
        return true;
      }
    } catch {
      // Retry a temporary local-server or Copernicus failure.
    }
    await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
  }
  return false;
}

async function worker() {
  while (cursor < jobs.length) {
    const job = jobs[cursor++];
    const ok = await fetchOverview(job);
    completed++;
    if (!ok) failures.push(job);
    console.log(`${completed}/${jobs.length} · ${job.year} · ${job.layer} · ${ok ? "готово" : "ошибка"}`);
  }
}

console.log(`Прогрев ${jobs.length} полноэкранных растров Каспия, параллельность ${CONCURRENCY}.`);
await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()));
console.log(`Готово: ${jobs.length - failures.length}; ошибок: ${failures.length}.`);
if (failures.length) {
  console.log(JSON.stringify(failures));
  process.exitCode = 1;
}
