/**
 * Demo mode: the web app runs on its own, with made-up cameras and people, and no backend. Used for the public demo page
 * (GitHub Pages). Switched on by the Vite mode `demo` (`npm run build:demo`, `npm run dev:demo`); off in every other build.
 */
export const DEMO: boolean = import.meta.env.VITE_DEMO === "true";

/** Where the "source code" link of the demo banner goes. */
export const REPO_URL: string = import.meta.env.VITE_REPO_URL ?? "";
