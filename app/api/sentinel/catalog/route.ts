type BBox = [number, number, number, number];

type CatalogRequest = {
  bbox?: BBox;
  collection?: "sentinel-1-grd" | "sentinel-2-l2a" | "sentinel-3-olci";
  from?: string;
  to?: string;
};

type CatalogFeature = {
  id: string;
  bbox?: BBox;
  geometry?: { type: string; coordinates: unknown };
  properties: {
    datetime?: string;
    "eo:cloud_cover"?: number;
    "sat:orbit_state"?: string;
    "sat:relative_orbit"?: number;
    constellation?: string;
  };
};

const TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token";
const CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search";

function validBBox(value: unknown): value is BBox {
  return Array.isArray(value) && value.length === 4 && value.every((entry) => typeof entry === "number" && Number.isFinite(entry));
}

async function accessToken(clientId: string, clientSecret: string) {
  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "client_credentials", client_id: clientId, client_secret: clientSecret }),
  });
  if (!response.ok) throw new Error(`OAuth failed: ${response.status}`);
  return (await response.json() as { access_token: string }).access_token;
}

export async function POST(request: Request) {
  const clientId = process.env.CDSE_CLIENT_ID;
  const clientSecret = process.env.CDSE_CLIENT_SECRET;
  if (!clientId || !clientSecret) return Response.json({ error: "Copernicus OAuth is not configured" }, { status: 503 });

  const input = await request.json() as CatalogRequest;
  if (!validBBox(input.bbox) || !input.collection || !input.from || !input.to) {
    return Response.json({ error: "bbox, collection, from and to are required" }, { status: 400 });
  }

  const center = [(input.bbox[0] + input.bbox[2]) / 2, (input.bbox[1] + input.bbox[3]) / 2];
  try {
    const token = await accessToken(clientId, clientSecret);
    const response = await fetch(CATALOG_URL, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify({
        collections: [input.collection],
        datetime: `${input.from}T00:00:00Z/${input.to}T23:59:59Z`,
        intersects: { type: "Point", coordinates: center },
        limit: 50,
      }),
    });
    if (!response.ok) return Response.json({ error: "Catalog search failed", detail: await response.text() }, { status: response.status });

    const payload = await response.json() as { features?: CatalogFeature[] };
    const features = payload.features ?? [];
    features.sort((a, b) => {
      if (input.collection === "sentinel-2-l2a") {
        const cloudA = a.properties["eo:cloud_cover"] ?? 100;
        const cloudB = b.properties["eo:cloud_cover"] ?? 100;
        if (cloudA !== cloudB) return cloudA - cloudB;
      }
      return (b.properties.datetime ?? "").localeCompare(a.properties.datetime ?? "");
    });

    return Response.json({
      mode: "single-scene",
      center,
      scenes: features.slice(0, 50).map((feature) => ({
        id: feature.id,
        datetime: feature.properties.datetime,
        bbox: feature.bbox,
        geometry: feature.geometry,
        cloud: feature.properties["eo:cloud_cover"] ?? null,
        orbitState: feature.properties["sat:orbit_state"] ?? null,
        relativeOrbit: feature.properties["sat:relative_orbit"] ?? null,
        constellation: feature.properties.constellation ?? null,
      })),
    });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Catalog search failed" }, { status: 502 });
  }
}
