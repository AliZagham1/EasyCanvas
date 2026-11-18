import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockConsent } from "./consent";

const LABELS = ["Dashboard", "Courses", "Upcoming", "Priorities", "Class Schedule", "Study Assistant", "Settings"];

// Pages call the backend for data; navigation must work even when it is unreachable.
async function open(page: Page, path = "/priorities") {
  await mockConsent(page);
  await page.route("**/127.0.0.1:8000/api/**", route => route.request().url().includes("/api/ai-consent") ? route.fallback() : route.fulfill({ status: 503, json: {} }));
  await page.goto(path);
}

test.describe("desktop sidebar", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("shows all seven destinations, marks the current page, and has no Menu button", async ({ page }) => {
    await open(page);
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    for (const label of LABELS) await expect(nav.getByRole("link", { name: label, exact: true })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Priorities", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("button", { name: "Menu" })).toBeHidden();
  });

  test("is reachable by keyboard in reading order", async ({ page }) => {
    await open(page);
    await page.getByRole("link", { name: "EasyCanvas" }).focus();
    const seen: string[] = [];
    for (let i = 0; i < LABELS.length; i++) {
      await page.keyboard.press("Tab");
      seen.push((await page.evaluate(() => document.activeElement?.textContent || "")).trim());
    }
    expect(seen).toEqual(LABELS);
  });
});

test.describe("mobile navigation", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("every page is reachable from the Menu, which closes on Escape and after navigating", async ({ page }) => {
    await open(page);
    const menu = page.getByRole("button", { name: "Menu" });
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("link", { name: "Courses", exact: true })).toBeHidden();

    await menu.click();
    await expect(page.getByRole("button", { name: "Close" })).toHaveAttribute("aria-expanded", "true");
    for (const label of LABELS) await expect(page.getByRole("link", { name: label, exact: true })).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByRole("link", { name: "Courses", exact: true })).toBeHidden();
    await expect(page.getByRole("button", { name: "Menu" })).toBeFocused();

    await page.getByRole("button", { name: "Menu" }).click();
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByRole("link", { name: "Courses", exact: true })).toBeHidden();
  });

  test("navigation controls are at least 44px tall and the page does not scroll sideways", async ({ page }) => {
    await open(page);
    await page.getByRole("button", { name: "Menu" }).click();
    const heights = await page.evaluate(() => [...document.querySelectorAll(".menu-button, .nav-link")].map(e => e.getBoundingClientRect().height));
    expect(heights.length).toBe(LABELS.length + 1);
    for (const height of heights) expect(height).toBeGreaterThanOrEqual(44);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
});
