import { expect, test } from "@playwright/test";
import { engineNote, makeCamera, reset, startCamera } from "./helpers";

test.beforeEach(async ({ request, page }) => {
  await reset(request);
  await page.goto("/#/cameras");
});

test("an empty system says so and offers to add a camera", async ({ page }) => {
  await expect(page.getByText("No cameras found")).toBeVisible();
  await page.getByRole("button", { name: "Add camera" }).first().click();
  await expect(page.getByRole("dialog", { name: "Add camera" })).toBeVisible();
});

test("a camera is added with its detection settings, and they are shown in the table", async ({ page }) => {
  await page.getByRole("button", { name: "Add camera" }).first().click();
  const form = page.getByRole("dialog", { name: "Add camera" });
  await form.getByLabel("Name", { exact: true }).fill("E2E front door");
  await form.getByRole("radio", { name: "Video file" }).click();
  await form.getByLabel("File path").fill("file:///videos/e2e.mp4");
  await form.getByRole("button", { name: "Add camera" }).click();

  const row = page.getByRole("row", { name: /E2E front door/ });
  await expect(row).toBeVisible();
  await expect(row).toContainText("Confidence from 25%");   // the defaults
  await expect(row).toContainText("Repeat after 10 s");

  // edit them: stricter, repeat after a minute and a half
  await row.getByRole("button", { name: "Edit E2E front door" }).click();
  const edit = page.getByRole("dialog", { name: "Edit E2E front door" });
  await edit.getByLabel("Minimum confidence (0-1)").fill("0.6");
  await edit.getByLabel("Report the same person again after (s)").fill("90");
  await edit.getByRole("button", { name: "Save changes" }).click();
  await expect(row).toContainText("Confidence at least 60%");
  await expect(row).toContainText("Repeat after 1 min 30 s");

  // and AI off
  await row.getByRole("button", { name: "Edit E2E front door" }).click();
  await page.getByRole("checkbox", { name: /Detect people/ }).uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(row).toContainText("AI off");
});

test("the form refuses what the server would refuse, and says where", async ({ page }) => {
  await page.getByRole("button", { name: "Add camera" }).first().click();
  const form = page.getByRole("dialog", { name: "Add camera" });
  await form.getByRole("button", { name: "Add camera" }).click();
  await expect(form.locator(".field-error").first()).toBeVisible();
  await form.getByPlaceholder("Front door").fill("Bad ranges");   // not by label: once it has an error, the error text is part of the label
  await form.getByRole("radio", { name: "Video file" }).click();
  await form.getByLabel("File path").fill("file:///videos/e2e.mp4");
  await form.getByLabel("Minimum confidence (0-1)").fill("7");
  await form.getByRole("button", { name: "Add camera" }).click();
  await expect(form.locator(".field-error")).toContainText([/./]);
  await expect(page.getByRole("row", { name: /Bad ranges/ })).toHaveCount(0);
});

test("the engine note counts the places and warns before a camera would restart the engine", async ({ page, request }) => {
  const cams = [];
  for (const name of ["E2E one", "E2E two", "E2E three"]) cams.push(await makeCamera(request, { name }));
  await page.reload();
  await expect(engineNote(page)).toContainText("Engine stopped");
  await expect(engineNote(page)).toContainText("3 places free");   // the places the engine will have

  await startCamera(request, cams[0].id);
  await expect(engineNote(page)).toContainText("Engine running");
  await expect(engineNote(page)).toContainText("Places in use: 1 of 3");
  await expect(engineNote(page)).toContainText("2 places free: a new camera starts without interrupting the others");

  await startCamera(request, cams[1].id);
  await startCamera(request, cams[2].id);
  await expect(engineNote(page)).toContainText("Places in use: 3 of 3");
  await expect(engineNote(page)).toContainText("All places are in use: the next camera restarts the engine");

  // a camera taken away frees a place again
  await page.getByRole("row", { name: /E2E three/ }).getByRole("button", { name: "Stop" }).click();
  await expect(engineNote(page)).toContainText("Places in use: 2 of 3");
  await expect(engineNote(page)).toContainText("1 place free");
});

test("a camera is not deleted before it is confirmed", async ({ page, request }) => {
  await makeCamera(request, { name: "E2E to delete" });
  await page.reload();
  const row = page.getByRole("row", { name: /E2E to delete/ });
  await row.getByRole("button", { name: "Delete E2E to delete" }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("Delete E2E to delete?");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(row).toBeVisible();

  await row.getByRole("button", { name: "Delete E2E to delete" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Delete camera" }).click();
  await expect(row).toHaveCount(0);
});
