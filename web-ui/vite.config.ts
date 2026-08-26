import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    proxy: {
      // The FastAPI shell (jobfeed serve) owns /api; loopback only per D2.
      "/api": `http://127.0.0.1:${process.env.JOBFEED_DEV_API_PORT ?? "7654"}`,
    },
  },
  build: {
    // Default outDir (web-ui/dist) — matches _DEFAULT_DIST_DIR in web/app.py.
    sourcemap: true,
  },
  test: {
    environment: "happy-dom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
  },
});
