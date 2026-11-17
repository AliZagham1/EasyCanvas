import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The app is reachable at both http://localhost:3000 and http://127.0.0.1:3000 in development; without this,
  // Next blocks its own dev scripts on 127.0.0.1 and no page becomes interactive.
  allowedDevOrigins: ["127.0.0.1"],
  experimental: {
    agentUpgrade: "security",
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
