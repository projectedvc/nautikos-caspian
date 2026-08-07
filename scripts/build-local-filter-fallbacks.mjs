import { createHash } from "node:crypto";
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";
import sharp from "sharp";

const CACHE_ROOT = process.env.CASPIAN_CACHE_DIR ?? "D:\\CaspianTwinData\\cache\\tiles";
const BBOX = "46,36,55.8,47.4";
const YEARS = [2020, 2026];
const LAYERS = ["shoreline", "water-quality", "vegetation", "soil-stress", "oil-roughness"];
mkdirSync(CACHE_ROOT, { recursive: true });

function cachePath(urlPath) {
  return path.join(CACHE_ROOT, `${createHash("sha256").update(urlPath).digest("hex")}.png`);
}

function photoPath(year) {
  return cachePath(`/api/sentinel/process?year=${year}&layer=true-color&bbox=${BBOX}&width=1024&height=1280&v=overview-16`);
}

function filterPath(year, layer) {
  return cachePath(`/api/sentinel/process?year=${year}&layer=${layer}&bbox=${BBOX}&width=640&height=800&v=overview-16`);
}

function classify(layer, r, g, b) {
  const brightness = (r + g + b) / 3;
  const water = brightness < 115 && (b > r * 0.88 || g > r * 1.12);
  if (layer === "shoreline") return water ? [15, 98, 141, 178] : [0, 0, 0, 0];
  if (layer === "water-quality") {
    if (!water) return [0, 0, 0, 0];
    const turbidity = Math.max(0, Math.min(1, (r - b + 65) / 120));
    return [Math.round(35 + turbidity * 210), Math.round(190 - turbidity * 75), Math.round(220 - turbidity * 120), 170];
  }
  if (layer === "vegetation") {
    if (water) return [0, 0, 0, 0];
    const excessGreen = 2 * g - r - b;
    if (excessGreen > 22) return [45, 166, 94, 175];
    if (excessGreen > 2) return [151, 139, 63, 120];
    return [0, 0, 0, 0];
  }
  if (layer === "soil-stress") {
    if (water || brightness < 60) return [0, 0, 0, 0];
    const greenSignal = 2 * g - r - b;
    const dry = Math.max(0, Math.min(1, (brightness - 85) / 95 - greenSignal / 180));
    if (dry > 0.62) return [235, 57, 32, 180];
    if (dry > 0.36) return [241, 168, 29, 145];
    if (greenSignal > 12) return [39, 123, 82, 100];
    return [0, 0, 0, 0];
  }
  if (layer === "oil-roughness") {
    if (!water) return [0, 0, 0, 0];
    const smooth = Math.max(0, Math.min(1, (75 - brightness) / 55));
    return smooth > 0.25 ? [7, Math.round(38 + (1 - smooth) * 95), Math.round(55 + (1 - smooth) * 155), 145] : [0, 0, 0, 0];
  }
  return [0, 0, 0, 0];
}

for (const year of YEARS) {
  const source = photoPath(year);
  if (!existsSync(source)) {
    console.log(`${year}: нет локального фотокомпозита, пропуск.`);
    continue;
  }
  const { data, info } = await sharp(source).resize(640, 800, { fit: "fill" }).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  for (const layer of LAYERS) {
    const output = Buffer.alloc(info.width * info.height * 4);
    for (let index = 0; index < info.width * info.height; index++) {
      const offset = index * 4;
      const [r, g, b, a] = [data[offset], data[offset + 1], data[offset + 2], data[offset + 3]];
      const color = a === 0 ? [0, 0, 0, 0] : classify(layer, r, g, b);
      output[offset] = color[0]; output[offset + 1] = color[1]; output[offset + 2] = color[2]; output[offset + 3] = color[3];
    }
    const destination = filterPath(year, layer);
    await sharp(output, { raw: { width: info.width, height: info.height, channels: 4 } }).png({ compressionLevel: 9 }).toFile(destination);
    console.log(`${year} · ${layer} · ${destination}`);
  }
}
