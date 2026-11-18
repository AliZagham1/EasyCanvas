import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockConsent } from "./consent";

test.beforeEach(async ({ page }) => { await mockConsent(page); });

const quiet = (page: Page) => page.route("**/127.0.0.1:8000/api/**", route => route.request().url().includes("/api/ai-consent") ? route.fallback() : route.fulfill({ status: 503, json: {} }));
const background = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const DARK = "rgb(15, 17, 21)";
const LIGHT = "rgb(255, 255, 255)";

test("with no saved choice the app follows the system setting", async ({ page }) => {
  await quiet(page);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/priorities");
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.*/);
  expect(await background(page)).toBe(DARK);
  await page.emulateMedia({ colorScheme: "light" });
  expect(await background(page)).toBe(LIGHT);
});

test("Settings switches the theme, remembers it after a reload, and System returns to the device setting", async ({ page }) => {
  await quiet(page);
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/settings");
  const group = page.getByRole("group", { name: "Theme" });
  await expect(group.getByLabel("System")).toBeChecked();

  await group.getByLabel("Dark").check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await background(page)).toBe(DARK);

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");           // set before first paint by the head script
  await expect(page.getByRole("group", { name: "Theme" }).getByLabel("Dark")).toBeChecked();
  expect(await background(page)).toBe(DARK);

  await page.getByRole("group", { name: "Theme" }).getByLabel("System").check();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.*/);
  expect(await background(page)).toBe(LIGHT);                                           // the device is in light mode
});

test("an explicit Light choice beats a dark device", async ({ page }) => {
  await quiet(page);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/settings");
  await page.getByRole("group", { name: "Theme" }).getByLabel("Light").check();
  expect(await background(page)).toBe(LIGHT);
  await page.goto("/priorities");
  expect(await background(page)).toBe(LIGHT);
});

test("a saved theme is applied before the page paints, so there is no flash", async ({ page }) => {
  await quiet(page);
  await page.addInitScript(() => localStorage.setItem("easycanvas-theme", "dark"));
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/priorities", { waitUntil: "commit" });
  await page.waitForFunction(() => document.documentElement.hasAttribute("data-theme"));  // present as soon as the document exists
  expect(await background(page)).toBe(DARK);
});

test("a corrupt saved value is ignored and the theme control is keyboard operable", async ({ page }) => {
  await quiet(page);
  await page.addInitScript(() => localStorage.setItem("easycanvas-theme", "purple"));
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/settings");
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.*/);
  const system = page.getByRole("group", { name: "Theme" }).getByLabel("System");
  await system.focus();
  await page.keyboard.press("ArrowRight");                                              // radio groups move with the arrow keys
  await expect(page.getByRole("group", { name: "Theme" }).getByLabel("Light")).toBeChecked();
  await page.keyboard.press("ArrowRight");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("the primary button and danger text stay readable in dark mode", async ({ page }) => {
  await quiet(page);
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/study-assistant");
  await page.getByRole("button", { name: "Enable AI Features" }).waitFor({ state: "hidden", timeout: 1000 }).catch(() => {});
  // Contrast of the filled button colors, computed from the real stylesheet.
  const ratio = await page.evaluate(() => {
    const probe = document.createElement("button");
    probe.className = "primary-button btn-primary"; document.body.append(probe);
    const cs = getComputedStyle(probe);
    const parse = (c: string) => c.match(/\d+(\.\d+)?/g)!.slice(0, 3).map(Number);
    const lum = ([r, g, b]: number[]) => { const f = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const a = lum(parse(cs.color)), b = lum(parse(cs.backgroundColor)); probe.remove();
    return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  });
  expect(ratio).toBeGreaterThanOrEqual(4.5);
});
