import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

type BBox = [number, number, number, number];

type ProcessRequest = {
  bbox?: BBox;
  layer?: "true-color" | "olci-true-color" | "shoreline" | "vegetation" | "coast-moisture" | "soil-stress" | "erosion-risk" | "oil-roughness" | "water-quality" | "chlorophyll" | "suspended-matter" | "water-temperature";
  from?: string;
  to?: string;
  sceneId?: string;
  year?: number;
  month?: number;
  width?: number;
  height?: number;
};

const TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token";
const PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1";
const IMAGE_CACHE_LIMIT = 192;
const FIRST_YEAR = 2020;
const LAST_YEAR = 2026;
const WATER_BODIES_300M = "byoc-c19a3068-8be5-4077-8233-1dc54fbffe31";
const LAKE_SURFACE_TEMPERATURE_1KM = "byoc-401ca642-a169-4783-b1cf-cbd33e98eccb";
const LOCAL_DATA_ROOT = process.env.NAUTIKOS_DATA_DIR ?? "D:\\CaspianTwinData\\cube";

let tokenCache: { value: string; expiresAt: number } | null = null;
let tokenRequest: Promise<string> | null = null;
const imageCache = new Map<string, { bytes: Uint8Array; source: string; scene: string }>();

const evalscripts = {
  "true-color": `//VERSION=3
function setup() {
  return { input: [{ bands: ["B02", "B03", "B04", "SCL", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var red = 0, green = 0, blue = 0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    var cloudy = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10 || s.SCL === 11;
    if (!s.dataMask || cloudy) continue;
    red += s.B04; green += s.B03; blue += s.B02; count++;
  }
  if (!count) return [0, 0, 0, 0];
  return [2.7 * red / count, 2.7 * green / count, 2.7 * blue / count, 1];
}`,
  "olci-true-color": `//VERSION=3
function setup() {
  return { input: [{ bands: ["B04", "B06", "B08", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var r = 0, g = 0, b = 0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    if (!samples[i].dataMask) continue;
    r += samples[i].B08; g += samples[i].B06; b += samples[i].B04; count++;
  }
  if (!count) return [0, 0, 0, 0];
  return [3.2 * r / count, 3.2 * g / count, 3.2 * b / count, 1];
}`,
  chlorophyll: `//VERSION=3
function setup() {
  return { input: [{ bands: ["CHL_OC4ME", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var sum = 0, count = 0;
  for (var i = 0; i < samples.length; i++) { if (samples[i].dataMask) { sum += samples[i].CHL_OC4ME; count++; } }
  if (!count) return [0, 0, 0, 0];
  var t = Math.max(0, Math.min(1, ((sum / count) + 1.2) / 2.6));
  if (t < 0.5) return [0.08, 0.34 + t * 0.72, 0.68 - t * 0.36, 0.84];
  return [0.16 + t * 0.80, 0.78 - t * 0.56, 0.16, 0.88];
}`,
  "suspended-matter": `//VERSION=3
function setup() {
  return { input: [{ bands: ["TSM_NN", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var sum = 0, count = 0;
  for (var i = 0; i < samples.length; i++) { if (samples[i].dataMask) { sum += samples[i].TSM_NN; count++; } }
  if (!count) return [0, 0, 0, 0];
  var t = Math.max(0, Math.min(1, ((sum / count) + 1.3) / 3.3));
  if (t < 0.5) return [0.08 + t * 1.20, 0.26 + t * 0.92, 0.62 - t * 0.52, 0.84];
  return [0.95, 0.70 - t * 0.50, 0.08, 0.88];
}`,
  "water-temperature": `//VERSION=3
function setup() {
  return { input: [{ bands: ["LSWT", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var sum = 0, count = 0;
  for (var i = 0; i < samples.length; i++) { if (samples[i].dataMask) { sum += samples[i].LSWT * 0.01; count++; } }
  if (!count) return [0, 0, 0, 0];
  var c = sum / count;
  var t = Math.max(0, Math.min(1, (c - 4) / 30));
  if (t < 0.5) return [0.08 + t * 0.36, 0.34 + t * 0.92, 0.82 - t * 0.48, 0.86];
  return [0.94, 0.82 - t * 0.62, 0.10, 0.88];
}`,
  shoreline: `//VERSION=3
function setup() {
  return { input: [{ bands: ["WB", "QUAL", "dataMask"] }], output: { bands: 4, sampleType: "AUTO" } };
}
function evaluatePixel(s) {
  if (!s.dataMask || s.WB === 251 || s.WB === 255) return [0, 0, 0, 0];
  var certainty = Math.max(0.52, Math.min(0.92, (s.QUAL || 70) / 100));
  return [0.03, 0.34, 0.58, certainty];
}`,
  vegetation: `//VERSION=3
function setup() {
  return { input: [{ bands: ["B04", "B08", "B11", "SCL", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var ndviSum = 0, ndmiSum = 0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    var cloudy = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10 || s.SCL === 11;
    if (!s.dataMask || cloudy) continue;
    ndviSum += (s.B08 - s.B04) / (s.B08 + s.B04 + 0.0001);
    ndmiSum += (s.B08 - s.B11) / (s.B08 + s.B11 + 0.0001);
    count++;
  }
  if (!count) return [0, 0, 0, 0];
  var ndvi = ndviSum / count, ndmi = ndmiSum / count;
  if (ndvi < 0) return [0.14, 0.23, 0.28, 0.48];
  return [0.62 - ndvi * 0.55, 0.32 + ndvi * 0.68, 0.12 + Math.max(0, ndmi) * 0.3, 0.80];
}`,
  "soil-stress": `//VERSION=3
function setup() {
  return { input: [{ bands: ["B02", "B04", "B08", "B11", "SCL", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var stressSum = 0, ndviSum = 0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    var cloudy = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10 || s.SCL === 11;
    if (!s.dataMask || cloudy) continue;
    var ndviValue = (s.B08 - s.B04) / (s.B08 + s.B04 + 0.0001);
    var bsi = ((s.B11 + s.B04) - (s.B08 + s.B02)) / ((s.B11 + s.B04) + (s.B08 + s.B02) + 0.0001);
    stressSum += Math.max(0, Math.min(1, (bsi + 0.12) * 1.8 + Math.max(0, 0.22 - ndviValue)));
    ndviSum += ndviValue; count++;
  }
  if (!count) return [0, 0, 0, 0];
  var stress = stressSum / count, ndvi = ndviSum / count;
  if (stress > 0.58) return [0.92, 0.20, 0.10, 0.86];
  if (stress > 0.34) return [0.96, 0.64, 0.10, 0.76];
  return [0.12, 0.48 + Math.max(0, ndvi) * 0.42, 0.32, 0.44];
}`,
  "oil-roughness": `//VERSION=3
function setup() {
  return { input: [{ bands: ["VV", "VH", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var vv = 0, vh = 0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    if (!samples[i].dataMask) continue;
    vv += samples[i].VV; vh += samples[i].VH; count++;
  }
  if (!count) return [0, 0, 0, 0];
  var s = { VV: vv / count, VH: vh / count };
  const roughness = Math.log(0.05 / (0.018 + s.VV * 1.5));
  const cross = Math.min(1, Math.sqrt(Math.max(0, s.VH)) * 4.0);
  const r = Math.max(0, Math.min(1, roughness * 0.14));
  return [r * 0.18, r * 0.72 + cross * 0.16, r + cross * 0.08, 1];
}`,
  "water-quality": `//VERSION=3
function setup() {
  return { input: [{ bands: ["B02", "B03", "B04", "B08", "SCL", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var ndwiSum = 0, turbiditySum = 0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    var cloudy = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10 || s.SCL === 11;
    if (!s.dataMask || cloudy) continue;
    ndwiSum += (s.B03 - s.B08) / (s.B03 + s.B08 + 0.0001);
    turbiditySum += Math.max(0, Math.min(1, (s.B04 - s.B02) * 8 + 0.35));
    count++;
  }
  if (!count) return [0, 0, 0, 0];
  var ndwi = ndwiSum / count, turbidity = turbiditySum / count;
  if (ndwi < 0) return [0, 0, 0, 0];
  return [turbidity, 0.45 + (1 - turbidity) * 0.40, 0.86, 0.80];
}`,
  "coast-moisture": `//VERSION=3
function setup() {
  return { input: [{ bands: ["B08", "B11", "SCL", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var sum = 0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    var cloudy = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10 || s.SCL === 11;
    if (!s.dataMask || cloudy) continue;
    sum += (s.B08 - s.B11) / (s.B08 + s.B11 + 0.0001); count++;
  }
  if (!count) return [0, 0, 0, 0];
  var t = Math.max(0, Math.min(1, ((sum / count) + 0.35) / 0.9));
  if (t < 0.5) return [0.82 - t * 0.76, 0.48 + t * 0.54, 0.16, 0.72];
  return [0.10, 0.62 - t * 0.28, 0.46 + t * 0.48, 0.78];
}`,
  "erosion-risk": `//VERSION=3
function setup() { return { input: ["DEM", "dataMask"], output: { bands: 4 } }; }
function evaluatePixel(s) {
  if (!s.dataMask) return [0, 0, 0, 0];
  var h = s.DEM;
  if (h < 0) return [0.05, 0.38, 0.58, 0.78];
  if (h < 50) return [0.34, 0.64, 0.38, 0.70];
  if (h < 200) return [0.92, 0.66, 0.18, 0.72];
  return [0.60, 0.24, 0.10, 0.76];
}`,
} as const;

const sentinel2ShorelineEvalscript = `//VERSION=3
function setup() {
  return { input: [{ bands: ["B03", "B08", "SCL", "dataMask"] }], mosaicking: "ORBIT", output: { bands: 4 } };
}
function evaluatePixel(samples) {
  var water = 0, clear = 0, ndwiSum = 0;
  for (var i = 0; i < samples.length; i++) {
    var s = samples[i];
    var cloudy = s.SCL === 3 || s.SCL === 8 || s.SCL === 9 || s.SCL === 10 || s.SCL === 11;
    if (!s.dataMask || cloudy) continue;
    var ndwi = (s.B03 - s.B08) / (s.B03 + s.B08 + 0.0001);
    clear++; ndwiSum += ndwi;
    if (ndwi > 0.02) water++;
  }
  if (!clear || water / clear < 0.42) return [0, 0, 0, 0];
  var meanNdwi = ndwiSum / clear;
  return [0.03, 0.34, 0.58, Math.min(0.90, 0.56 + water / clear * 0.28 + Math.max(0, meanNdwi) * 0.12)];
}`;

function validBBox(value: unknown): value is BBox {
  return Array.isArray(value) && value.length === 4 && value.every((entry) => typeof entry === "number" && Number.isFinite(entry));
}

function clampSize(value: number | undefined, fallback: number, max: number) {
  return Math.max(256, Math.min(max, Math.round(value ?? fallback)));
}

function bboxDimensionsKm(bbox: BBox) {
  const [west, south, east, north] = bbox;
  const midLat = (south + north) / 2 * Math.PI / 180;
  return {
    width: Math.abs(east - west) * 111.32 * Math.max(0.15, Math.cos(midLat)),
    height: Math.abs(north - south) * 110.57,
  };
}

function outputSize(input: ProcessRequest) {
  const dimensions = bboxDimensionsKm(input.bbox as BBox);
  // CDSE rejects Sentinel-2 requests coarser than 1500 m/px. At small scales
  // a 512 px web tile is therefore rendered a little larger and downsampled by
  // the browser. At detailed zooms we keep the native 512 px tile.
  const minimumWidth = Math.ceil(dimensions.width * 1000 / 1450);
  const minimumHeight = Math.ceil(dimensions.height * 1000 / 1450);
  const side = clampSize(Math.max(input.width ?? 512, input.height ?? 512, minimumWidth, minimumHeight), 512, 2048);
  return { width: side, height: side };
}

function annualWindow(year: number, layer?: ProcessRequest["layer"]) {
  if (layer === "shoreline") {
    // The historical Water Bodies series begins in October 2020. October is
    // therefore the common month for 2020–2025; 2026 uses the latest complete
    // month available at the time of the hackathon.
    const month = year === 2026 ? "07" : "10";
    const lastDay = month === "07" ? "31" : "31";
    return { from: `${year}-${month}-01T00:00:00Z`, to: `${year}-${month}-${lastDay}T23:59:59Z` };
  }
  if (["olci-true-color", "chlorophyll", "suspended-matter"].includes(layer ?? "")) {
    // OLCI revisits frequently. A fixed five-day August window avoids an
    // expensive multi-month ocean mosaic while keeping every year comparable.
    return { from: `${year}-08-01T00:00:00Z`, to: `${year}-08-05T23:59:59Z` };
  }
  if (layer === "water-temperature" || layer === "oil-roughness") {
    return { from: `${year}-08-01T00:00:00Z`, to: `${year}-08-10T23:59:59Z` };
  }
  // The same completed summer window is used for every year, including 2026.
  // This prevents the map from comparing winter with summer or future dates.
  return {
    from: `${year}-06-01T00:00:00Z`,
    to: `${year}-08-05T23:59:59Z`,
  };
}

function monthlyWindow(year: number, month: number) {
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const paddedMonth = String(month).padStart(2, "0");
  return {
    from: `${year}-${paddedMonth}-01T00:00:00Z`,
    to: `${year}-${paddedMonth}-${String(lastDay).padStart(2, "0")}T23:59:59Z`,
  };
}

function asIsoTime(value: string, end = false) {
  return value.includes("T") ? value : `${value}T${end ? "23:59:59" : "00:00:00"}Z`;
}

function tileBBox(z: number, x: number, y: number): BBox {
  const scale = 2 ** z;
  const west = x / scale * 360 - 180;
  const east = (x + 1) / scale * 360 - 180;
  const north = Math.atan(Math.sinh(Math.PI * (1 - 2 * y / scale))) * 180 / Math.PI;
  const south = Math.atan(Math.sinh(Math.PI * (1 - 2 * (y + 1) / scale))) * 180 / Math.PI;
  return [west, south, east, north];
}

async function accessToken(clientId: string, clientSecret: string) {
  if (tokenCache && tokenCache.expiresAt > Date.now() + 30_000) return tokenCache.value;
  if (tokenRequest) return tokenRequest;
  tokenRequest = (async () => {
    const body = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: clientId,
      client_secret: clientSecret,
    });
    const response = await fetch(TOKEN_URL, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
      signal: AbortSignal.timeout(20_000),
    });
    if (!response.ok) throw new Error(`OAuth failed: ${response.status}`);
    const payload = await response.json() as { access_token: string; expires_in?: number };
    tokenCache = {
      value: payload.access_token,
      expiresAt: Date.now() + Math.max(60, payload.expires_in ?? 300) * 1000,
    };
    return payload.access_token;
  })();
  try {
    return await tokenRequest;
  } finally {
    tokenRequest = null;
  }
}

function renderCacheKey(input: ProcessRequest) {
  const bbox = input.bbox?.map((value) => value.toFixed(5)).join(",") ?? "";
  return [input.sceneId ?? `${input.from}/${input.to}`, input.layer, bbox, input.width, input.height].join("|");
}

function imageResponse(bytes: Uint8Array, source: string, scene: string, hit: boolean) {
  return new Response(bytes.slice(), {
    headers: {
      "content-type": "image/png",
      "cache-control": "public, max-age=31536000, immutable",
      "x-caspian-source": source,
      "x-caspian-scene": scene,
      "x-caspian-cache": hit ? "HIT" : "MISS",
    },
  });
}

async function localProductResponse(relativePath: string) {
  const fullPath = path.join(LOCAL_DATA_ROOT, relativePath);
  if (!existsSync(fullPath)) return null;
  const bytes = await readFile(fullPath);
  const contentType = bytes[0] === 0x89 && bytes[1] === 0x50
    ? "image/png"
    : bytes[0] === 0xff && bytes[1] === 0xd8
      ? "image/jpeg"
      : bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46
        ? "image/webp"
        : "application/octet-stream";
  return new Response(bytes, {
    headers: {
      "content-type": contentType,
      "cache-control": "public, max-age=31536000, immutable",
      "x-nautikos-source": "LOCAL",
      "x-nautikos-path": relativePath.replaceAll("\\", "/"),
    },
  });
}

async function firstLocalProduct(relativePaths: string[]) {
  for (const relativePath of relativePaths) {
    const response = await localProductResponse(relativePath);
    if (response) return response;
  }
  return null;
}

async function processImage(input: ProcessRequest) {
  const clientId = process.env.CDSE_CLIENT_ID;
  const clientSecret = process.env.CDSE_CLIENT_SECRET;
  if (!clientId || !clientSecret) {
    return Response.json({ error: "Copernicus OAuth is not configured" }, { status: 503 });
  }

  if (!validBBox(input.bbox) || !input.layer || !(input.layer in evalscripts) || !input.from || !input.to) {
    return Response.json({ error: "bbox, layer, from and to are required" }, { status: 400 });
  }

  const collection = input.layer === "oil-roughness"
    ? "sentinel-1-grd"
    : ["olci-true-color", "chlorophyll", "suspended-matter"].includes(input.layer)
      ? "sentinel-3-olci-l2"
      : input.layer === "water-temperature"
        ? LAKE_SURFACE_TEMPERATURE_1KM
        : input.layer === "erosion-risk"
          ? "dem"
          : input.layer === "shoreline"
            ? WATER_BODIES_300M
            : "sentinel-2-l2a";
  const isS2 = collection === "sentinel-2-l2a";
  const isS3 = collection === "sentinel-3-olci-l2";
  const isDem = collection === "dem";
  const cacheKey = renderCacheKey(input);
  const cached = imageCache.get(cacheKey);
  if (cached) {
    imageCache.delete(cacheKey);
    imageCache.set(cacheKey, cached);
    return imageResponse(cached.bytes, cached.source, cached.scene, true);
  }

  const dataFilter: Record<string, unknown> = isDem ? { demInstance: "COPERNICUS_30" } : {
    timeRange: {
      from: asIsoTime(input.from),
      to: asIsoTime(input.to, true),
    },
    mosaickingOrder: isS2 ? "leastCC" : "mostRecent",
  };
  if (isS2 || isS3) dataFilter.maxCloudCoverage = 35;
  if (input.layer === "oil-roughness") {
    dataFilter.acquisitionMode = "IW";
    dataFilter.polarization = "DV";
  }

  const dataEntry: Record<string, unknown> = { type: collection, dataFilter };
  if (isDem) {
    dataEntry.processing = { upsampling: "BILINEAR", downsampling: "BILINEAR" };
  }
  if (input.layer === "oil-roughness") {
    dataEntry.processing = {
      orthorectify: true,
      demInstance: "COPERNICUS_30",
      speckleFilter: { type: "LEE", windowSizeX: 5, windowSizeY: 5 },
    };
  }

  try {
    const size = outputSize(input);
    const token = await accessToken(clientId, clientSecret);
    const response = await fetch(PROCESS_URL, {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        accept: "image/png",
      },
      body: JSON.stringify({
        input: { bounds: { bbox: input.bbox, properties: { crs: "http://www.opengis.net/def/crs/OGC/1.3/CRS84" } }, data: [dataEntry] },
        output: {
          width: size.width,
          height: size.height,
          responses: [{ identifier: "default", format: { type: "image/png" } }],
        },
        evalscript: evalscripts[input.layer],
      }),
      signal: AbortSignal.timeout(isS3 ? 75_000 : 45_000),
    });

    if (!response.ok) {
      const detail = await response.text();
      return Response.json({ error: "Copernicus processing failed", detail }, { status: response.status });
    }

    const bytes = new Uint8Array(await response.arrayBuffer());
    imageCache.set(cacheKey, { bytes, source: collection, scene: input.sceneId ?? "time-locked" });
    while (imageCache.size > IMAGE_CACHE_LIMIT) {
      const oldest = imageCache.keys().next().value as string | undefined;
      if (!oldest) break;
      imageCache.delete(oldest);
    }
    return imageResponse(bytes, collection, input.sceneId ?? "time-locked", false);
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Unexpected processing error" }, { status: 502 });
  }
}

export async function POST(request: Request) {
  let input: ProcessRequest;
  try {
    input = await request.json() as ProcessRequest;
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }
  return processImage(input);
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const optionalNumber = (name: string) => {
    const raw = url.searchParams.get(name);
    return raw === null || raw.trim() === "" ? Number.NaN : Number(raw);
  };
  const z = optionalNumber("z");
  const x = optionalNumber("x");
  const y = optionalNumber("y");
  const requestedBBox = url.searchParams.get("bbox")?.split(",").map(Number);
  const layer = url.searchParams.get("layer") as ProcessRequest["layer"];
  const from = url.searchParams.get("from") ?? undefined;
  const to = url.searchParams.get("to") ?? undefined;
  const sceneId = url.searchParams.get("sceneId") ?? undefined;
  const year = optionalNumber("year");
  const month = optionalNumber("month");
  const width = optionalNumber("width");
  const height = optionalNumber("height");

  if (Number.isInteger(year) && layer && layer in evalscripts) {
    const local = Number.isInteger(z) && Number.isInteger(x) && Number.isInteger(y)
      ? await firstLocalProduct([
          ...["webp", "jpg", "png"].map((extension) => path.join("tiles", layer, String(year), String(z), String(x), `${y}.${extension}`)),
          ...layer === "erosion-risk"
            ? ["webp", "jpg", "png"].map((extension) => path.join("tiles", layer, "2026", String(z), String(x), `${y}.${extension}`))
            : [],
        ])
      : Number.isInteger(month) && month >= 1 && month <= 12
        ? await localProductResponse(path.join("overviews", "monthly", String(year), `${String(month).padStart(2, "0")}.webp`))
        : await localProductResponse(path.join("overviews", "annual", String(year), `${layer}.webp`));
    if (local) return local;
    // Annual monitoring is deliberately local-only.  A missing cache entry is
    // reported as missing instead of silently switching scene/provider at a
    // different zoom.  Monthly slideshow frames keep their separate updater.
    if (Number.isInteger(z) || !Number.isInteger(month)) {
      return new Response(null, { status: 404, headers: { "cache-control": "no-store", "x-nautikos-source": "LOCAL-MISS" } });
    }
  }

  let bbox: BBox;
  if (validBBox(requestedBBox)) {
    bbox = requestedBBox;
  } else {
    if (![z, x, y].every(Number.isInteger) || z < 0 || z > 17 || x < 0 || y < 0) {
      return Response.json({ error: "bbox or valid tile coordinates are required" }, { status: 400 });
    }
    bbox = tileBBox(z, x, y);
  }

  const validYear = Number.isInteger(year) && year >= FIRST_YEAR && year <= LAST_YEAR;
  const validMonth = Number.isInteger(month) && month >= 1 && month <= 12 && !(year === 2026 && month > 8);
  const fixedWindow = validYear
    ? validMonth ? monthlyWindow(year, month) : annualWindow(year, layer)
    : null;

  return processImage({
    bbox,
    layer,
    from: fixedWindow?.from ?? from,
    to: fixedWindow?.to ?? to,
    sceneId,
    year: fixedWindow ? year : undefined,
    month: fixedWindow && validMonth ? month : undefined,
    width: Number.isFinite(width) && width > 0 ? width : 512,
    height: Number.isFinite(height) && height > 0 ? height : 512,
  });
}
