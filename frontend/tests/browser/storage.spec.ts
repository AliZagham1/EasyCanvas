import { test, expect } from "@playwright/test";

function preview() {
  return { preview_id: "a".repeat(64), grace_days: 30, blocked_reason: null, eligible_bytes: 102400, chats_preserved: true,
    courses: [
      { course_id: 1, title: "TEST 100", name: "Old course", term_name: "Previous semester", kept: false, eligible: true,
        materials_removed: false, reason: null, counts: { documents: 8 }, estimated_bytes: 102400 },
      { course_id: 2, title: "TEST 200", name: "Current course", term_name: "Current semester", kept: false, eligible: false,
        materials_removed: false, reason: "Current course.", counts: { documents: 12 }, estimated_bytes: 204800 },
    ] };
}

test("cleanup requires selection and explicit confirmation; current courses stay protected", async ({ page }) => {
  let data = preview();
  const deletes: unknown[] = [];
  await page.route("**/api/storage**", async route => {
    if (route.request().method() === "POST") {
      deletes.push(route.request().postDataJSON());
      data = { ...data, courses: [data.courses[1]] };
      return route.fulfill({ json: { compacted: true } });
    }
    return route.fulfill({ json: data });
  });
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByLabel("Select TEST 200 for cleanup")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Delete selected materials" })).toBeDisabled();
  await page.getByLabel("Select TEST 100 for cleanup").check();
  await page.getByRole("button", { name: "Delete selected materials" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(deletes).toHaveLength(0);
  await page.getByRole("button", { name: "Delete selected materials" }).click();
  await page.getByRole("button", { name: "Delete materials", exact: true }).click();
  await expect(page.getByText("Selected course materials deleted. Saved conversations were kept.")).toBeVisible();
  expect(deletes).toEqual([{ course_ids: [1], preview_id: "a".repeat(64) }]);
});

test("keep choices persist through the backend and invalidate selection", async ({ page }) => {
  const data = preview();
  await page.route("**/api/storage**", route => {
    if (route.request().method() === "PATCH") {
      expect(route.request().postDataJSON()).toEqual({ keep: true });
      data.courses[0] = { ...data.courses[0], kept: true, eligible: false, reason: "You chose to keep this course." };
      return route.fulfill({ json: { course_id: 1, kept: true } });
    }
    return route.fulfill({ json: data });
  });
  await page.goto("/settings");
  await page.getByLabel("Select TEST 100 for cleanup").check();
  await page.getByLabel("Keep TEST 100 materials").check();
  await expect(page.getByLabel("Select TEST 100 for cleanup")).toBeDisabled();
  await expect(page.getByLabel("Keep TEST 100 materials")).toBeChecked();
  await expect(page.getByRole("button", { name: "Delete selected materials" })).toBeDisabled();
});

test("loading and failure never offer deletion", async ({ page }) => {
  await page.route("**/api/storage", route => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
  await page.goto("/settings");
  await expect(page.getByRole("region", { name: "Course storage" }).getByRole("alert")).toContainText("Unable to load");
  await expect(page.getByRole("button", { name: "Delete selected materials" })).toHaveCount(0);
});

test("stale preview keeps the dialog open with recovery guidance", async ({ page }) => {
  await page.route("**/api/storage**", route => route.request().method() === "POST"
    ? route.fulfill({ status: 409, json: { detail: "Changed" } }) : route.fulfill({ json: preview() }));
  await page.goto("/settings");
  await page.getByLabel("Select TEST 100 for cleanup").check();
  await page.getByRole("button", { name: "Delete selected materials" }).click();
  await page.getByRole("button", { name: "Delete materials", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("refresh the preview");
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeEnabled();
});

for (const width of [1280, 390]) {
  test(`storage page and delete confirmation fit at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.route("**/api/storage", route => route.fulfill({ json: preview() }));
    await page.goto("/settings");
    await page.getByLabel("Select TEST 100 for cleanup").check();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/private/tmp/easycanvas-storage-${width}.png`, fullPage: true });
    await page.getByRole("button", { name: "Delete selected materials" }).click();
    const box = await page.getByRole("dialog").boundingBox();
    expect(box?.width).toBeLessThanOrEqual(width - 16);
    await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
    await page.screenshot({ path: `/private/tmp/easycanvas-storage-dialog-${width}.png`, fullPage: true });
  });
}
