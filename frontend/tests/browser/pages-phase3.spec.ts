import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockConsent } from "./consent";

test.use({ timezoneId: "America/Chicago" });
test.beforeEach(async ({ page }) => { await mockConsent(page); });

const NOW = new Date("2026-11-17T18:00:00Z");
const at = (hours: number) => new Date(NOW.getTime() + hours * 3_600_000).toISOString();

async function upcoming(page: Page, items: unknown[]) {
  await page.route("**/api/courses", route => route.fulfill({ json: [{ id: 1, course_code: "2268-CSE-4321-002", name: "Testing" }] }));
  await page.route("**/api/courses/1/upcoming", route => route.fulfill({ json: items }));
  await page.clock.setFixedTime(NOW);
  await page.goto("/upcoming");
}
const task = (id: number, name: string, hours: number, points: number | null = 10) => ({ id, name, due_at: at(hours), points_possible: points, status: "open" });

test("Upcoming is chronological, grouped under one heading per day, with times and points", async ({ page }) => {
  await upcoming(page, [task(1, "Morning quiz", 20, 5), task(2, "Night lab", 45), task(3, "Later project", 80, null)]);
  await expect(page.getByRole("heading", { name: "Upcoming", exact: true, level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: /All upcoming/ })).toContainText("3");
  const headings = page.locator(".work-group-title");
  await expect(headings).toHaveCount(3);
  await expect(headings.first()).toContainText("Tomorrow");
  const first = page.locator(".work-row").first();
  await expect(first).toContainText("Morning quiz");
  await expect(first).toContainText("CSE 4321 · 5 points");
  await expect(first.locator("time")).toHaveAttribute("title", /20\d\d/);                 // the full date and zone stay available
  await expect(page.getByText("Points unavailable")).toBeVisible();                       // no invented point values
  await expect(page.locator("table")).toHaveCount(0);
});

test("Upcoming keeps its honest empty and failure states", async ({ page }) => {
  await upcoming(page, []);
  await expect(page.getByText("No upcoming assignments found.")).toBeVisible();
  const failing = await page.context().newPage();
  await failing.route("**/api/courses", route => route.fulfill({ status: 503, json: {} }));
  await failing.goto("/upcoming");
  await expect(failing.locator(".list-message[role=alert]")).toContainText("Unable to load upcoming assignments");
});

test("Courses lists one plain row per course: no stripes, no repeated readiness text", async ({ page }) => {
  const course = (id: number, code: string, name: string) => ({ course_id: id, course_code: code, name, time_zone: "America/Chicago", canvas_url: null, synced_at: null,
    counts: { assignments: 1, modules: 2, files: 3, pages: 0, quizzes: 0, announcements: 4 }, meetings: [], latest_announcement: null });
  await page.route("**/api/course-catalog", route => route.fulfill({ json: { courses: [course(1, "2268-CSE-4321-002", "2268-CSE-4321-002-TESTING"), course(2, "2268-CSE-3320-003", "2268-CSE-3320-003-OS")] } }));
  await page.route("**/api/priorities**", route => route.fulfill({ status: 502, json: {} }));
  await page.route("**/api/search/status", route => route.fulfill({ json: { courses: [] } }));
  await page.goto("/courses");
  const rows = page.locator(".course-card");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toHaveAttribute("href", "/courses/2");
  expect(await rows.first().evaluate(el => getComputedStyle(el).borderLeftWidth)).toBe("0px");
  expect(await rows.first().evaluate(el => getComputedStyle(el).borderRadius)).toBe("0px");
  await expect(page.getByText("Study Assistant ready")).toHaveCount(0);
});

test("Settings is one column of five consistently styled sections", async ({ page }) => {
  await page.goto("/settings");
  for (const name of ["Appearance", "AI & Privacy", "OpenAI API key", "Study Assistant search", "Course storage"]) {
    await expect(page.getByRole("heading", { name, level: 2 })).toBeVisible();
  }
  const widths = await page.locator(".settings-sections > section").evaluateAll(els => els.map(el => Math.round(el.getBoundingClientRect().width)));
  expect(widths).toHaveLength(5);
  expect(new Set(widths).size).toBe(1);                                                    // every section has the same width
});

for (const path of ["/", "/courses", "/upcoming", "/priorities", "/class-schedule", "/study-plans", "/study-assistant", "/settings"]) {
  test(`${path} has no sideways scrolling on a phone`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 800 });
    await page.route("**/127.0.0.1:8000/api/**", route => route.request().url().includes("/api/ai-consent") ? route.fallback() : route.fulfill({ status: 503, json: {} }));
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test.describe("phone touch targets", () => {
  test.use({ viewport: { width: 390, height: 800 } });

  test("Dashboard links and Priorities rows are large enough to tap", async ({ page }) => {
    await page.route("**/api/courses", route => route.fulfill({ json: [{ id: 1, course_code: "2268-CSE-4321-002", name: "Testing" }] }));
    await page.route("**/api/courses/1/upcoming", route => route.fulfill({ json: [task(1, "Soon quiz", 6)] }));
    await page.route("**/api/ingestion", route => route.fulfill({ json: { status: "completed" } }));
    await page.route("**/api/schedule", route => route.fulfill({ json: { courses: [] } }));
    await page.goto("/");
    const link = page.getByRole("link", { name: "View all upcoming" });
    expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    expect((await page.getByRole("link", { name: /CSE 4321/ }).boundingBox())!.height).toBeGreaterThanOrEqual(44);

    await page.route("**/api/priorities**", route => route.fulfill({ json: { fetched_at: at(0), cached: false, courses: [{ id: 1, course_code: "a", name: "a" }],
      assignments: [{ id: 9, course_id: 1, course_code: "2268-CSE-4321-002", course_name: "Testing", name: "Soon quiz", html_url: "https://canvas.example/a/9", due_at: at(6),
        lock_at: null, unlock_at: null, locked_for_user: false, points_possible: 1, submission_types: [], is_group: false, submission_status: "incomplete", submitted_at: null, missing: false, late: false }],
      failed_course_ids: [] } }));
    await page.context().route("https://canvas.example/**", route => route.fulfill({ body: "ok" }));
    await page.goto("/priorities");
    const row = page.locator(".priority-row").first();
    const box = (await row.boundingBox())!;
    const popup = page.waitForEvent("popup");
    await page.mouse.click(box.x + box.width - 8, box.y + box.height / 2);                  // the empty right edge of the row, not the title text
    const opened = await popup;
    await opened.waitForLoadState();
    expect(opened.url()).toBe("https://canvas.example/a/9");
  });
});
