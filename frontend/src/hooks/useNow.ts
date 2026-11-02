import { useEffect, useState } from "react";

/** Date.now(), refreshed every `ms`: keeps "5 min ago" labels moving. */
export function useNow(ms = 10000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}
