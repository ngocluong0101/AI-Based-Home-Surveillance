import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser talks to one origin; the dev server forwards /api (REST and the WebSocket) to the backend.
const backend = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const REPO_URL = process.env.VITE_REPO_URL ?? "https://github.com/ngocluong0101/AI-Based-Home-Surveillance";

export default defineConfig(({ mode }) => ({
  // The public demo (mode "demo": npm run build:demo) is served from a sub-path on GitHub Pages, so it finds its files
  // relative to the page. Setting the flag to a constant in every other mode lets the build drop the demo code there.
  base: mode === "demo" ? "./" : "/",
  define: {
    "import.meta.env.VITE_DEMO": JSON.stringify(mode === "demo" ? "true" : "false"),
    "import.meta.env.VITE_REPO_URL": JSON.stringify(mode === "demo" ? REPO_URL : ""),
  },
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": { target: backend, ws: true, changeOrigin: false },
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts"] }, // e2e/*.spec.ts belong to Playwright
}));
