import type { NextConfig } from "next";

const apiBase = process.env.NAUTIKOS_API_BASE_URL?.replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    if (!apiBase) return [];
    return {
      beforeFiles: [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }],
      afterFiles: [],
      fallback: [],
    };
  },
};

export default nextConfig;
