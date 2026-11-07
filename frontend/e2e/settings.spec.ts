import { expect, test } from "@playwright/test";
import { makeCamera, makeEvent, reset } from "./helpers";

test.beforeEach(async ({ request, page }) => {
  await reset(request); // cameras gone, settings back to what they are on a new installation
  await page.goto("/#/settings");
  await page.getByRole("heading", { level: 1, name: "Settings" }).waitFor();
});

test("shows what is set now, with nothing to save", async ({ page }) => {
  await expect(page.getByLabel("Send Telegram alerts")).toBeChecked();
  await expect(page.getByLabel("Quiet hours")).not.toBeChecked();
  await expect(page.getByLabel("From")).toBeDisabled();
  await expect(page.getByLabel("Until")).toBeDisabled();
  await expect(page.getByLabel("Keep snapshots for (days)")).toHaveValue("7");
  await expect(page.getByLabel("Minimum confidence (0-1)")).toHaveValue("0.25");
  await expect(page.getByLabel("Report the same person again after (s)")).toHaveValue("10");
  await expect(page.getByText("Telegram not set up")).toBeVisible(); // the e2e stack has no Telegram
  await expect(page.getByRole("button", { name: "Send test message" })).toBeDisabled();
  await expect(page.getByText("All changes saved.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Discard" })).toBeDisabled();
});

test("changes are saved, kept after a reload, and used by the Add camera form", async ({ page, request }) => {
  await page.getByLabel("Send Telegram alerts").uncheck();
  await page.getByLabel("Quiet hours").check();
  await page.getByLabel("From").fill("23:00");
  await page.getByLabel("Until").fill("07:00");
  await page.getByLabel("Keep snapshots for (days)").fill("14");
  await page.getByLabel("Minimum confidence (0-1)").fill("0.4");
  await page.getByLabel("Report the same person again after (s)").fill("20");
  await expect(page.getByText("You have unsaved changes.")).toBeVisible();
  await expect(page.getByText("Alerts are held back from 23:00 until 07:00 the next day.")).toBeVisible();

  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Settings saved")).toBeVisible();
  await expect(page.getByText("All changes saved.")).toBeVisible();

  const stored = await (await request.get("/api/settings")).json();
  expect(stored).toMatchObject({
    alerts_enabled: false,
    quiet_hours_enabled: true,
    quiet_start: "23:00",
    quiet_end: "07:00",
    snapshot_retention_days: 14,
    default_conf_threshold: 0.4,
    default_absence_timeout: 20,
  });

  await page.reload();
  await expect(page.getByLabel("Send Telegram alerts")).not.toBeChecked();
  await expect(page.getByLabel("Quiet hours")).toBeChecked();
  await expect(page.getByLabel("From")).toHaveValue("23:00");
  await expect(page.getByLabel("Until")).toHaveValue("07:00");
  await expect(page.getByLabel("Keep snapshots for (days)")).toHaveValue("14");

  await page.goto("/#/cameras");
  await page.getByRole("button", { name: "Add camera" }).first().click();
  const form = page.getByRole("dialog", { name: "Add camera" });
  await expect(form.getByLabel("Minimum confidence (0-1)")).toHaveValue("0.4");
  await expect(form.getByLabel("Report the same person again after (s)")).toHaveValue("20");
});

test("refuses what the server would refuse, and says where", async ({ page, request }) => {
  await page.getByLabel("Quiet hours").check();
  await page.getByLabel("Until").fill("22:00"); // the same minute as the start
  await page.getByLabel("Keep snapshots for (days)").fill("0");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("The end must differ from the start.")).toBeVisible();
  await expect(page.getByText("Enter a whole number of days from 1 to 365.")).toBeVisible();
  const stored = await (await request.get("/api/settings")).json();
  expect(stored.quiet_hours_enabled).toBe(false); // nothing was sent
});

test("discard puts back what was saved", async ({ page }) => {
  await page.getByLabel("Keep snapshots for (days)").fill("30");
  await page.getByLabel("Send Telegram alerts").uncheck();
  await expect(page.getByRole("button", { name: "Discard" })).toBeEnabled();
  await page.getByRole("button", { name: "Discard" }).click();
  await expect(page.getByLabel("Keep snapshots for (days)")).toHaveValue("7");
  await expect(page.getByLabel("Send Telegram alerts")).toBeChecked();
  await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
});

test("with alerts off an event is still saved and shown, and says its alert was skipped", async ({ page, request }) => {
  await page.getByLabel("Send Telegram alerts").uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Settings saved")).toBeVisible();

  const camera = await makeCamera(request, { name: "Settings hall" });
  await makeEvent(request, camera.id, 7);
  await expect
    .poll(async () => {
      const page1 = await (await request.get(`/api/events?camera_id=${camera.id}`)).json();
      return page1.items[0]?.notify_status;
    }, { timeout: 15_000 })
    .toBe("skipped");

  await page.goto("/#/events");
  await page.reload(); // the camera was made behind the page's back: reload so that the page knows its name
  await page.getByRole("button", { name: /Settings hall/ }).first().click();
  await expect(page.getByRole("dialog").getByText("skipped")).toBeVisible();
});
