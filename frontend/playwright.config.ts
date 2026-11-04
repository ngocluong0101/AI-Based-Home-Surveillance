import { defineConfig } from "@playwright/test";

// End-to-end tests: the real frontend (Vite dev server) against the real backend and engine manager, see e2e/stack.py.
// They use the Chrome that is installed on the machine, so no browser is downloaded.
const python = process.env.E2E_PYTHON ?? "python3";
const BACKEND = "http://127.0.0.1:18765";
const FRONTEND_PORT = 5174;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1, // one database, one engine
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${FRONTEND_PORT}`,
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  outputDir: "./e2e/results",
  webServer: [
    {
      command: `${python} frontend/e2e/stack.py`,
      cwd: "..",
      url: `${BACKEND}/api/health`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `npx vite --port ${FRONTEND_PORT} --strictPort`,
      env: { BACKEND_URL: BACKEND },
      url: `http://127.0.0.1:${FRONTEND_PORT}`,
      timeout: 60_000,
      reuseExistingServer: false,
    },
  ],
});
