import { keepPreviousData, useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Camera, CameraStatus, CameraUpdate, EventFilters, EventStatus, LiveStatus, PersonEvent } from "../types";
import { healthOf, lastErrorOf, streamOf, type Health } from "../utils/health";
import { liveKey } from "../websocket/messages";

const STATUS_POLL_MS = 5000;

export const useCameras = () =>
  useQuery({ queryKey: ["cameras"], queryFn: api.cameras, refetchInterval: 30000 });

/** The engine as a whole (places, whether the latest list is applied). Poll often: it changes after every camera edit. */
export const useEngine = () => useQuery({ queryKey: ["engine"], queryFn: api.engine, refetchInterval: 5000 });

export interface FleetItem {
  camera: Camera;
  status: CameraStatus | undefined;
  stream: ReturnType<typeof streamOf>;
  health: Health;
  lastError: string | null;
  /** When the status shown was received (ms since epoch), 0 if never. */
  updatedAt: number;
}

/**
 * Every camera with its health: the polled /status merged with the pushed camera.status.
 * One hook so the dashboard counts, the camera list and the cards all agree.
 */
export function useFleet(cameras: Camera[] | undefined): FleetItem[] {
  const list = cameras ?? [];
  const statuses = useQueries({
    queries: list.map((c) => ({
      queryKey: ["status", c.id],
      queryFn: () => api.cameraStatus(c.id),
      refetchInterval: STATUS_POLL_MS,
    })),
  });
  const lives = useQueries({
    queries: list.map((c) => ({
      queryKey: liveKey(c.id),
      queryFn: (): Promise<LiveStatus> => Promise.reject(new Error("pushed by the WebSocket only")),
      enabled: false,
      staleTime: Infinity,
      gcTime: Infinity,
    })),
  });
  return list.map((camera, i) => {
    const status = statuses[i]?.data;
    const live = lives[i]?.data;
    const stream = streamOf(status, live);
    return {
      camera,
      status,
      stream,
      health: healthOf(status, stream),
      lastError: lastErrorOf(status, stream),
      updatedAt: Math.max(live ? (lives[i]?.dataUpdatedAt ?? 0) : 0, statuses[i]?.dataUpdatedAt ?? 0),
    };
  });
}

export const useEvents = (filters: EventFilters) =>
  useQuery({
    queryKey: ["events", filters],
    queryFn: () => api.events(filters),
    placeholderData: keepPreviousData,
  });

/**
 * The newest snapshot of a camera (and when it was taken): the cover of its card.
 * Looks at the last few events because the newest one may have no image (the file was cleaned up or never written).
 * Same query key family as the event lists, so a new event refreshes it.
 */
export function useLatestSnapshot(cameraId: number, enabled = true): { url: string | null; at: string | null; pending: boolean } {
  const q = useQuery({
    queryKey: ["events", { cameraId, limit: 3, offset: 0 }],
    queryFn: () => api.events({ cameraId, limit: 3, offset: 0 }),
    enabled,
    placeholderData: keepPreviousData,
  });
  const items = q.data?.items ?? [];
  const withImage = items.find((e) => e.snapshot_url);
  return { url: withImage?.snapshot_url ?? null, at: (withImage ?? items[0])?.timestamp ?? null, pending: q.isPending && enabled };
}

/** One event, for the detail drawer: seeded from the lists, kept fresh by event.updated. */
export function useEvent(eventId: string | null) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["event", eventId],
    queryFn: () => api.event(eventId!),
    enabled: eventId !== null,
    initialData: () => {
      for (const [, page] of qc.getQueriesData<{ items: PersonEvent[] }>({ queryKey: ["events"] })) {
        const hit = page?.items?.find((e) => e.event_id === eventId);
        if (hit) return hit;
      }
      return undefined;
    },
  });
}

/** Start of the local day, as ISO. */
export function startOfToday(now = new Date()): string {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
}

const todayQuery = (cameraId?: number) => ({
  queryKey: ["eventsToday", cameraId ?? null],
  queryFn: () => api.events({ cameraId, from: startOfToday(), limit: 1, offset: 0 }),
  refetchInterval: 60000,
  select: (page: { total: number }) => page.total,
});

export const useEventsToday = (cameraId?: number) => useQuery(todayQuery(cameraId));

/** Events today for each camera, in the order of `cameras` (undefined while loading). */
export function useEventsTodayByCamera(cameras: Camera[]): (number | undefined)[] {
  return useQueries({ queries: cameras.map((c) => todayQuery(c.id)) }).map((q) => q.data);
}

export const useUnreviewedCount = () =>
  useQuery({
    queryKey: ["eventsCount", "new"],
    queryFn: () => api.events({ status: "new", limit: 1, offset: 0 }),
    select: (page) => page.total,
  });

export function useCameraControl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, run }: { id: number; run: boolean }) => (run ? api.startCamera(id) : api.stopCamera(id)),
    onSuccess: (status) => {
      qc.setQueryData(["status", status.camera_id], status);
      void qc.invalidateQueries({ queryKey: ["cameras"] });
      void qc.invalidateQueries({ queryKey: ["engine"] });
    },
  });
}

export function useSetEventStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: EventStatus }) => api.setEventStatus(id, status),
    onSuccess: (event) => {
      qc.setQueryData(["event", event.event_id], event);
      void qc.invalidateQueries({ queryKey: ["events"] });
      void qc.invalidateQueries({ queryKey: ["eventsCount"] });
    },
  });
}

/** id -> camera name, for lists of events. Falls back to "Camera #id" (deleted or not loaded yet). */
export function useCameraName(): (id: number) => string {
  const { data } = useCameras();
  const names = new Map((data ?? []).map((c) => [c.id, c.name]));
  return (id) => names.get(id) ?? `Camera #${id}`;
}

function useInvalidateCameras() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ["cameras"] });
    void qc.invalidateQueries({ queryKey: ["status"] });
    void qc.invalidateQueries({ queryKey: ["engine"] });
  };
}

export function useCreateCamera() {
  const refresh = useInvalidateCameras();
  return useMutation({ mutationFn: api.createCamera, onSuccess: refresh });
}

export function useUpdateCamera() {
  const refresh = useInvalidateCameras();
  return useMutation({
    mutationFn: ({ id, body }: { id: number; body: CameraUpdate }) => api.updateCamera(id, body),
    onSuccess: refresh,
  });
}

export function useDeleteCamera() {
  const qc = useQueryClient();
  const refresh = useInvalidateCameras();
  return useMutation({
    mutationFn: (id: number) => api.deleteCamera(id),
    onSuccess: (_, id) => {
      qc.removeQueries({ queryKey: ["status", id] });
      qc.removeQueries({ queryKey: liveKey(id) });
      refresh();
    },
  });
}

export const useTestConnection = () => useMutation({ mutationFn: api.testConnection });
