import type { BehaviorName, Camera, CameraCreate, CameraUpdate, ConnectionCheckBody, FallNet } from "../types";
import { sameZones, zoneErrors, zonesOf, type ZoneDraft } from "./zones";

export interface CameraFormValues {
  name: string;
  sourceType: "rtsp" | "file";
  /** The address, preferably without a login. Empty while editing = keep the saved address. */
  sourceUri: string;
  username: string;
  password: string;
  enabled: boolean;
  /** Detection settings. The numbers stay text while they are typed and are checked by validateForm. */
  aiEnabled: boolean;
  confThreshold: string;
  absenceTimeout: string;
  /** Which rules run on this camera, and the zones they use (ROADMAP_V2 V2-3). */
  behaviors: BehaviorName[];
  zones: ZoneDraft[];
  /** What decides a fall: the rules, the skeleton network, or either. */
  fallNet: FallNet;
  /** The network's thresholds; text while they are typed (checked by validateForm). */
  fallNetThr: string;
  fallNetK: string;
}

/** What a new camera starts with: what the Settings page says (`defaults`), else 0.25 (what the detector produces anyway) and 10 s. */
export const emptyForm = (defaults?: { default_conf_threshold: number; default_absence_timeout: number }): CameraFormValues => ({
  name: "",
  sourceType: "rtsp",
  sourceUri: "",
  username: "",
  password: "",
  enabled: true,
  aiEnabled: true,
  confThreshold: String(defaults?.default_conf_threshold ?? 0.25),
  absenceTimeout: String(defaults?.default_absence_timeout ?? 10),
  behaviors: [],
  zones: [],
  fallNet: 0,
  fallNetThr: "0.95",
  fallNetK: "3",
});

const asNumber = (text: string): number => (text.trim() === "" ? NaN : Number(text));

const SCHEMES = { rtsp: ["rtsp://", "rtsps://"], file: ["file://"] } as const;

/** The address with the username and password from the form put into it (percent-encoded). */
export function composeUri(values: CameraFormValues): string {
  const uri = values.sourceUri.trim();
  if (values.sourceType !== "rtsp" || (!values.username && !values.password)) return uri;
  const match = /^(rtsps?:\/\/)(.*)$/i.exec(uri);
  if (!match) return uri;
  const login = encodeURIComponent(values.username) + (values.password ? `:${encodeURIComponent(values.password)}` : "");
  return `${match[1]}${login}@${match[2]}`;
}

export type FormErrors = Partial<Record<"name" | "sourceUri" | "login" | "confThreshold" | "absenceTimeout" | "zones" | "fallNetThr" | "fallNetK", string>>;

/** What is wrong with the form, field by field; empty = fine. `original` is the camera being edited. */
export function validateForm(values: CameraFormValues, original?: Camera): FormErrors {
  const errors: FormErrors = {};
  const name = values.name.trim();
  if (!name) errors.name = "Give the camera a name.";
  else if (name.length > 100) errors.name = "The name can have at most 100 characters.";

  const uri = values.sourceUri.trim();
  const hasLoginFields = values.sourceType === "rtsp" && (values.username !== "" || values.password !== "");
  if (!uri) {
    if (!original) errors.sourceUri = values.sourceType === "rtsp" ? "Enter the camera address (rtsp://...)." : "Enter the file path (file:///...).";
    else if (original.source_type !== values.sourceType) errors.sourceUri = "Enter the address of the new source.";
    else if (hasLoginFields) errors.sourceUri = "To change the login, enter the whole address too (the saved one cannot be shown).";
  } else {
    if (!SCHEMES[values.sourceType].some((s) => uri.toLowerCase().startsWith(s))) {
      errors.sourceUri = `A ${values.sourceType === "rtsp" ? "camera" : "file"} address starts with ${SCHEMES[values.sourceType].join(" or ")}`;
    } else if (hasLoginFields && /^rtsps?:\/\/[^/]*@/i.test(uri)) {
      errors.login = "The address already has a login. Use the address or the fields, not both.";
    }
  }

  const conf = asNumber(values.confThreshold);
  if (!(conf >= 0 && conf <= 1)) errors.confThreshold = "Enter a number from 0 to 1.";
  const absence = asNumber(values.absenceTimeout);
  if (!(absence >= 0 && absence <= 86400)) errors.absenceTimeout = "Enter a number of seconds from 0 to 86400.";

  if (values.fallNet !== 0) {
    const thr = asNumber(values.fallNetThr);
    if (!(thr >= 0 && thr <= 1)) errors.fallNetThr = "Enter a number from 0 to 1.";
    const k = asNumber(values.fallNetK);
    if (!(Number.isInteger(k) && k >= 1 && k <= 100)) errors.fallNetK = "Enter a whole number from 1 to 100.";
  }

  const zoneProblems = zoneErrors(values.zones);
  if (zoneProblems.length > 0) errors.zones = zoneProblems[0];
  else if (values.behaviors.includes("zones") && !values.zones.some((z) => z.kind === "restricted")) {
    errors.zones = "Restricted-area alerts need at least one restricted zone. Draw one, or switch the alert off.";
  }
  return errors;
}

export function toCreatePayload(values: CameraFormValues): CameraCreate {
  return {
    name: values.name.trim(),
    source_type: values.sourceType,
    source_uri: composeUri(values),
    enabled: values.enabled,
    ai_enabled: values.aiEnabled,
    conf_threshold: asNumber(values.confThreshold),
    absence_timeout: asNumber(values.absenceTimeout),
    behaviors: values.behaviors,
    zones: values.zones,
    fall_net: values.fallNet,
    fall_net_thr: asNumber(values.fallNetThr),
    fall_net_k: asNumber(values.fallNetK),
  };
}

/** Only what changed. The address and its type go together (the API demands it) and only when an address was typed. */
export function toUpdatePayload(values: CameraFormValues, original: Camera): CameraUpdate {
  const body: CameraUpdate = {};
  if (values.name.trim() !== original.name) body.name = values.name.trim();
  if (values.enabled !== original.enabled) body.enabled = values.enabled;
  if (values.aiEnabled !== original.ai_enabled) body.ai_enabled = values.aiEnabled;
  const conf = asNumber(values.confThreshold);
  if (conf !== original.conf_threshold) body.conf_threshold = conf;
  const absence = asNumber(values.absenceTimeout);
  if (absence !== original.absence_timeout) body.absence_timeout = absence;
  if (JSON.stringify([...values.behaviors].sort()) !== JSON.stringify([...original.behaviors].sort())) body.behaviors = values.behaviors;
  if (!sameZones(values.zones, zonesOf(original.zones))) body.zones = values.zones;
  if (values.fallNet !== original.fall_net) body.fall_net = values.fallNet;
  if (values.fallNet !== 0) {   // the thresholds only matter with the network (and are not checked otherwise)
    const thr = asNumber(values.fallNetThr);
    if (thr !== original.fall_net_thr) body.fall_net_thr = thr;
    const k = asNumber(values.fallNetK);
    if (k !== original.fall_net_k) body.fall_net_k = k;
  }
  if (values.sourceUri.trim()) {
    body.source_type = values.sourceType;
    body.source_uri = composeUri(values);
  }
  return body;
}

/** What to send to test-connection from the form, or null when there is nothing to test yet. */
export function toConnectionBody(values: CameraFormValues, original?: Camera): ConnectionCheckBody | null {
  if (values.sourceUri.trim()) return { source_type: values.sourceType, source_uri: composeUri(values) };
  if (original && original.source_type === values.sourceType) return { camera_id: original.id };
  return null;
}

export const formFromCamera = (c: Camera): CameraFormValues => ({
  name: c.name,
  sourceType: c.source_type,
  sourceUri: "",
  username: "",
  password: "",
  enabled: c.enabled,
  aiEnabled: c.ai_enabled,
  confThreshold: String(c.conf_threshold),
  absenceTimeout: String(c.absence_timeout),
  behaviors: [...c.behaviors],
  zones: zonesOf(c.zones),
  fallNet: c.fall_net,
  fallNetThr: String(c.fall_net_thr),
  fallNetK: String(c.fall_net_k),
});
