import type { NextConfig } from "next";

const apiBase = process.env.NAUTIKOS_API_BASE_URL?.replace(/\/$/, "");

const nextConfig: NextConfig = {
  async rewrites() {
    if (!apiBase) return [];
    return [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }];
  },
};

export default nextConfig;
