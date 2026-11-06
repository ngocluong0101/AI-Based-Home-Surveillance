import { expect, test } from "@playwright/test";
import { makeCamera, makeEvent, reset, startCamera } from "./helpers";

// The pictures: camera cards with a cover, the Events gallery, Monitoring's diagnostics, the camera wall.

test.beforeEach(async ({ request, page }) => {
  await reset(request);
  await page.addInitScript(() => localStorage.clear());
});

test("a dashboard camera card is a picture you click to watch the camera", async ({ page, request }) => {
  const cam = await makeCamera(request, { name: "UI porch" });
  await makeEvent(request, cam.id, 1);
  await page.goto("/#/dashboard");
  const watch = page.getByRole("button", { name: "Watch UI porch" });
  await expect(watch).toBeVisible();
  await expect(watch).toContainText("Last detection");   // the event has no image here, but the cover still says when
  await watch.click();
  await expect(page).toHaveURL(new RegExp(`#/monitoring/${cam.id}$`));
});

test("a dashboard card with no detection yet says so", async ({ page, request }) => {
  await makeCamera(request, { name: "UI quiet" });
  await page.goto("/#/dashboard");
  await expect(page.getByRole("button", { name: "Watch UI quiet" })).toContainText("No detections yet");
});

test("events are a gallery grouped by day, a table on request, and the choice is remembered", async ({ page, request }) => {
  const cam = await makeCamera(request, { name: "UI hall" });
  await makeEvent(request, cam.id, 1);
  await makeEvent(request, cam.id, 2);
  await page.goto("/#/events");

  await expect(page.getByRole("heading", { level: 2, name: /Today/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /UI hall/ })).toHaveCount(2);
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Gallery" })).toHaveAttribute("aria-pressed", "true");

  await page.getByRole("button", { name: "Table" }).click();
  await expect(page.getByRole("table")).toBeVisible();
  await expect(page.getByRole("row", { name: /UI hall/ })).toHaveCount(2);   // not all rows: earlier tests left events behind

  // remembered for next time (not tested with a reload: the init script above clears storage on every page load)
  expect(await page.evaluate(() => localStorage.getItem("events-view"))).toBe("table");

  await page.getByRole("button", { name: "Gallery" }).click();
  await expect(page.getByRole("button", { name: /UI hall/ })).toHaveCount(2);

  // a card opens the same detail drawer as a table row
  await page.getByRole("button", { name: /UI hall/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
});

test("monitoring keeps the engine details folded away until asked", async ({ page, request }) => {
  const cam = await makeCamera(request, { name: "UI garage" });
  await startCamera(request, cam.id);
  await page.goto(`/#/monitoring/${cam.id}`);
  const diag = page.locator("details.diag");
  await expect(diag).toBeVisible();
  await expect(diag).not.toHaveAttribute("open", "");
  await expect(page.getByText("Config applied")).toBeHidden();
  await diag.getByText("Diagnostics").click();
  await expect(page.getByText("Config applied")).toBeVisible();
  await expect(page.getByText("Heartbeat")).toBeVisible();
});

test("the wall shows every camera, resizes, focuses one and comes back", async ({ page, request }) => {
  const a = await makeCamera(request, { name: "UI wall A" });
  await makeCamera(request, { name: "UI wall B" });
  await makeCamera(request, { name: "UI wall C" });
  await page.goto("/#/wall");

  await expect(page.getByRole("heading", { level: 1, name: "Wall" })).toBeVisible();
  for (const n of ["A", "B", "C"]) await expect(page.getByText(`UI wall ${n}`, { exact: true })).toBeVisible();

  // 1x1 holds one tile and pages through the rest; 3x3 holds all three
  await expect(page.getByRole("button", { name: "2×2" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "1×1" }).click();
  await expect(page.getByText("Page 1 of 3")).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Page 2 of 3")).toBeVisible();
  await page.getByRole("button", { name: "3×3" }).click();
  await expect(page.getByText(/Page \d of/)).toHaveCount(0);

  // focus one tile: the address says which, and the way back is there
  await page.getByRole("button", { name: "Focus UI wall A" }).click();
  await expect(page).toHaveURL(new RegExp(`#/wall/${a.id}$`));
  await expect(page.getByText("UI wall B", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Back to wall" }).click();
  await expect(page).toHaveURL(/#\/wall$/);
  await expect(page.getByText("UI wall B", { exact: true })).toBeVisible();
});

test("only a few wall tiles play live; Go live moves the picture to another camera", async ({ page, request }) => {
  for (let i = 1; i <= 5; i++) {
    const cam = await makeCamera(request, { name: `UI live ${i}` });
    await startCamera(request, cam.id);
  }
  await page.goto("/#/wall");
  await page.getByRole("button", { name: "3×3" }).click();
  await expect(page.getByText("Up to 4 cameras play live at once")).toBeVisible();

  const goLive = page.getByRole("button", { name: /^Go live/ });
  await expect(goLive).toHaveCount(1);   // five running cameras, four live
  await expect(page.getByRole("button", { name: "Go live: UI live 5" })).toBeVisible();   // the fifth is the one waiting

  await page.getByRole("button", { name: "Go live: UI live 5" }).click();
  await expect(page.getByRole("button", { name: "Go live: UI live 5" })).toHaveCount(0);   // it plays now...
  await expect(goLive).toHaveCount(1);   // ...and the oldest live one gave its place up
  await expect(page.getByRole("button", { name: "Go live: UI live 1" })).toBeVisible();
});
