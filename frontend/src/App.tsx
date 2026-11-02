import { useEffect, useState } from "react";
import { Dashboard } from "./components/Dashboard";
import { Events } from "./components/Events";
import { Monitoring } from "./components/Monitoring";
import { useRealtime } from "./realtime";

type Tab = "dashboard" | "monitoring" | "events";
const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "monitoring", label: "Monitoring" },
  { id: "events", label: "Events" },
];

// The tab and camera live in the URL hash (#/monitoring/2), so reload and back/forward work without a router.
function parseHash(): { tab: Tab; cameraId: number | null } {
  const [, tab, id] = location.hash.split("/");
  const known = TABS.find((t) => t.id === tab)?.id ?? "dashboard";
  const cameraId = Number(id);
  return { tab: known, cameraId: Number.isInteger(cameraId) && id ? cameraId : null };
}

export function App() {
  const connection = useRealtime();
  const [route, setRoute] = useState(parseHash);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (tab: Tab, cameraId?: number | null) => {
    location.hash = cameraId ? `#/${tab}/${cameraId}` : `#/${tab}`;
  };

  return (
    <div className="app">
      <header className="top">
        <h1>Home Surveillance</h1>
        <nav>
          {TABS.map((t) => (
            <a
              key={t.id}
              href={`#/${t.id}`}
              className={route.tab === t.id ? "active" : undefined}
              onClick={(e) => {
                e.preventDefault();
                go(t.id, t.id === "monitoring" ? route.cameraId : null);
              }}
            >
              {t.label}
            </a>
          ))}
        </nav>
        <span className={`live live-${connection}`} title="WebSocket connection to the backend">
          {connection === "open" ? "Live" : connection === "connecting" ? "Connecting..." : "Offline, retrying"}
        </span>
      </header>
      <main>
        {route.tab === "dashboard" && <Dashboard onOpenCamera={(id) => go("monitoring", id)} />}
        {route.tab === "monitoring" && (
          <Monitoring cameraId={route.cameraId} onSelect={(id) => go("monitoring", id)} />
        )}
        {route.tab === "events" && <Events />}
      </main>
    </div>
  );
}
