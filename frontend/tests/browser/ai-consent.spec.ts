import { test, expect } from "@playwright/test";
import { mockConsent } from "./consent";

const undecided = { status: "undecided", enabled: false, needs_decision: true } as const;
const declined = { status: "declined", enabled: false, needs_decision: false } as const;

test("a first-time student sees the consent modal with nothing preselected", async ({ page }) => {
  const consent = await mockConsent(page, undecided);
  await page.goto("/");
  const dialog = page.getByRole("dialog", { name: "AI Data Processing Consent" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("including OpenAI")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Not Now" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Agree & Continue" })).toBeVisible();
  expect(consent.saved).toEqual([]);
  await expect(dialog.getByRole("checkbox")).toHaveCount(0);
});

test("Not Now saves a decline, closes the modal, and leaves non-AI pages usable", async ({ page }) => {
  const consent = await mockConsent(page, undecided);
  await page.goto("/priorities");
  await page.getByRole("button", { name: "Not Now" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(consent.saved).toEqual(["declined"]);
  await expect(page.getByRole("heading", { name: "Priorities", exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("dialog")).toHaveCount(0); // saved: not asked again
});

test("Agree & Continue enables AI and does not show the modal again", async ({ page }) => {
  const consent = await mockConsent(page, undecided);
  await page.goto("/");
  await page.getByRole("button", { name: "Agree & Continue" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(consent.saved).toEqual(["granted"]);
  expect(consent.state.enabled).toBe(true);
});

test("Escape closes the modal without recording any decision", async ({ page }) => {
  const consent = await mockConsent(page, undecided);
  await page.goto("/");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(consent.saved).toEqual([]);
});

test("without consent the Study Assistant shows the disabled state and makes no assistant requests", async ({ page }) => {
  await mockConsent(page, declined);
  const requests: string[] = [];
  page.on("request", r => { if (/\/assistant|\/conversations|search-index/.test(r.url())) requests.push(r.url()); });
  await page.goto("/study-assistant");
  await expect(page.getByRole("heading", { name: "AI Study Assistant is disabled" })).toBeVisible();
  await expect(page.getByText("Enable AI features to get beginner-friendly explanations")).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
  expect(requests).toEqual([]);
  await page.getByRole("button", { name: "Enable AI Features" }).click();
  await expect(page.getByRole("dialog", { name: "AI Data Processing Consent" })).toBeVisible();
});

test("Settings can withdraw consent and explains what that does and does not delete", async ({ page }) => {
  const consent = await mockConsent(page);
  await page.route("**/api/storage**", route => route.fulfill({ json: { preview_id: "a".repeat(64), grace_days: 30, blocked_reason: null, eligible_bytes: 0, chats_preserved: true, courses: [] } }));
  await page.goto("/settings");
  await expect(page.getByRole("heading", { name: "AI & Privacy" })).toBeVisible();
  await page.getByRole("button", { name: "Withdraw consent" }).click();
  await expect(page.getByText("cannot recall data that was already sent")).toBeVisible();
  await page.getByRole("group", { name: "Confirm withdrawal" }).getByRole("button", { name: "Withdraw consent" }).click();
  await expect(page.getByText("Consent withdrawn.")).toBeVisible();
  expect(consent.saved).toEqual(["withdrawn"]);
  await expect(page.getByRole("region", { name: "AI & Privacy" }).getByRole("button", { name: "Enable AI Features" })).toBeVisible();
});

test("if the consent status cannot be loaded, AI stays off and no modal blocks the app", async ({ page }) => {
  await page.route("**/api/ai-consent", route => route.fulfill({ status: 500, json: {} }));
  await page.goto("/study-assistant");
  await expect(page.getByRole("heading", { name: "AI Study Assistant is disabled" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
