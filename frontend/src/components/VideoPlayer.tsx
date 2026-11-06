import { useQuery } from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { Maximize2, VideoOff, WifiOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Health } from "../utils/health";
import { connectWhep } from "../utils/whep";
import { Spinner } from "./ui/Spinner";
import { ErrorState } from "./ui/States";

const RETRY_MS = 3000;

type Phase = "idle" | "connecting" | "live";

/** Live video of one camera (boxes and track IDs are drawn by the engine). Retries while the camera is expected. */
export function VideoPlayer({
  cameraId,
  shouldRun,
  health,
  chips,
  compact = false,
}: {
  cameraId: number;
  shouldRun: boolean;
  health: Health;
  /** Drawn over the picture while it is live (FPS, people). */
  chips?: React.ReactNode;
  /** A small tile (camera wall): shorter messages. */
  compact?: boolean;
}) {
  const link = useQuery({
    queryKey: ["stream", cameraId],
    queryFn: () => api.cameraStream(cameraId),
    refetchInterval: 5000,
    retry: false,
  });
  const box = useRef<HTMLDivElement>(null);
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

  let overlay: React.ReactNode = null;
  if (link.isError) {
    overlay = (
      <ErrorState
        title="Live video is unavailable"
        message={link.error instanceof Error ? link.error.message : undefined}
        onRetry={() => void link.refetch()}
        compact
      />
    );
  } else if (link.isPending) {
    overlay = <Spinner label="Loading stream" />;
  } else if (!available) {
    if (!shouldRun) {
      overlay = (
        <>
          <VideoOff size={34} />
          <strong>Camera stopped</strong>
          <span>Start it from the dashboard to see live video.</span>
        </>
      );
    } else if (health.tone === "bad") {
      overlay = (
        <>
          <WifiOff size={34} />
          <strong>Camera unavailable</strong>
          <span>{health.label}. Retrying automatically.</span>
        </>
      );
    } else {
      overlay = (
        <>
          <Spinner label="Starting" />
          <strong>Waiting for the camera</strong>
          <span>The engine is starting this camera.</span>
        </>
      );
    }
  } else if (phase !== "live") {
    overlay = error ? (
      <>
        <WifiOff size={34} />
        <strong>Could not connect to the video</strong>
        <span>{error}. Retrying...</span>
      </>
    ) : (
      <>
        <Spinner label="Connecting" />
        <span>Connecting to the stream...</span>
      </>
    );
  }

  return (
    <div className={`player ${phase === "live" ? "live" : ""} ${compact ? "compact" : ""}`} ref={box}>
      <video ref={videoRef} autoPlay muted playsInline />
      {phase === "live" && (
        <>
          {chips}
          <span className="player-tag">
            <span className="dot dot-bad dot-pulse" /> LIVE
          </span>
          <button
            className="btn btn-icon btn-sm player-fs"
            aria-label="Full screen"
            onClick={() => void box.current?.requestFullscreen?.()}
          >
            <Maximize2 size={16} />
          </button>
        </>
      )}
      <AnimatePresence>
        {overlay && (
          <motion.div
            key="overlay"
            className="player-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            {overlay}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
