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
  if (![z, x, y].every(Number.isInteger) || z < 3 || z > 16 || x < 0 || y < 0) {
    return Response.json({ error: "Valid z/x/y are required" }, { status: 400 });
  }
  // Keep this route serverless-safe. It is only the surrounding regional
  // surface; all Caspian measurements and detailed imagery come from the
  // versioned local Sentinel archive on the Jupyter data server.
  const upstream = await fetch(
    `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`,
    { headers: { accept: "image/avif,image/webp,image/jpeg,image/*" } },
  );
  if (!upstream.ok) return new Response(null, { status: upstream.status, headers: { "cache-control": "no-store" } });
  const headers = new Headers(upstream.headers);
  headers.set("cache-control", "public, s-maxage=2592000, stale-while-revalidate=604800");
  headers.set("x-nautikos-source", "REGIONAL-SATELLITE-CONTEXT");
  // Buffer the small 256px JPEG before returning it.  Some serverless
  // runtimes cannot pass an upstream Web ReadableStream through their edge
  // response adapter and turn an otherwise valid Esri 200 into a generic
  // platform 500.
  const body = await upstream.arrayBuffer();
  return new Response(body, {
    headers: {
      "content-type": headers.get("content-type") ?? "image/jpeg",
      "cache-control": headers.get("cache-control")!,
      "x-nautikos-source": headers.get("x-nautikos-source")!,
    },
  });
}
