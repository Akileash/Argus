import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // contracts/*.schema.json live one level above web/ (validated directly by ajv).
  outputFileTracingRoot: path.join(__dirname, ".."),
  transpilePackages: ["@argus/shared"],
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
