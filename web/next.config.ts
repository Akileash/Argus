import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The repo root is the tracing root: the workspace packages (../packages/shared, ../worker) and ../contracts live
  // above web/. contracts/*.schema.json are imported (so bundled), not read from disk at runtime.
  outputFileTracingRoot: path.join(__dirname, ".."),
  // Workspace packages ship TypeScript source.
  transpilePackages: ["@argus/shared", "@argus/worker"],
  // The worker's SDKs and ajv are bundled into the route (not externalized): under pnpm they are not hoisted to web/.
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
