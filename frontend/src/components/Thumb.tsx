import { ImageOff } from "lucide-react";
import { useState } from "react";

/** Snapshot thumbnail; a placeholder when the event has no image or the file is gone. */
export function Thumb({ src }: { src: string | null }) {
  const [failed, setFailed] = useState(false);
  return (
    <span className="thumb">
      {src && !failed ? <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} /> : <ImageOff size={18} />}
    </span>
  );
}
