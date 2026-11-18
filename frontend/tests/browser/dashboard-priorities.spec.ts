import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockConsent } from "./consent";

test.beforeEach(async ({ page }) => { await mockConsent(page); });

const NOW = new Date("2026-11-17T18:00:00Z");
const at = (hours: number) => new Date(NOW.getTime() + hours * 3_600_000).toISOString();

async function dashboard(page: Page, upcoming: unknown[] = []) {
  await page.route("**/api/courses", route => route.fulfill({ json: [
    { id: 1, course_code: "2268-CSE-4321-002", name: "2268-CSE-4321-002-SOFTWARE TESTING & MAINTENANCE" },
    { id: 2, course_code: "2268-CSE-3320-003", name: "2268-CSE-3320-003-OPERATING SYSTEMS" }] }));
  await page.route("**/api/courses/1/upcoming", route => route.fulfill({ json: upcoming }));
  await page.route("**/api/courses/2/upcoming", route => route.fulfill({ json: [] }));
  await page.route("**/api/ingestion", route => route.fulfill({ json: { status: "completed" } }));
  await page.route("**/api/schedule", route => route.fulfill({ json: { courses: [] } }));
  await page.clock.setFixedTime(NOW);
  await page.goto("/");
}
const task = (id: number, name: string, hours: number) => ({ id, name, due_at: at(hours), points_possible: 10, status: "open" });

test("the Dashboard groups upcoming work by urgency without repeating it as a stat, table or card", async ({ page }) => {
  await dashboard(page, [task(1, "Soon quiz", 6), task(2, "Mid homework", 40), task(3, "Later project", 200)]);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  const stats = page.getByLabel("Coursework overview");
  await expect(stats).toContainText("Due within 24 hours");
  await expect(stats.locator("dd")).toHaveText(["1", "1", "3"]);
  await expect(page.getByRole("group", { name: "Due within 24 hours" })).toContainText("Soon quiz");
  await expect(page.getByRole("group", { name: "Due in 1–3 days" })).toContainText("Mid homework");
  await expect(page.getByRole("group", { name: "Later" })).toContainText("Later project");
  await expect(page.getByText("Due in 6 hours")).toBeVisible();
  await expect(page.locator("table")).toHaveCount(0);                                         // no table-of-tables dashboard
  await expect(page.getByRole("link", { name: "View all upcoming" })).toHaveAttribute("href", "/upcoming");
});

test("the Dashboard lists each course once, as a link to its page, with its upcoming count", async ({ page }) => {
  await dashboard(page, [task(1, "Soon quiz", 6)]);
  const row = page.getByRole("link", { name: /CSE 4321/ });
  await expect(row).toHaveAttribute("href", "/courses/1");
  await expect(row).toContainText("SOFTWARE TESTING & MAINTENANCE");
  await expect(row).toContainText("1 upcoming");
  await expect(page.getByRole("link", { name: /CSE 3320/ })).toContainText("0 upcoming");
});

test("the Dashboard keeps its honest empty and failure states", async ({ page }) => {
  await dashboard(page, []);
  await expect(page.getByText("No upcoming assignments found.")).toBeVisible();
  await expect(page.getByLabel("Coursework overview").locator("dd")).toHaveText(["0", "0", "0"]);
});

let n = 0;
const item = (over: Record<string, unknown> = {}) => ({ id: ++n, course_id: 1, course_code: "2268-CSE-4321-002", course_name: "Testing", name: `Task ${n}`,
  html_url: `https://canvas.example/courses/1/assignments/${n}`, due_at: at(48), lock_at: null, unlock_at: null, locked_for_user: false, points_possible: 10,
  submission_types: ["online_upload"], is_group: false, submission_status: "incomplete", submitted_at: null, missing: false, late: false, ...over });

async function priorities(page: Page) {
  await page.route("**/api/priorities**", route => route.fulfill({ json: { fetched_at: at(0), cached: false,
    courses: [{ id: 1, course_code: "2268-CSE-4321-002", name: "Testing" }],
    assignments: [item({ name: "Late lab", due_at: at(-30), missing: true }), item({ name: "Soon quiz", due_at: at(6) }),
      item({ name: "Medium hw", due_at: at(40) }), item({ name: "Far project", due_at: at(200) })], failed_course_ids: [] } }));
  await page.route("**/api/ingestion", route => route.fulfill({ json: { status: "completed" } }));
  await page.clock.setFixedTime(NOW);
  await page.goto("/priorities");
}

test("Priorities uses plain rows: no pills or colored stripes, urgency is stated in words", async ({ page }) => {
  await priorities(page);
  await expect(page.locator(".priority-badge")).toHaveCount(0);
  const overdue = page.getByRole("region", { name: /Overdue/ });
  await expect(overdue).toContainText("Late lab");
  await expect(overdue).toContainText("Missing");
  await expect(overdue).toContainText("Overdue by 30 hours");
  expect(await overdue.locator(".priority-row").first().evaluate(el => getComputedStyle(el).borderLeftWidth)).toBe("0px");
  const upcoming = page.getByRole("region", { name: /Upcoming/ });
  await expect(upcoming.getByText("High priority")).toHaveCount(1);
  await expect(upcoming.getByText("Medium priority")).toHaveCount(1);
  await expect(upcoming.getByText("Low priority")).toHaveCount(1);                           // spoken to screen readers, not drawn
  await expect(upcoming.getByText("Low priority")).toHaveClass(/sr-only/);
});

test("Priorities rows still open the assignment in Canvas, and the toolbar still filters", async ({ page }) => {
  await priorities(page);
  const link = page.getByRole("link", { name: /Soon quiz/ });
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", /noopener/);
  await page.getByLabel("Priority").selectOption("low");
  await expect(page.getByText("Far project")).toBeVisible();
  await expect(page.getByText("Soon quiz")).toHaveCount(0);
});

for (const width of [1280, 390]) {
  test(`Dashboard and Priorities fit without sideways scrolling at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await dashboard(page, [task(1, "Soon quiz", 6), task(2, "A very long assignment name that must wrap rather than push the layout wider than the screen", 40)]);
    await expect(page.getByText("Soon quiz")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.unroute("**/api/priorities**");
    await priorities(page);
    await expect(page.getByText("Far project")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}
