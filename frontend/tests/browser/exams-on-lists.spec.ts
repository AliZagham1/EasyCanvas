import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockConsent } from "./consent";

test.use({ timezoneId: "America/Chicago" });
test.beforeEach(async ({ page }) => { await mockConsent(page); });

const NOW = new Date("2026-11-17T18:00:00Z");                       // Tuesday 12:00 in Chicago
const at = (hours: number) => new Date(NOW.getTime() + hours * 3_600_000).toISOString();
const task = (id: number, name: string, hours: number) => ({ id, name, due_at: at(hours), points_possible: 10, status: "open" });

const plan = (id: number, kind: string, title: string, due: string) => ({ plan_id: String(id).padStart(32, "0"), course_id: 1, kind, assignment_id: kind === "assignment" ? 7 : null, title, due_at: due,
  course_code: "2268-CSE-4321-002", course_name: "TESTING", time_zone: "America/Chicago", activities: [], needed_minutes: 0, scheduled_minutes: 0, done_minutes: 0 });
const plans = () => [
  plan(1, "custom", "Midterm exam", "2026-11-18T15:00:00Z"),            // Wednesday 9:00 AM local: tomorrow, 21 hours away
  plan(2, "custom", "Final exam", "2026-12-05T16:00:00Z"),              // far away
  plan(3, "custom", "Old exam", "2026-11-16T15:00:00Z"),                // already past
  plan(4, "assignment", "Homework 7", "2026-11-18T23:00:00Z"),          // an assignment plan: Canvas already supplies it
];

async function open(page: Page, path: string, options: { plans?: "ok" | "fail" } = {}) {
  await page.route("**/api/courses", (route) => route.fulfill({ json: [{ id: 1, course_code: "2268-CSE-4321-002", name: "Testing" }] }));
  await page.route("**/api/courses/1/upcoming", (route) => route.fulfill({ json: [task(1, "Soon quiz", 6), task(2, "Mid homework", 40), task(3, "Later project", 200)] }));
  await page.route("**/api/ingestion", (route) => route.fulfill({ json: { status: "completed" } }));
  await page.route("**/api/schedule", (route) => route.fulfill({ json: { courses: [] } }));
  await page.route("**/api/study-plans", (route) => options.plans === "fail" ? route.fulfill({ status: 503, json: {} })
    : route.fulfill({ json: { plans: plans(), preferences: { day_start: "09:00", day_end: "21:00", max_minutes_per_day: 240, buffer_minutes: 120 } } }));
  await page.clock.setFixedTime(NOW);
  await page.goto(path);
}

test("the Dashboard lists exams from Study Plans with an Exam tag, and counts them", async ({ page }) => {
  await open(page, "/");
  const soon = page.getByRole("group", { name: "Due within 24 hours" });
  await expect(soon).toContainText("Soon quiz");
  await expect(soon).toContainText("Midterm exam");
  const exam = soon.getByRole("link", { name: "Midterm exam" });
  await expect(exam).toHaveAttribute("href", "/study-plans");
  await expect(soon.locator(".work-row").filter({ hasText: "Midterm exam" })).toContainText("Exam");
  await expect(soon.locator(".work-row").filter({ hasText: "Midterm exam" })).toContainText("Tomorrow");
  await expect(page.getByRole("group", { name: "Later" })).toContainText("Final exam");
  await expect(page.getByLabel("Coursework overview").locator("dd")).toHaveText(["2", "1", "5"]);   // 3 assignments + 2 upcoming exams
  const rows = page.locator(".dashboard-upcoming");
  await expect(rows).not.toContainText("Old exam");                                                  // already past
  await expect(rows).not.toContainText("Homework 7");                                                // assignment plans are not repeated
  await expect(page.getByRole("link", { name: /CSE 4321/ })).toContainText("5 upcoming");
  await exam.click();
  await expect(page).toHaveURL(/\/study-plans$/);
});

test("the Dashboard still works, with assignments only, when Study Plans cannot be loaded", async ({ page }) => {
  await open(page, "/", { plans: "fail" });
  await expect(page.getByRole("group", { name: "Due within 24 hours" })).toContainText("Soon quiz");
  await expect(page.getByText("Midterm exam")).toHaveCount(0);
  await expect(page.getByLabel("Coursework overview").locator("dd")).toHaveText(["1", "1", "3"]);
});

test("Upcoming lists exams under their day with the tag and no invented points", async ({ page }) => {
  await open(page, "/upcoming");
  await expect(page.getByRole("heading", { name: "Upcoming", exact: true, level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: /All upcoming/ })).toContainText("5");
  const tomorrow = page.getByRole("group", { name: /Tomorrow/ });
  const row = tomorrow.locator(".work-row").filter({ hasText: "Midterm exam" });
  await expect(row).toContainText("Exam");
  await expect(row).toContainText("CSE 4321");
  await expect(row).not.toContainText("points");
  await expect(row.getByRole("link", { name: "Midterm exam" })).toHaveAttribute("href", "/study-plans");
  await expect(page.locator(".work-row").filter({ hasText: "Soon quiz" })).toContainText("10 points");
  await expect(page.getByText("Old exam")).toHaveCount(0);
  await expect(page.getByText("Homework 7")).toHaveCount(0);
});

for (const width of [1280, 390]) {
  test(`Dashboard and Upcoming with exams fit without sideways scrolling at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    await open(page, "/");
    await expect(page.getByText("Midterm exam")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.goto("/upcoming");
    await expect(page.getByText("Midterm exam")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
}

test("exam rows are highlighted so they catch the eye, on the Dashboard and Upcoming", async ({ page }) => {
  for (const path of ["/", "/upcoming"]) {
    await open(page, path);
    const exam = page.locator(".work-row.is-exam").filter({ hasText: "Midterm exam" });
    const normal = page.locator(".work-row").filter({ hasText: "Soon quiz" });
    const background = (row: typeof exam) => row.evaluate((el) => getComputedStyle(el).backgroundColor);
    const examBackground = await background(exam);
    expect(examBackground).not.toBe("rgba(0, 0, 0, 0)");                               // a tint, not transparent
    expect(examBackground).not.toBe(await background(normal));
    const tag = exam.locator(".due-tag");
    expect(await tag.evaluate((el) => getComputedStyle(el).backgroundColor)).toBe("rgb(79, 70, 229)");   // filled indigo tag
    expect(await tag.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(255, 255, 255)");
  }
});

const courseDetails = { course_id: 1, course_code: "2268-CSE-4321-002", name: "2268-CSE-4321-002-TESTING", time_zone: "America/Chicago", canvas_url: null, syllabus_url: null, synced_at: null,
  counts: { assignments: 0, modules: 0, files: 0, pages: 0, quizzes: 0, announcements: 0 }, meetings: [], announcements: [], modules: [], latest_announcement: null };

async function openCourse(page: Page, studyPlans: unknown[] | "fail") {
  await page.route("**/api/course-catalog", (route) => route.fulfill({ json: { courses: [courseDetails] } }));
  await page.route("**/api/courses/1/details", (route) => route.fulfill({ json: courseDetails }));
  await page.route("**/api/priorities**", (route) => route.fulfill({ status: 502, json: {} }));
  await page.route("**/api/search/status", (route) => route.fulfill({ json: { courses: [] } }));
  await page.route("**/api/study-plans", (route) => studyPlans === "fail" ? route.fulfill({ status: 503, json: {} })
    : route.fulfill({ json: { plans: studyPlans, preferences: { day_start: "09:00", day_end: "21:00", max_minutes_per_day: 240, buffer_minutes: 120 } } }));
  await page.clock.setFixedTime(NOW);
  await page.goto("/courses/1");
}

test("a course page lists only that course's upcoming exams, highlighted, with a link to the plan", async ({ page }) => {
  await openCourse(page, [...plans(), { ...plan(5, "custom", "Other course exam", "2026-11-20T15:00:00Z"), course_id: 2 }]);
  const section = page.getByRole("region", { name: /Exams/ });
  await expect(section).toContainText("Midterm exam");
  await expect(section).toContainText("Final exam");
  await expect(section).toContainText("Tomorrow");
  await expect(section.locator(".work-row.is-exam")).toHaveCount(2);
  await expect(section.getByRole("link", { name: "Midterm exam" })).toHaveAttribute("href", "/study-plans");
  await expect(section.locator(".due-tag").first()).toHaveText("Exam");
  await expect(page.getByText("Old exam")).toHaveCount(0);                                  // already past
  await expect(page.getByText("Other course exam")).toHaveCount(0);                         // belongs to another course
  await expect(page.getByText("Homework 7")).toHaveCount(0);                                // assignment plans are not repeated
  await section.getByRole("link", { name: "Midterm exam" }).click();
  await expect(page).toHaveURL(/\/study-plans$/);
});

test("a course page has no Exams section when there are none, or when Study Plans cannot be loaded", async ({ page }) => {
  await openCourse(page, [plan(3, "custom", "Old exam", "2026-11-16T15:00:00Z"), plan(4, "assignment", "Homework 7", "2026-11-18T23:00:00Z")]);
  await expect(page.getByRole("heading", { name: "CSE 4321", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Exams/ })).toHaveCount(0);
  const failing = await page.context().newPage();
  await failing.route("**/api/course-catalog", (route) => route.fulfill({ json: { courses: [courseDetails] } }));
  await failing.route("**/api/courses/1/details", (route) => route.fulfill({ json: courseDetails }));
  await failing.route("**/api/priorities**", (route) => route.fulfill({ status: 502, json: {} }));
  await failing.route("**/api/search/status", (route) => route.fulfill({ json: { courses: [] } }));
  await failing.route("**/api/study-plans", (route) => route.fulfill({ status: 503, json: {} }));
  await failing.goto("/courses/1");
  await expect(failing.getByRole("heading", { name: "CSE 4321", level: 1 })).toBeVisible();
  await expect(failing.getByRole("heading", { name: /^Exams/ })).toHaveCount(0);
});
