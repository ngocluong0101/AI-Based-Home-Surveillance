/*
 * @author: Ngoc Luong <ngocluongg0101@gmail.com>
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// WCAG 2.1 level A and AA, the rules a machine can decide.
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

/** Waits until nothing on the page is still fading or sliding in (axe measures colours half way through an animation, which are not the real ones). */
async function settle(page: Page) {
  const snapshot = () =>
    page.evaluate(() =>
      Array.from(document.querySelectorAll("body *"))
        .map((el) => { const c = getComputedStyle(el); return `${c.opacity}|${c.transform}`; })
        .join(","));
  let last = await snapshot();
  for (let stable = 0; stable < 3; ) {
    await page.waitForTimeout(200);
    const now = await snapshot();
    stable = now === last ? stable + 1 : 0;
    last = now;
  }
}

async function scan(page: Page, what: string) {
  await settle(page);
  const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const lines = result.violations.map(
    (v) => `${v.id} (${v.impact}): ${v.help}\n  ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join("\n  ")}`,
  );
  expect(lines, `${what} has accessibility violations`).toEqual([]);
}

// The demo's person who falls starts a new cycle every 70 s counted from the epoch.
const CYCLE = 1_800_000_000_000 - (1_800_000_000_000 % 70_000);

test("the demo says it is made up", async ({ page }) => {
  await page.goto("/#/dashboard");
  await expect(page.getByText(/made-up cameras/i)).toBeVisible();
});

for (const [name, hash] of [
  ["the dashboard", "#/dashboard"],
  ["the wall", "#/wall"],
  ["monitoring (living room, where the fall happens)", "#/monitoring/3"],
  ["events", "#/events"],
  ["cameras", "#/cameras"],
  ["settings", "#/settings"],
] as const) {
  test(`${name} has no accessibility violations`, async ({ page }) => {
    await page.goto(`/${hash}`);
    await page.getByRole("heading", { level: 1 }).waitFor();
    await page.waitForLoadState("networkidle");
    await scan(page, name);
  });
}

test("the camera form with the fall network settings has no accessibility violations and checks them", async ({ page }) => {
  await page.goto("/#/cameras");
  await page.getByRole("button", { name: "Edit Living room" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit Living room" });
  await expect(dialog).toBeVisible();
  // the living room has the fall rule and the network "or the rules": the two thresholds are shown
  await expect(dialog.getByLabel("Network score that counts as a fall")).toHaveValue("0.95");
  await expect(dialog.getByLabel("Scores in a row needed")).toHaveValue("3");
  await scan(page, "the edit-camera form with the network settings");

  await dialog.getByLabel("Network score that counts as a fall").fill("1.5");
  await expect(dialog.getByRole("button", { name: /save/i })).toBeVisible();
  await dialog.getByRole("button", { name: /save/i }).click();
  await expect(dialog.getByText("Enter a number from 0 to 1.")).toBeVisible();   // not saved: the dialog is still there
  await expect(dialog).toBeVisible();

  await dialog.getByLabel("Network score that counts as a fall").fill("0.95");
  await dialog.getByLabel("Scores in a row needed").fill("4");
  await dialog.getByRole("button", { name: /save/i }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "Edit Living room" }).click();
  await expect(page.getByLabel("Network score that counts as a fall")).toHaveValue("0.95");
  await expect(page.getByLabel("Scores in a row needed")).toHaveValue("4");
});

test("switching the network off hides its thresholds", async ({ page }) => {
  await page.goto("/#/cameras");
  await page.getByRole("button", { name: "Edit Living room" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit Living room" });
  await dialog.getByLabel("How a fall is decided").selectOption("0");
  await expect(dialog.getByLabel("Network score that counts as a fall")).toHaveCount(0);
});

test("a person falls on the living room picture, a FALL alert arrives and the pretend VLM confirms it", async ({ page }) => {
  // The fall begins 8 s into a cycle and its alert is due at 10 s. The clock is frozen at 6 s and moved only by hand, so the moments are exact.
  await page.clock.install({ time: CYCLE });
  await page.goto("/#/events");
  await page.getByRole("heading", { level: 1 }).waitFor();
  await page.getByRole("button", { name: "Table" }).click();
  const falls = page.getByRole("row").filter({ hasText: "FALL" });
  await expect(page.getByRole("row").nth(1)).toBeVisible();
  await page.clock.pauseAt(CYCLE + 6_000);
  const before = await falls.count();                       // the history has falls of its own: only a NEW one counts

  await page.clock.runFor(3_900);                           // 9.9 s: the person is on the floor, the alert is not due yet
  expect(await falls.count()).toBe(before);
  // the alert is due at 10 s; the demo's fake network needs a little more clock to show it: allow up to 1 s, not more
  for (let i = 0; i < 5 && (await falls.count()) < before + 1; i++) await page.clock.runFor(200);
  await expect(falls).toHaveCount(before + 1);
  await page.clock.resume();                                // the pretend VLM answers 3 s later: let the time run on its own
  await falls.first().click();                              // the event drawer says what the VLM concluded
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText(/confirmed/i);
  await expect(drawer).not.toContainText(/checking/i);
});
