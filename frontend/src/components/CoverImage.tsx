import { ImageOff } from "lucide-react";
import { useState } from "react";

/** A snapshot filling its box (cover); a placeholder when there is no image or the file is gone. */
export function CoverImage({ src, dim = false }: { src: string | null | undefined; dim?: boolean }) {
  const [failed, setFailed] = useState<string | null>(null);
  const broken = failed === src; // a new address gets a new chance
  return (
    <span className={`cover-img ${dim ? "dim" : ""}`}>
      {src && !broken ? <img src={src} alt="" loading="lazy" onError={() => setFailed(src)} /> : <ImageOff size={22} aria-hidden="true" />}
    </span>
  );
}
