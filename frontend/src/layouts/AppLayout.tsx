import { AnimatePresence, motion } from "motion/react";
import { Bell, Cctv, Grid2x2, Info, LayoutDashboard, PanelLeftClose, PanelLeftOpen, Settings, ShieldCheck, UserRound, Video, WifiOff } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Toaster, toast } from "sonner";
import { DEMO, REPO_URL } from "../demo/flag";
import { EventDrawer } from "../components/EventDrawer";
import { StatusDot } from "../components/ui/StatusDot";
import { EventDrawerContext } from "../hooks/useEventDrawer";
import type { Page } from "../hooks/useHashRoute";
import { useCameras, useSettings, useUnreviewedCount } from "../hooks/queries";
import type { PersonEvent } from "../types";
import { percent } from "../utils/format";
import { useRealtime, type Connection } from "../websocket/useRealtime";
import { useQueryClient } from "@tanstack/react-query";
import type { Camera } from "../types";

const NAV: { page: Page; label: string; icon: typeof Bell }[] = [
  { page: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { page: "wall", label: "Wall", icon: Grid2x2 },
  { page: "monitoring", label: "Monitoring", icon: Video },
  { page: "events", label: "Events", icon: Bell },
  { page: "cameras", label: "Cameras", icon: Cctv },
  { page: "settings", label: "Settings", icon: Settings },
];

const CONNECTION: Record<Connection, { label: string; tone: "ok" | "warn" }> = {
  open: { label: "Live", tone: "ok" },
  connecting: { label: "Connecting...", tone: "warn" },
  closed: { label: "Reconnecting...", tone: "warn" },
};

export function AppLayout({
  page,
  routeKey,
  go,
  children,
}: {
  page: Page;
  /** Changes whenever the URL does (page or camera); an open drawer closes with it. */
  routeKey: string;
  go: (page: Page, cameraId?: number | null) => void;
  children: ReactNode;
}) {
  const qc = useQueryClient();
  useCameras(); // keeps camera names warm for toasts and lists
  useSettings(); // and what a new camera starts with, ready before the Add camera form opens
  const unreviewed = useUnreviewedCount();
  const [openId, setOpenId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("sidebar-collapsed") === "1");

  useEffect(() => setOpenId(null), [routeKey]);

  const open = useCallback((id: string) => setOpenId(id), []);
  const close = useCallback(() => setOpenId(null), []);

  const onNewEvent = useCallback(
    (event: PersonEvent) => {
      const name = qc.getQueryData<Camera[]>(["cameras"])?.find((c) => c.id === event.camera_id)?.name ?? `Camera #${event.camera_id}`;
      toast("Person detected", {
        description: `${name} · ${percent(event.confidence)} confidence`,
        icon: <UserRound size={16} />,
        action: { label: "View", onClick: () => setOpenId(event.event_id) },
      });
    },
    [qc],
  );
  const connection = useRealtime(onNewEvent);
  const conn = CONNECTION[connection];

  const toggle = () =>
    setCollapsed((c) => {
      localStorage.setItem("sidebar-collapsed", c ? "0" : "1");
      return !c;
    });

  return (
    <EventDrawerContext.Provider value={open}>
      <div className={`shell ${collapsed ? "collapsed" : ""}`}>
        <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark">
              <ShieldCheck size={18} />
            </span>
            <span className="brand-name">
              Home Surveillance
              <small>AI person detection</small>
            </span>
          </div>
          <nav className="nav" aria-label="Main">
            {NAV.map(({ page: p, label, icon: Icon }) => (
              <a
                key={p}
                href={`#/${p}`}
                className={page === p ? "active" : undefined}
                aria-current={page === p ? "page" : undefined}
                title={label}
                onClick={(e) => {
                  e.preventDefault();
                  go(p);
                }}
              >
                <Icon size={19} />
                <span className="label">{label}</span>
                {p === "events" && !!unreviewed.data && <span className="count">{unreviewed.data > 99 ? "99+" : unreviewed.data}</span>}
              </a>
            ))}
          </nav>
          <div className="sidebar-foot">
            <span className={`conn conn-${connection}`} title="WebSocket connection to the backend" role="status">
              <StatusDot tone={conn.tone} pulse={connection === "open"} />
              <span className="label">{conn.label}</span>
            </span>
            <button className="btn btn-ghost btn-sm collapse-btn" onClick={toggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
              {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
            </button>
          </div>
        </aside>

        <div className="main">
          {DEMO && (
            <div className="demo-banner" role="note">
              <Info size={15} />
              <span>Live demo with made-up cameras and people. Nothing leaves your browser; reloading starts over.</span>
              {REPO_URL && (
                <a href={REPO_URL} target="_blank" rel="noreferrer">
                  Source code
                </a>
              )}
            </div>
          )}
          <AnimatePresence>
            {connection === "closed" && (
              <motion.div className="banner" role="alert" initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}>
                <WifiOff size={16} /> Connection lost. Reconnecting; what you see may be out of date.
              </motion.div>
            )}
          </AnimatePresence>
          <AnimatePresence mode="wait" initial={false}>
            <motion.div key={page} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.16 }}>
              {children}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      <EventDrawer
        eventId={openId}
        onClose={close}
        onOpenCamera={(cameraId) => {
          close();
          go("monitoring", cameraId);
        }}
      />
      <Toaster theme="dark" position="bottom-right" visibleToasts={3} />
    </EventDrawerContext.Provider>
  );
}
