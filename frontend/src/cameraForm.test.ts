import { describe, expect, it } from "vitest";
import { describeDetail } from "./api/client";
import { parseHash } from "./hooks/useHashRoute";
import type { Camera } from "./types";
import {
  composeUri,
  emptyForm,
  formFromCamera,
  toConnectionBody,
  toCreatePayload,
  toUpdatePayload,
  validateForm,
  type CameraFormValues,
} from "./utils/cameraForm";

const camera: Camera = {
  id: 4,
  name: "Garage",
  source_type: "rtsp",
  source_uri: "rtsp://***@10.0.0.5/stream1",
  enabled: true,
  ai_enabled: true,
  desired_state: "stopped",
  conf_threshold: 0.5,
  absence_timeout: 10,
  created_at: "",
  updated_at: "",
};

const form = (over: Partial<CameraFormValues> = {}): CameraFormValues => ({ ...emptyForm(), name: "Porch", sourceUri: "rtsp://10.0.0.9/s", ...over });

describe("composeUri", () => {
  it("leaves the address alone without a login", () => {
    expect(composeUri(form())).toBe("rtsp://10.0.0.9/s");
  });
  it("puts the login in, percent-encoded, so special characters survive", () => {
    expect(composeUri(form({ username: "admin", password: "p@ss:w/rd#?%" }))).toBe("rtsp://admin:p%40ss%3Aw%2Frd%23%3F%25@10.0.0.9/s");
  });
  it("keeps rtsps and ignores the login fields for a file", () => {
    expect(composeUri(form({ sourceUri: "rtsps://h/x", username: "u" }))).toBe("rtsps://u@h/x");
    expect(composeUri(form({ sourceType: "file", sourceUri: "file:///a.mp4", username: "u" }))).toBe("file:///a.mp4");
  });
});

describe("validateForm", () => {
  it("accepts a good new camera", () => {
    expect(validateForm(form())).toEqual({});
  });
  it("needs a name and an address for a new camera", () => {
    expect(validateForm(form({ name: "  ", sourceUri: "" }))).toMatchObject({ name: expect.any(String), sourceUri: expect.any(String) });
  });
  it("checks the address scheme against the source type", () => {
    expect(validateForm(form({ sourceUri: "http://x" })).sourceUri).toContain("rtsp://");
    expect(validateForm(form({ sourceType: "file", sourceUri: "rtsp://x" })).sourceUri).toContain("file://");
  });
  it("keeps the confidence between 0 and 1 and the timeout between 0 and a day", () => {
    expect(validateForm(form({ confThreshold: "0", absenceTimeout: "0" }))).toEqual({});
    expect(validateForm(form({ confThreshold: "1", absenceTimeout: "86400" }))).toEqual({});
    for (const bad of ["", "abc", "1.5", "-0.1", "NaN"]) {
      expect(validateForm(form({ confThreshold: bad })).confThreshold).toBeTruthy();
    }
    for (const bad of ["", "x", "-1", "86401"]) {
      expect(validateForm(form({ absenceTimeout: bad })).absenceTimeout).toBeTruthy();
    }
  });
  it("refuses a login typed twice", () => {
    expect(validateForm(form({ sourceUri: "rtsp://a:b@h/x", username: "u" })).login).toBeTruthy();
  });
  it("lets an edit keep the saved address, but not change the login or type without a new one", () => {
    expect(validateForm({ ...formFromCamera(camera) }, camera)).toEqual({});
    expect(validateForm({ ...formFromCamera(camera), password: "x" }, camera).sourceUri).toBeTruthy();
    expect(validateForm({ ...formFromCamera(camera), sourceType: "file" }, camera).sourceUri).toBeTruthy();
  });
});

describe("payloads", () => {
  it("builds the create body", () => {
    expect(toCreatePayload(form({ name: " Porch ", enabled: false }))).toEqual({
      name: "Porch",
      source_type: "rtsp",
      source_uri: "rtsp://10.0.0.9/s",
      enabled: false,
      ai_enabled: true,
      conf_threshold: 0.25,
      absence_timeout: 10,
    });
  });
  it("sends the detection settings as numbers", () => {
    expect(toCreatePayload(form({ aiEnabled: false, confThreshold: "0.6", absenceTimeout: "0.5" }))).toMatchObject({
      ai_enabled: false,
      conf_threshold: 0.6,
      absence_timeout: 0.5,
    });
  });
  it("sends a changed detection setting alone, and nothing when they are as saved", () => {
    expect(toUpdatePayload({ ...formFromCamera(camera), confThreshold: "0.7" }, camera)).toEqual({ conf_threshold: 0.7 });
    expect(toUpdatePayload({ ...formFromCamera(camera), absenceTimeout: "30", aiEnabled: false }, camera)).toEqual({
      absence_timeout: 30,
      ai_enabled: false,
    });
    expect(toUpdatePayload({ ...formFromCamera(camera), confThreshold: "0.50" }, camera)).toEqual({});   // 0.50 is 0.5
  });
  it("sends only what changed on edit", () => {
    expect(toUpdatePayload(formFromCamera(camera), camera)).toEqual({});
    expect(toUpdatePayload({ ...formFromCamera(camera), name: "Garage 2", enabled: false }, camera)).toEqual({ name: "Garage 2", enabled: false });
  });
  it("sends the type and address together, only when an address was typed", () => {
    expect(toUpdatePayload({ ...formFromCamera(camera), sourceUri: "rtsp://new/x", username: "u", password: "p" }, camera)).toEqual({
      source_type: "rtsp",
      source_uri: "rtsp://u:p@new/x",
    });
  });
  it("tests the typed address, or the saved camera when nothing was typed", () => {
    expect(toConnectionBody(form())).toEqual({ source_type: "rtsp", source_uri: "rtsp://10.0.0.9/s" });
    expect(toConnectionBody(formFromCamera(camera), camera)).toEqual({ camera_id: 4 });
    expect(toConnectionBody(emptyForm())).toBeNull();
    expect(toConnectionBody({ ...formFromCamera(camera), sourceType: "file" }, camera)).toBeNull();
  });
});

describe("describeDetail", () => {
  it("passes text through and flattens FastAPI validation lists", () => {
    expect(describeDetail("a camera with this name already exists")).toBe("a camera with this name already exists");
    expect(
      describeDetail([
        { loc: ["body", "name"], msg: "String should have at least 1 character" },
        { loc: ["body"], msg: "Value error, source_uri of a file camera must start with file://" },
      ]),
    ).toBe("name: String should have at least 1 character; source_uri of a file camera must start with file://");
    expect(describeDetail(undefined)).toBeNull();
  });
});

describe("route", () => {
  it("knows the cameras page", () => {
    expect(parseHash("#/cameras")).toEqual({ page: "cameras", cameraId: null });
  });
});
