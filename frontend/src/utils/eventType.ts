import { Ban, Siren, Swords, TriangleAlert, UserRound, type LucideIcon } from "lucide-react";
import type { BadgeTone } from "../components/ui/Badge";
import type { EventType, PersonEvent, Severity } from "../types";

export interface EventKind {
  /** The title of the event ("Fall detected"). */
  label: string;
  /** A few words for a filter or a column ("Fall"). */
  short: string;
  icon: LucideIcon;
}

export const EVENT_KINDS: Record<EventType, EventKind> = {
  PERSON_DETECTED: { label: "Person detected", short: "Person", icon: UserRound },
  FALL: { label: "Fall detected", short: "Fall", icon: TriangleAlert },
  FIGHT: { label: "Possible fight", short: "Fight", icon: Swords },
  RESTRICTED_AREA: { label: "Restricted area", short: "Restricted area", icon: Ban },
};

/** An event of a type this app does not know yet (a newer engine) is shown as a person event rather than breaking the page. */
export const kindOf = (type: string): EventKind => EVENT_KINDS[type as EventType] ?? EVENT_KINDS.PERSON_DETECTED;

export const SEVERITY_TONE: Record<Severity, BadgeTone> = { info: "idle", medium: "warn", high: "bad" };
export const SEVERITY_LABEL: Record<Severity, string> = { info: "Info", medium: "Warning", high: "Alert" };

/** Falls and fights ask for someone to look now. */
export const isAlert = (e: Pick<PersonEvent, "severity">): boolean => e.severity === "high";

/** The filter choices of the Events page: what is shown and the event types behind it. */
export const TYPE_FILTERS: { id: string; label: string; types: EventType[] | undefined }[] = [
  { id: "all", label: "All events", types: undefined },
  { id: "alerts", label: "Alerts (falls, fights)", types: ["FALL", "FIGHT"] },
  { id: "restricted", label: "Restricted area", types: ["RESTRICTED_AREA"] },
  { id: "person", label: "Person detected", types: ["PERSON_DETECTED"] },
];

/** One line saying what happened to whom: "Person 12 fell", "Persons 3 and 7 are fighting", "Person 5 in zone 2". */
export function describeEvent(e: Pick<PersonEvent, "type" | "track_ids" | "track_id" | "zone_id">): string {
  const ids = e.track_ids.length > 0 ? e.track_ids : [e.track_id];
  const who = ids.length === 1 ? `Person ${ids[0]}` : `Persons ${ids.join(" and ")}`;
  switch (e.type) {
    case "FALL":
      return `${who} fell and has not got up`;
    case "FIGHT":
      return `${who} seem to be fighting`;
    case "RESTRICTED_AREA":
      return `${who} in restricted zone ${e.zone_id ?? "?"}`;
    default:
      return `${who} detected`;
  }
}

/** Icon for the "alerts" in a header or toast. */
export const AlertIcon = Siren;
