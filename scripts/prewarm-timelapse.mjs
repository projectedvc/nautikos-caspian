const CASPIAN_BBOX = "46,36,55.8,47.4";
const BASE_URL = process.env.CASPIAN_APP_URL ?? "http://127.0.0.1:4180";
const CONCURRENCY = Number(process.env.CASPIAN_CACHE_CONCURRENCY ?? 2);
const jobs = [];

for (let year = 2020; year <= 2026; year++) {
  const lastMonth = year === 2026 ? 7 : 12;
  for (let month = 1; month <= lastMonth; month++) jobs.push({ year, month });
}

let cursor = 0;
let completed = 0;
const failures = [];

async function fetchFrame(job) {
  const query = new URLSearchParams({
    year: String(job.year),
    month: String(job.month),
    layer: "true-color",
    bbox: CASPIAN_BBOX,
    width: "640",
    height: "800",
    v: "timelapse-16",
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
    await new Promise((resolve) => setTimeout(resolve, attempt * 1500));
  }
  return false;
}

async function worker() {
  while (cursor < jobs.length) {
    const job = jobs[cursor++];
    const ok = await fetchFrame(job);
    completed++;
    if (!ok) failures.push(job);
    console.log(`${completed}/${jobs.length} · ${job.year}-${String(job.month).padStart(2, "0")} · ${ok ? "готово" : "ошибка"}`);
  }
}

console.log(`Прогрев ${jobs.length} месячных кадров Каспия, параллельность ${CONCURRENCY}.`);
await Promise.all(Array.from({ length: CONCURRENCY }, () => worker()));
console.log(`Готово: ${jobs.length - failures.length}; ошибок: ${failures.length}.`);
if (failures.length) {
  console.log(JSON.stringify(failures));
  process.exitCode = 1;
}
