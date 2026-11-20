import { defineConfig } from "@playwright/test";

// End-to-end tests of the DEMO build (the app with made-up cameras, as on GitHub Pages): no backend, no engine, no database.
//   npx playwright test -c playwright.demo.config.ts
// Run it with the Chromium that ships with Playwright (the official Docker image has it), or with the Chrome of the machine
// (set E2E_CHANNEL=chrome).
const PORT = 5175;
const channel = process.env.E2E_CHANNEL;

export default defineConfig({
  testDir: "./e2e-demo",
  testMatch: /.*\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, ...(channel ? { channel } : {}), trace: "retain-on-failure", screenshot: "only-on-failure" },
  outputDir: "./e2e-demo/results",
  webServer: {
    command: `npx vite --mode demo --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    timeout: 60_000,
    reuseExistingServer: false,
  },
});
