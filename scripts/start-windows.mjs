import { createReadStream, existsSync, mkdirSync, readFileSync, statSync, writeFile } from "node:fs";
import { createHash } from "node:crypto";
import { createServer, request as proxyRequest } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { startProdServer } from "../node_modules/vinext/dist/server/prod-server.js";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const clientRoot = path.join(projectRoot, "dist", "client");
const outerPort = Number(process.env.PORT ?? process.argv[2] ?? 4180);
const innerPort = outerPort + 1;
const host = process.env.HOST ?? "127.0.0.1";
const upstreamHost = "127.0.0.1";
const preferredCacheRoot = process.env.CASPIAN_CACHE_DIR ?? "D:\\CaspianTwinData\\cache\\tiles";
const tileCacheRoot = path.parse(preferredCacheRoot).root && existsSync(path.parse(preferredCacheRoot).root)
  ? preferredCacheRoot
  : path.join(projectRoot, ".cache", "tiles");
mkdirSync(tileCacheRoot, { recursive: true });

for (const filename of [".env.local", ".env"]) {
  const envPath = path.join(projectRoot, filename);
  if (!existsSync(envPath)) continue;
  for (const line of readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match || process.env[match[1]] !== undefined) continue;
    process.env[match[1]] = match[2].replace(/^(['"])(.*)\1$/, "$2");
  }
}

const mime = {
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
  ".woff2": "font/woff2",
};

const corsHeaders = {
  "Access-Control-Allow-Origin": process.env.NAUTIKOS_CORS_ORIGIN ?? "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

const localDataRoot = process.env.NAUTIKOS_DATA_DIR ?? path.join(projectRoot, "data");

function contentTypeFor(filename) {
  return mime[path.extname(filename).toLowerCase()] ?? "application/octet-stream";
}

function sendLocalFile(res, filename, source) {
  if (!existsSync(filename) || !statSync(filename).isFile()) return false;
  res.writeHead(200, {
    ...corsHeaders,
    "Content-Type": contentTypeFor(filename),
    "Content-Length": statSync(filename).size,
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Nautikos-Source": source,
  });
  createReadStream(filename).pipe(res);
  return true;
}

function integerParameter(url, name) {
  const raw = url.searchParams.get(name);
  if (raw === null || raw.trim() === "") return Number.NaN;
  return Number(raw);
}

const backend = await startProdServer({
  port: innerPort,
  host: upstreamHost,
  outDir: path.join(projectRoot, "dist"),
  purpose: "internal",
});

const server = createServer((req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, corsHeaders).end();
    return;
  }

  const rawPath = (req.url ?? "/").split("?")[0];
  if (rawPath === "/health") {
    res.writeHead(200, { ...corsHeaders, "Content-Type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ status: "ok", service: "nautikos", dataMode: "local" }));
    return;
  }

  // Jupyter is the authoritative data backend.  Serve immutable local
  // products here, before the application router, so a stale framework build
  // can never make the map fall back to white placeholders or another scene.
  if (req.method === "GET" && rawPath === "/api/sentinel/process") {
    const url = new URL(req.url ?? rawPath, `http://${host}:${outerPort}`);
    const year = integerParameter(url, "year");
    const month = integerParameter(url, "month");
    const layer = url.searchParams.get("layer") ?? "true-color";
    const z = integerParameter(url, "z");
    const x = integerParameter(url, "x");
    const y = integerParameter(url, "y");
    const candidates = [];
    if (Number.isInteger(year) && Number.isInteger(z) && Number.isInteger(x) && Number.isInteger(y)) {
      for (const extension of ["webp", "jpg", "png"]) {
        candidates.push(path.join(localDataRoot, "tiles", layer, String(year), String(z), String(x), `${y}.${extension}`));
      }
    } else if (Number.isInteger(year) && Number.isInteger(month)) {
      candidates.push(path.join(projectRoot, "public", "overviews", "monthly", String(year), `${String(month).padStart(2, "0")}.webp`));
    } else if (Number.isInteger(year)) {
      candidates.push(path.join(projectRoot, "public", "overviews", "annual", String(year), `${layer}.webp`));
    }
    for (const candidate of candidates) {
      if (sendLocalFile(res, candidate, "JUPYTER-LOCAL")) return;
    }
    if (Number.isInteger(year)) {
      res.writeHead(404, { ...corsHeaders, "Cache-Control": "no-store", "X-Nautikos-Source": "LOCAL-MISS" }).end();
      return;
    }
  }

  if (req.method === "GET" && rawPath === "/api/basemap") {
    const url = new URL(req.url ?? rawPath, `http://${host}:${outerPort}`);
    const z = integerParameter(url, "z");
    const x = integerParameter(url, "x");
    const y = integerParameter(url, "y");
    if (![z, x, y].every(Number.isInteger) || z < 3 || z > 16 || x < 0 || y < 0) {
      res.writeHead(400, { ...corsHeaders, "Content-Type": "application/json; charset=utf-8" });
      res.end(JSON.stringify({ error: "Valid z/x/y are required" }));
      return;
    }
    const filename = path.join(localDataRoot, "tiles", "basemap", String(z), String(x), `${y}.jpg`);
    if (sendLocalFile(res, filename, "JUPYTER-LOCAL-BASEMAP")) return;
    const upstream = proxyRequest({
      protocol: "http:",
      hostname: "server.arcgisonline.com",
      port: 80,
      path: `/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`,
      method: "GET",
      headers: { "user-agent": "Nautikos-Caspian/1.0" },
    }, (upstreamResponse) => {
      if (upstreamResponse.statusCode !== 200) {
        res.writeHead(upstreamResponse.statusCode ?? 502, corsHeaders);
        upstreamResponse.pipe(res);
        return;
      }
      const chunks = [];
      upstreamResponse.on("data", (chunk) => chunks.push(chunk));
      upstreamResponse.on("end", () => {
        const bytes = Buffer.concat(chunks);
        mkdirSync(path.dirname(filename), { recursive: true });
        writeFile(filename, bytes, () => {});
        res.writeHead(200, {
          ...corsHeaders,
          "Content-Type": bytes[0] === 0x89 && bytes[1] === 0x50 ? "image/png" : "image/jpeg",
          "Content-Length": bytes.length,
          "Cache-Control": "public, max-age=31536000, immutable",
          "X-Nautikos-Source": "JUPYTER-CACHED-ON-DEMAND",
        });
        res.end(bytes);
      });
    });
    upstream.on("error", () => res.writeHead(502, corsHeaders).end("Satellite basemap unavailable"));
    upstream.end();
    return;
  }
  let decodedPath;
  try {
    decodedPath = decodeURIComponent(rawPath);
  } catch {
    res.writeHead(400).end("Bad request");
    return;
  }

  const staticPath = path.resolve(clientRoot, `.${decodedPath}`);
  const insideClient = staticPath.startsWith(`${clientRoot}${path.sep}`);
  if (rawPath !== "/" && insideClient && existsSync(staticPath) && statSync(staticPath).isFile()) {
    const extension = path.extname(staticPath).toLowerCase();
    res.writeHead(200, {
      ...corsHeaders,
      "Content-Type": mime[extension] ?? "application/octet-stream",
      "Cache-Control": rawPath.startsWith("/assets/")
        ? "public, max-age=31536000, immutable"
        : "public, max-age=3600",
    });
    createReadStream(staticPath).pipe(res);
    return;
  }

  const isCopernicusImage = req.method === "GET"
    && rawPath === "/api/sentinel/process"
    && (req.url ?? "").includes("year=");
  if (isCopernicusImage && process.env.NAUTIKOS_REQUEST_LOG !== "0") {
    console.log(`[imagery] ${req.url}`);
  }
  const tileCachePath = isCopernicusImage
    ? path.join(tileCacheRoot, `${createHash("sha256").update(req.url ?? "").digest("hex")}.png`)
    : null;
  if (tileCachePath && existsSync(tileCachePath)) {
    res.writeHead(200, {
      ...corsHeaders,
      "Content-Type": "image/png",
      "Content-Length": statSync(tileCachePath).size,
      "Cache-Control": "public, max-age=31536000, immutable",
      "X-Caspian-Cache": "DISK",
    });
    createReadStream(tileCachePath).pipe(res);
    return;
  }

  const upstream = proxyRequest({
    hostname: upstreamHost,
    port: innerPort,
    path: req.url,
    method: req.method,
    headers: { ...req.headers, host: `${upstreamHost}:${innerPort}` },
  }, (upstreamResponse) => {
    if (!tileCachePath) {
      res.writeHead(upstreamResponse.statusCode ?? 502, {
        ...upstreamResponse.headers,
        ...corsHeaders,
      });
      upstreamResponse.pipe(res);
      return;
    }

    const chunks = [];
    upstreamResponse.on("data", (chunk) => chunks.push(chunk));
    upstreamResponse.on("end", () => {
      const bytes = Buffer.concat(chunks);
      res.writeHead(upstreamResponse.statusCode ?? 502, {
        ...upstreamResponse.headers,
        ...corsHeaders,
        "x-caspian-disk-cache": "MISS",
      });
      res.end(bytes);
      if (upstreamResponse.statusCode === 200 && upstreamResponse.headers["content-type"]?.startsWith("image/png")) {
        writeFile(tileCachePath, bytes, () => {});
      }
    });
  });
  upstream.on("error", () => res.writeHead(502).end("Local backend unavailable"));
  req.pipe(upstream);
});

server.listen(outerPort, host, () => {
  console.log(`Nautikos: http://${host}:${outerPort}`);
});

function shutdown() {
  server.close();
  backend.server.close();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
