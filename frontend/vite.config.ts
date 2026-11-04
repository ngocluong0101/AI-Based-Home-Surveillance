import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser talks to one origin; the dev server forwards /api (REST and the WebSocket) to the backend.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: backend, ws: true, changeOrigin: false },
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] }, // e2e/*.spec.ts belong to Playwright
});
