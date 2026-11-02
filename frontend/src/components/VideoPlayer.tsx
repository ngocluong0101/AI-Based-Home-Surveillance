import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { connectWhep } from "../whep";

const RETRY_MS = 3000;

type Phase = "idle" | "connecting" | "live";

/** Live video of one camera (boxes and track IDs are drawn by the engine). Retries while the camera is expected. */
export function VideoPlayer({ cameraId, shouldRun }: { cameraId: number; shouldRun: boolean }) {
  const link = useQuery({
    queryKey: ["stream", cameraId],
    queryFn: () => api.cameraStream(cameraId),
    refetchInterval: 5000,
    retry: false,
  });
  const videoRef = useRef<HTMLVideoElement>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const whepUrl = link.data?.whep_url;
  const available = link.data?.available ?? false;

  useEffect(() => {
    if (!whepUrl || !available) {
      setPhase("idle");
      return;
    }
    let cancelled = false;
    let close: (() => void) | undefined;
    let timer: number | undefined;
    const retry = () => {
      if (!cancelled) timer = window.setTimeout(() => setAttempt((n) => n + 1), RETRY_MS);
    };

    setPhase("connecting");
    setError(null);
    connectWhep(whepUrl, () => {
      setPhase("connecting");
      retry();
    }).then(
      (session) => {
        if (cancelled) return session.close();
        close = session.close;
        if (videoRef.current) videoRef.current.srcObject = session.stream;
        setPhase("live");
      },
      (e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        retry();
      },
    );

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      close?.();
      if (videoRef.current) videoRef.current.srcObject = null;
    };
  }, [whepUrl, available, attempt]);

  let message: string | null = null;
  if (link.isError) message = link.error instanceof Error ? link.error.message : "Live video is unavailable.";
  else if (!available) message = shouldRun ? "Waiting for the camera to start streaming..." : "The camera is stopped.";
  else if (phase !== "live") message = error ? `Connecting failed (${error}); retrying...` : "Connecting...";

  return (
    <div className="player">
      <video ref={videoRef} autoPlay muted playsInline />
      {message && <div className="player-overlay">{message}</div>}
    </div>
  );
}
