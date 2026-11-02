import type { Camera, CameraCreate, CameraUpdate, ConnectionCheckBody } from "../types";

export interface CameraFormValues {
  name: string;
  sourceType: "rtsp" | "file";
  /** The address, preferably without a login. Empty while editing = keep the saved address. */
  sourceUri: string;
  username: string;
  password: string;
  enabled: boolean;
}

export const emptyForm = (): CameraFormValues => ({
  name: "",
  sourceType: "rtsp",
  sourceUri: "",
  username: "",
  password: "",
  enabled: true,
});

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

export type FormErrors = Partial<Record<"name" | "sourceUri" | "login", string>>;

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
  return errors;
}

export function toCreatePayload(values: CameraFormValues): CameraCreate {
  return { name: values.name.trim(), source_type: values.sourceType, source_uri: composeUri(values), enabled: values.enabled };
}

/** Only what changed. The address and its type go together (the API demands it) and only when an address was typed. */
export function toUpdatePayload(values: CameraFormValues, original: Camera): CameraUpdate {
  const body: CameraUpdate = {};
  if (values.name.trim() !== original.name) body.name = values.name.trim();
  if (values.enabled !== original.enabled) body.enabled = values.enabled;
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
});
