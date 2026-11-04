import { describe, expect, it } from "vitest";
import { trapTarget } from "./hooks/useModal";

describe("trapTarget", () => {
  const items = ["close", "name", "save"];

  it("lets the browser move inside the modal", () => {
    expect(trapTarget(items, "name", false)).toBeNull();
    expect(trapTarget(items, "name", true)).toBeNull();
    expect(trapTarget(items, "close", false)).toBeNull();
    expect(trapTarget(items, "save", true)).toBeNull();
  });

  it("wraps from the last element to the first on Tab", () => {
    expect(trapTarget(items, "save", false)).toBe("close");
  });

  it("wraps from the first element to the last on Shift+Tab", () => {
    expect(trapTarget(items, "close", true)).toBe("save");
  });

  it("brings the focus in when it is outside the modal or nowhere", () => {
    expect(trapTarget(items, "page-link", false)).toBe("close");
    expect(trapTarget(items, "page-link", true)).toBe("save");
    expect(trapTarget(items, null, false)).toBe("close");
  });

  it("does nothing for a modal with nothing to focus", () => {
    expect(trapTarget([], null, false)).toBeNull();
  });

  it("keeps a lone element on itself", () => {
    expect(trapTarget(["only"], "only", false)).toBe("only");
    expect(trapTarget(["only"], "only", true)).toBe("only");
  });
});
