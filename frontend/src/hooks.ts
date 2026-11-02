import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type EventFilters, type LiveStatus } from "./api";
import { healthOf, lastErrorOf, streamOf } from "./health";
import { liveKey } from "./realtime";

const STATUS_POLL_MS = 5000;

export const useCameras = () => useQuery({ queryKey: ["cameras"], queryFn: api.cameras, refetchInterval: 30000 });

/** Polled status merged with the pushed camera.status for the same camera. */
export function useCameraHealth(cameraId: number) {
  const status = useQuery({
    queryKey: ["status", cameraId],
    queryFn: () => api.cameraStatus(cameraId),
    refetchInterval: STATUS_POLL_MS,
  });
  const live = useQuery<LiveStatus>({
    queryKey: liveKey(cameraId),
    queryFn: () => Promise.reject(new Error("pushed by the WebSocket only")),
    enabled: false,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const stream = streamOf(status.data, live.data);
  return {
    status: status.data,
    stream,
    health: healthOf(status.data, stream),
    lastError: lastErrorOf(status.data, stream),
    loading: status.isPending,
  };
}

export const useEvents = (filters: EventFilters) =>
  useQuery({
    queryKey: ["events", filters],
    queryFn: () => api.events(filters),
    placeholderData: keepPreviousData,
  });

/** Start of the local day, as ISO. */
export function startOfToday(now = new Date()): string {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
}

export function useEventsToday(cameraId?: number) {
  return useQuery({
    queryKey: ["eventsToday", cameraId ?? null],
    queryFn: () => api.events({ cameraId, from: startOfToday(), limit: 1, offset: 0 }),
    refetchInterval: 60000,
    select: (page) => page.total,
  });
}

export function useCameraControl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, run }: { id: number; run: boolean }) => (run ? api.startCamera(id) : api.stopCamera(id)),
    onSuccess: (status) => {
      qc.setQueryData(["status", status.camera_id], status);
      void qc.invalidateQueries({ queryKey: ["cameras"] });
    },
  });
}

export function useAcknowledge() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: "new" | "acknowledged" }) => api.acknowledge(id, status),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["events"] }),
  });
}
