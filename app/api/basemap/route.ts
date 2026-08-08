import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const DATA_ROOT = process.env.NAUTIKOS_DATA_DIR ?? "D:\\CaspianTwinData\\cube";
const DATA_BACKEND = process.env.NAUTIKOS_DATA_BACKEND?.replace(/\/$/, "");

function optionalInteger(url: URL, name: string) {
  const raw = url.searchParams.get(name);
  if (raw === null || raw.trim() === "") return Number.NaN;
  return Number(raw);
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  if (DATA_BACKEND) {
    try {
      const target = new URL(`${url.pathname}${url.search}`, `${DATA_BACKEND}/`);
      const response = await fetch(target, { cache: "no-store", signal: AbortSignal.timeout(4_000) });
      if (response.ok) {
        const headers = new Headers(response.headers);
        headers.set("x-nautikos-gateway", "JUPYTER-DATA-BACKEND");
        return new Response(response.body, { status: response.status, headers });
      }
    } catch {
      // A sleeping or unavailable data server must not blank the map.  The
      // public regional imagery below is the visual fallback while verified
      // Sentinel products continue to load independently.
    }
  }
  const z = optionalInteger(url, "z");
  const x = optionalInteger(url, "x");
  const y = optionalInteger(url, "y");
  if (![z, x, y].every(Number.isInteger) || z < 3 || z > 16 || x < 0 || y < 0) {
    return Response.json({ error: "Valid z/x/y are required" }, { status: 400 });
  }
  const fullPath = path.join(DATA_ROOT, "tiles", "basemap", String(z), String(x), `${y}.jpg`);
  let bytes: Buffer;
  let source = "LOCAL-REGIONAL-BASEMAP";
  if (existsSync(fullPath)) {
    bytes = await readFile(fullPath);
  } else {
    const upstream = await fetch(
      `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`,
      { headers: { "user-agent": "Nautikos-Caspian/1.0" }, signal: AbortSignal.timeout(15_000) },
    );
    if (!upstream.ok) return new Response(null, { status: upstream.status, headers: { "cache-control": "no-store" } });
    bytes = Buffer.from(await upstream.arrayBuffer());
    source = "SATELLITE-BASEMAP-CACHED-ON-DEMAND";
    // The Jupyter deployment has persistent storage. Vercel is read-only, but
    // still benefits from the public edge-cache headers below.
    if (!process.env.VERCEL) {
      try {
        await mkdir(path.dirname(fullPath), { recursive: true });
        await writeFile(fullPath, bytes);
      } catch {
        // A cache write must never turn a valid satellite tile into an error.
      }
    }
  }
  const contentType = bytes[0] === 0x89 && bytes[1] === 0x50 ? "image/png" : "image/jpeg";
  const body = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return new Response(body, {
    headers: {
      "content-type": contentType,
      "cache-control": "public, max-age=31536000, immutable",
      "x-nautikos-source": source,
    },
  });
}
