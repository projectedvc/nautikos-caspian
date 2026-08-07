import { readFile } from "node:fs/promises";
import path from "node:path";
import { PNG } from "pngjs";

type BBox = [number, number, number, number];
type TrendRequest = { bbox?: BBox };

const LOCAL_DATA_ROOT = process.env.NAUTIKOS_DATA_DIR ?? "D:\\CaspianTwinData\\cube";
const CASPIAN_BBOX: BBox = [46, 36, 55.8, 47.4];
const YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026];
const cache = new Map<string, unknown>();

function validBBox(value: unknown): value is BBox {
  return Array.isArray(value) && value.length === 4
    && value.every((entry) => typeof entry === "number" && Number.isFinite(entry))
    && value[0] < value[2] && value[1] < value[3];
}

function regression(points: Array<{ year: number; value: number }>, min: number, max: number) {
  const n = points.length;
  if (n < 2) return { value: null, slope: null, r2: 0 };
  const meanX = points.reduce((sum, point) => sum + point.year, 0) / n;
  const meanY = points.reduce((sum, point) => sum + point.value, 0) / n;
  const denominator = points.reduce((sum, point) => sum + (point.year - meanX) ** 2, 0);
  const slope = denominator === 0 ? 0 : points.reduce((sum, point) => sum + (point.year - meanX) * (point.value - meanY), 0) / denominator;
  const intercept = meanY - slope * meanX;
  const predicted = points.map((point) => intercept + slope * point.year);
  const ssResidual = points.reduce((sum, point, index) => sum + (point.value - predicted[index]) ** 2, 0);
  const ssTotal = points.reduce((sum, point) => sum + (point.value - meanY) ** 2, 0);
  const r2 = ssTotal < 1e-12 ? 1 : Math.max(0, Math.min(1, 1 - ssResidual / ssTotal));
  return { value: Math.max(min, Math.min(max, intercept + slope * 2027)), slope, r2 };
}

async function readMetrics(year: number, bbox: BBox) {
  const bytes = await readFile(path.join(LOCAL_DATA_ROOT, "metrics", "annual", `${year}.png`));
  const png = PNG.sync.read(bytes);
  const [cw, cs, ce, cn] = CASPIAN_BBOX;
  const west = Math.max(cw, bbox[0]);
  const south = Math.max(cs, bbox[1]);
  const east = Math.min(ce, bbox[2]);
  const north = Math.min(cn, bbox[3]);
  if (west >= east || south >= north) throw new Error("Область находится вне локального куба Каспия");

  const x0 = Math.max(0, Math.floor((west - cw) / (ce - cw) * png.width));
  const x1 = Math.min(png.width, Math.ceil((east - cw) / (ce - cw) * png.width));
  const y0 = Math.max(0, Math.floor((cn - north) / (cn - cs) * png.height));
  const y1 = Math.min(png.height, Math.ceil((cn - south) / (cn - cs) * png.height));
  let valid = 0;
  let water = 0;
  let land = 0;
  let ndvi = 0;
  let stress = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const offset = (y * png.width + x) * 4;
      if (png.data[offset + 3] < 32) continue;
      valid++;
      if (png.data[offset] >= 128) {
        water++;
      } else {
        land++;
        ndvi += png.data[offset + 1] / 255 * 2 - 1;
        stress += png.data[offset + 2] / 255;
      }
    }
  }
  if (!valid) throw new Error("В выбранной области нет локальных валидных пикселей");
  return {
    year,
    waterShare: water / valid,
    vegetation: land ? ndvi / land : 0,
    soilStress: land ? stress / land : 0,
  };
}

export async function POST(request: Request) {
  let input: TrendRequest;
  try {
    input = await request.json() as TrendRequest;
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }
  if (!validBBox(input.bbox)) return Response.json({ error: "Valid bbox is required" }, { status: 400 });
  const key = input.bbox.map((value) => value.toFixed(4)).join(",");
  const cached = cache.get(key);
  if (cached) return Response.json(cached, { headers: { "x-nautikos-cache": "HIT" } });

  try {
    const series = await Promise.all(YEARS.map((year) => readMetrics(year, input.bbox as BBox)));
    const water = regression(series.map((point) => ({ year: point.year, value: point.waterShare })), 0, 1);
    const vegetation = regression(series.map((point) => ({ year: point.year, value: point.vegetation })), -1, 1);
    const soilStress = regression(series.map((point) => ({ year: point.year, value: point.soilStress })), 0, 1);
    const result = {
      source: "Nautikos local Sentinel‑2 analysis cube",
      method: "Одинаковый фиксированный период 1–15 июля каждого года; расчёт только по пикселям выбранной области",
      resolutionDegrees: 0.022,
      series,
      forecast: { year: 2027, waterShare: water.value, vegetation: vegetation.value, soilStress: soilStress.value },
      slopes: { waterShare: water.slope, vegetation: vegetation.slope, soilStress: soilStress.slope },
      confidence: (water.r2 + vegetation.r2 + soilStress.r2) / 3,
      limitation: "Прогноз 2027 — линейная сценарная экстраполяция локального ряда, а не будущий снимок. Для решения нужны более плотный ряд, гидрология и полевая проверка.",
    };
    cache.set(key, result);
    while (cache.size > 128) cache.delete(cache.keys().next().value as string);
    return Response.json(result, { headers: { "cache-control": "public, max-age=86400", "x-nautikos-source": "LOCAL" } });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Local trend calculation failed" }, { status: 500 });
  }
}
