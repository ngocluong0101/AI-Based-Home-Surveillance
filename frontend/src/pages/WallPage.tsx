import { ArrowLeft, Camera as CameraIcon, ChevronLeft, ChevronRight, Maximize2, Play } from "lucide-react";
import { useState } from "react";
import { CoverImage } from "../components/CoverImage";
import { LiveChips } from "../components/LiveChips";
import { VideoPlayer } from "../components/VideoPlayer";
import { Segmented } from "../components/ui/Segmented";
import { Skeleton } from "../components/ui/Skeleton";
import { StatusDot } from "../components/ui/StatusDot";
import { EmptyState, ErrorState } from "../components/ui/States";
import { useCameras, useFleet, useLatestSnapshot, type FleetItem } from "../hooks/queries";
import { useNow } from "../hooks/useNow";
import { relativeTime } from "../utils/format";

/** Every live tile is a full video stream to the browser, so only this many play at once; the others show their last snapshot. */
export const LIVE_LIMIT = 4;

type Size = "1" | "2" | "3"; // tiles per row (and column)
const SIZE_KEY = "wall-size";
const savedSize = (): Size => {
  const v = localStorage.getItem(SIZE_KEY);
  return v === "1" || v === "3" ? v : "2";
};

const isRunning = (f: FleetItem): boolean => f.status?.should_run ?? f.camera.desired_state === "running";

function WallTile({
  item,
  live,
  big,
  onFocus,
  onGoLive,
}: {
  item: FleetItem;
  live: boolean;
  big: boolean;
  onFocus: () => void;
  onGoLive: () => void;
}) {
  const { camera, stream, health } = item;
  const running = isRunning(item);
  const latest = useLatestSnapshot(camera.id, !live);
  const now = useNow();
  return (
    <figure className="tile">
      {live ? (
        <VideoPlayer cameraId={camera.id} shouldRun={running} health={health} compact={!big} />
      ) : (
        <div className="player snap">
          <CoverImage src={latest.url} dim />
          <div className="snap-over">
            <span className="snap-note">
              {!running ? "Camera stopped" : latest.at ? `Last detection ${relativeTime(latest.at, now)}` : "No detections yet"}
            </span>
            {running && (
              <button className="btn btn-sm" onClick={onGoLive} aria-label={`Go live: ${camera.name}`}>
                <Play size={14} aria-hidden="true" /> Go live
              </button>
            )}
          </div>
        </div>
      )}
      <figcaption className="tile-cap">
        <StatusDot tone={health.tone} pulse={health.tone === "ok"} />
        <span className="name">{camera.name}</span>
        <LiveChips stream={stream} />
        {!big && (
          <button className="btn btn-ghost btn-icon btn-sm" aria-label={`Focus ${camera.name}`} onClick={onFocus}>
            <Maximize2 size={15} aria-hidden="true" />
          </button>
        )}
      </figcaption>
    </figure>
  );
}

/** All cameras at once: a grid of tiles, a few of them live, the rest showing the last snapshot. */
export function WallPage({ focusId, onFocus }: { focusId: number | null; onFocus: (id: number | null) => void }) {
  const cameras = useCameras();
  const fleet = useFleet(cameras.data);
  const [size, setSizeState] = useState<Size>(savedSize);
  const [pageIndex, setPageIndex] = useState(0);
  const [picked, setPicked] = useState<number[] | null>(null); // what the user chose to watch live; null = the first running ones

  const setSize = (s: Size) => {
    localStorage.setItem(SIZE_KEY, s);
    setSizeState(s);
    setPageIndex(0);
  };

  const focused = focusId === null ? undefined : fleet.find((f) => f.camera.id === focusId);
  const cols = focused ? 1 : Number(size);
  const cells = cols * cols;
  const pages = Math.max(1, Math.ceil(fleet.length / cells));
  const current = Math.min(pageIndex, pages - 1);
  const visible = focused ? [focused] : fleet.slice(current * cells, (current + 1) * cells);

  const defaults = visible.filter(isRunning).slice(0, LIVE_LIMIT).map((f) => f.camera.id);
  const liveIds = picked ?? defaults;
  const goLive = (id: number) => setPicked([...liveIds.filter((x) => x !== id).slice(-(LIVE_LIMIT - 1)), id]);
  const overLimit = !focused && visible.filter(isRunning).length > LIVE_LIMIT;

  let content;
  if (cameras.isPending) {
    content = (
      <div className="wall" data-cols={2}>
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} style={{ aspectRatio: "16 / 9", height: "auto", borderRadius: 12 }} />
        ))}
      </div>
    );
  } else if (!cameras.data) {
    content = <ErrorState title="Failed to load cameras" message={cameras.error.message} onRetry={() => void cameras.refetch()} />;
  } else if (cameras.data.length === 0) {
    content = (
      <EmptyState icon={CameraIcon} title="No cameras found" hint="Add a camera to see it on the wall.">
        <a className="btn btn-primary" href="#/cameras">Add a camera</a>
      </EmptyState>
    );
  } else {
    content = (
      <>
        {focused && (
          <div className="wall-focus-bar">
            <button className="btn btn-sm" onClick={() => onFocus(null)}>
              <ArrowLeft size={15} aria-hidden="true" /> Back to wall
            </button>
            <a className="link" href={`#/monitoring/${focused.camera.id}`}>Open in Monitoring</a>
          </div>
        )}
        <div className="wall" data-cols={cols}>
          {visible.map((item) => (
            <WallTile
              key={item.camera.id}
              item={item}
              live={focused ? true : liveIds.includes(item.camera.id)}
              big={!!focused}
              onFocus={() => onFocus(item.camera.id)}
              onGoLive={() => goLive(item.camera.id)}
            />
          ))}
        </div>
        {overLimit && (
          <p className="muted wall-hint">
            Up to {LIVE_LIMIT} cameras play live at once to save bandwidth. Press Go live on another to switch to it.
          </p>
        )}
        {!focused && pages > 1 && (
          <div className="pager card">
            <span className="muted">
              Page {current + 1} of {pages}
            </span>
            <div className="btns">
              <button className="btn btn-sm" disabled={current === 0} onClick={() => setPageIndex(current - 1)}>
                <ChevronLeft size={15} aria-hidden="true" /> Previous
              </button>
              <button className="btn btn-sm" disabled={current + 1 >= pages} onClick={() => setPageIndex(current + 1)}>
                Next <ChevronRight size={15} aria-hidden="true" />
              </button>
            </div>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Wall</h1>
          <p>Every camera at a glance. Press the expand button on a tile to look closer.</p>
        </div>
        {!focused && (
          <Segmented
            label="Tiles per row"
            value={size}
            onChange={setSize}
            options={[
              { value: "1", label: "1×1" },
              { value: "2", label: "2×2" },
              { value: "3", label: "3×3" },
            ]}
          />
        )}
      </div>
      {content}
    </div>
  );
}
