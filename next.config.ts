import type { NextConfig } from "next";

// Keep Next.js API routes on the Vercel application. Satellite tiles are
// requested from NEXT_PUBLIC_NAUTIKOS_DATA_URL by the map itself, while local
// routes such as /api/sentinel/trend, /api/ai/analyze and /api/basemap must be
// executed by Next.js. Rewriting every /api/* request to the tile service made
// those product routes return FastAPI 404 responses in production.
const nextConfig: NextConfig = {};

export default nextConfig;
