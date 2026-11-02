import { useCallback, useEffect, useState } from "react";

export type Page = "dashboard" | "monitoring" | "events" | "cameras";
export const PAGES: Page[] = ["dashboard", "monitoring", "events", "cameras"];

export interface Route {
  page: Page;
  cameraId: number | null;
}

// The page and camera live in the URL hash (#/monitoring/2), so reload and back/forward work without a router.
export function parseHash(hash: string): Route {
  const [, page, id] = hash.split("/");
  const cameraId = Number(id);
  return {
    page: PAGES.find((p) => p === page) ?? "dashboard",
    cameraId: id && Number.isInteger(cameraId) ? cameraId : null,
  };
}

export function useHashRoute(): [Route, (page: Page, cameraId?: number | null) => void] {
  const [route, setRoute] = useState(() => parseHash(location.hash));
  useEffect(() => {
    const onHash = () => setRoute(parseHash(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = useCallback((page: Page, cameraId?: number | null) => {
    location.hash = cameraId ? `#/${page}/${cameraId}` : `#/${page}`;
  }, []);
  return [route, go];
}
