import path from "node:path";
import { defineConfig } from "vitest/config";

// .env.local supplies the Supabase URL/anon key to the integration test (Node >= 22).
try {
  process.loadEnvFile(path.resolve(import.meta.dirname, ".env.local"));
} catch {
  // optional
}

export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname) } },
  test: {
    environment: "node",
    // tests/integration/* self-skip unless ARGUS_INTEGRATION=1
    include: ["tests/**/*.test.ts"],
  },
});
