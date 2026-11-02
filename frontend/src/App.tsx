import { AppLayout } from "./layouts/AppLayout";
import { useHashRoute } from "./hooks/useHashRoute";
import { DashboardPage } from "./pages/DashboardPage";
import { EventsPage } from "./pages/EventsPage";
import { MonitoringPage } from "./pages/MonitoringPage";

export function App() {
  const [route, go] = useHashRoute();
  return (
    <AppLayout page={route.page} routeKey={`${route.page}/${route.cameraId ?? ""}`} go={go}>
      {route.page === "dashboard" && <DashboardPage onOpenCamera={(id) => go("monitoring", id)} />}
      {route.page === "monitoring" && <MonitoringPage cameraId={route.cameraId} onSelect={(id) => go("monitoring", id)} />}
      {route.page === "events" && <EventsPage />}
    </AppLayout>
  );
}
