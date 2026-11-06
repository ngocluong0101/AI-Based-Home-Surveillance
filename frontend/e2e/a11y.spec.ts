import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { makeCamera, makeEvent, reset, startCamera } from "./helpers";

// WCAG 2.1 level A and AA, the rules a machine can decide. (Contrast, names, roles, labels, landmarks...)
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

async function scan(page: Page, what: string) {
  const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const lines = result.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.help}\n  ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("\n  ")}`,
  );
  expect(lines, `${what} has accessibility violations`).toEqual([]);
}

test.beforeEach(async ({ request }) => {
  await reset(request);
  const a = await makeCamera(request, { name: "A11y garage" });
  const b = await makeCamera(request, { name: "A11y garden", ai_enabled: false });
  await startCamera(request, a.id);
  await makeEvent(request, a.id, 1);
  await makeEvent(request, b.id, 2);
});

for (const [name, hash] of [
  ["the dashboard", "#/dashboard"],
  ["the wall", "#/wall"],
  ["monitoring", "#/monitoring"],
  ["events", "#/events"],
  ["cameras", "#/cameras"],
] as const) {
  test(`${name} has no accessibility violations`, async ({ page }) => {
    await page.goto(`/${hash}`);
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.waitForLoadState("networkidle");
    await scan(page, name);
  });
}

test("the camera form (add and edit) has no accessibility violations", async ({ page }) => {
  await page.goto("/#/cameras");
  await page.getByRole("button", { name: "Add camera" }).first().click();
  await expect(page.getByRole("dialog", { name: "Add camera" })).toBeVisible();
  await scan(page, "the add-camera form");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Edit A11y garage" }).click();
  await expect(page.getByRole("dialog", { name: "Edit A11y garage" })).toBeVisible();
  await scan(page, "the edit-camera form");
});

test("events as a table and a focused wall tile have no accessibility violations", async ({ page, request }) => {
  await page.goto("/#/events");
  await page.getByRole("button", { name: "Table" }).click();
  await expect(page.getByRole("row").nth(1)).toBeVisible();
  await scan(page, "events as a table");

  const cameras = (await (await request.get("/api/cameras")).json()) as { id: number }[];
  await page.goto(`/#/wall/${cameras[0].id}`);
  await page.getByRole("button", { name: "Back to wall" }).waitFor();
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".player-overlay").first()).toHaveCSS("opacity", "1");   // it fades in; colours measured half way through are not its colours
  await scan(page, "a focused wall tile");
});

test("the event drawer and the delete dialog have no accessibility violations", async ({ page }) => {
  await page.goto("/#/events");
  await page.getByRole("button", { name: /A11y/ }).first().click();   // a card of the gallery
  await expect(page.getByRole("dialog")).toBeVisible();
  await scan(page, "the event drawer");
  await page.keyboard.press("Escape");

  await page.goto("/#/cameras");
  await page.getByRole("button", { name: "Delete A11y garden" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveCSS("opacity", "1");   // it fades in; colours measured half way through are not its colours
  await scan(page, "the delete dialog");
});

// ---- the keyboard

test("a drawer takes the keyboard in, keeps it, and gives it back", async ({ page }) => {
  await page.goto("/#/cameras");
  const opener = page.getByRole("button", { name: "Add camera" }).first();
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Add camera" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Name", { exact: true })).toBeFocused();   // the first thing to type in

  // Tab and Shift+Tab never leave the drawer, however long they are held
  const inside = () => page.evaluate(() => !!document.activeElement?.closest("[role=dialog]"));
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Tab");
    expect(await inside(), `Tab ${i + 1} left the drawer`).toBe(true);
  }
  for (let i = 0; i < 25; i++) {
    await page.keyboard.press("Shift+Tab");
    expect(await inside(), `Shift+Tab ${i + 1} left the drawer`).toBe(true);
  }

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();   // back where the user was
});

test("a confirmation starts on the safe choice, keeps the keyboard, and gives it back", async ({ page }) => {
  await page.goto("/#/cameras");
  const opener = page.getByRole("button", { name: "Delete A11y garden" });
  await opener.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();   // not "Delete camera"

  for (let i = 0; i < 6; i++) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => !!document.activeElement?.closest("[role=alertdialog]"))).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(opener).toBeFocused();
  await expect(page.getByRole("row", { name: /A11y garden/ })).toBeVisible();   // nothing was deleted
});

test("everything on the cameras page can be reached and used with the keyboard", async ({ page }) => {
  await page.goto("/#/cameras");
  await page.getByRole("button", { name: "Add camera" }).first().focus();
  // Tab through the page: every control has an accessible name and a visible focus
  const seen = new Set<string>();
  for (let i = 0; i < 40; i++) {
    const info = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return null;
      const name = el.getAttribute("aria-label") || el.textContent?.trim() || (el as HTMLInputElement).placeholder || "";
      const r = el.getBoundingClientRect();
      return { tag: el.tagName, name, visible: r.width > 0 && r.height > 0 };
    });
    if (!info) break;
    expect(info.visible, `a focused ${info.tag} is not visible`).toBe(true);
    expect(info.name, `a focused ${info.tag} has no name`).not.toBe("");
    seen.add(`${info.tag}:${info.name}`);
    await page.keyboard.press("Tab");
  }
  for (const wanted of ["Edit A11y garage", "Delete A11y garage", "Start", "Stop", "Test"]) {
    expect([...seen].some((s) => s.endsWith(`:${wanted}`) || s.includes(wanted)), `${wanted} cannot be reached by Tab`).toBe(true);
  }
});
