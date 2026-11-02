import { useSyncExternalStore } from "react";

// Which events arrived over the WebSocket: used to drop duplicates and to highlight new rows for a moment.

const SEEN_LIMIT = 500;
export const HIGHLIGHT_MS = 6000;

const seen = new Set<string>();
let highlighted: ReadonlySet<string> = new Set();
const listeners = new Set<() => void>();
const timers = new Map<string, ReturnType<typeof setTimeout>>();

const emit = () => listeners.forEach((l) => l());

/** True the first time an event id is seen (false for a duplicate message); starts its highlight. */
export function markCreated(eventId: string): boolean {
  if (seen.has(eventId)) return false;
  seen.add(eventId);
  if (seen.size > SEEN_LIMIT) seen.delete(seen.values().next().value as string);

  highlighted = new Set(highlighted).add(eventId);
  timers.set(
    eventId,
    setTimeout(() => {
      const next = new Set(highlighted);
      next.delete(eventId);
      highlighted = next;
      timers.delete(eventId);
      emit();
    }, HIGHLIGHT_MS),
  );
  emit();
  return true;
}

export function resetRecentEvents(): void {
  seen.clear();
  timers.forEach(clearTimeout);
  timers.clear();
  highlighted = new Set();
  emit();
}

/** Ids of the events that arrived in the last few seconds. */
export function useHighlighted(): ReadonlySet<string> {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => highlighted,
  );
}
