import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

const DATA_ROOT = process.env.NAUTIKOS_DATA_DIR ?? "D:\\CaspianTwinData\\cube";

function optionalInteger(url: URL, name: string) {
  const raw = url.searchParams.get(name);
  if (raw === null || raw.trim() === "") return Number.NaN;
  return Number(raw);
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const z = optionalInteger(url, "z");
  const x = optionalInteger(url, "x");
  const y = optionalInteger(url, "y");
  if (![z, x, y].every(Number.isInteger) || z < 3 || z > 8 || x < 0 || y < 0) {
    return Response.json({ error: "Valid z/x/y are required" }, { status: 400 });
  }
  const fullPath = path.join(DATA_ROOT, "tiles", "basemap", String(z), String(x), `${y}.jpg`);
  if (!existsSync(fullPath)) return new Response(null, { status: 404, headers: { "cache-control": "no-store" } });
  const bytes = await readFile(fullPath);
  const contentType = bytes[0] === 0x89 && bytes[1] === 0x50 ? "image/png" : "image/jpeg";
  return new Response(bytes, {
    headers: {
      "content-type": contentType,
      "cache-control": "public, max-age=31536000, immutable",
      "x-nautikos-source": "LOCAL-REGIONAL-BASEMAP",
    },
  });
}
