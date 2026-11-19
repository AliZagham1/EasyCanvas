import { test, expect } from "@playwright/test";
import type { Locator } from "@playwright/test";
import { mockConsent } from "./consent";

test.beforeEach(async ({ page }) => { await mockConsent(page); });

/** A visible action must read as a button: a border and a real click target, never bare underlined text. */
async function looksLikeAButton(target: Locator) {
  const style = await target.evaluate((el) => {
    const css = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    return { border: parseFloat(css.borderTopWidth), underline: css.textDecorationLine, height: box.height };
  });
  expect(style.border).toBeGreaterThanOrEqual(1);
  expect(style.underline).toBe("none");
  expect(style.height).toBeGreaterThanOrEqual(28);
}

const step = (title: string, start: string | null) => ({ activity_id: title.length.toString().padStart(32, "0"), plan_id: "1".padStart(32, "0"), position: 0, title, minutes: 60, done: false,
  start_at: start, end_at: start ? new Date(Date.parse(start) + 3600000).toISOString() : null });
const plan = { plan_id: "1".padStart(32, "0"), course_id: 1, kind: "custom", assignment_id: null, title: "Midterm exam", due_at: "2030-01-01T15:00:00Z", course_code: "2268-CSE-4321-002",
  course_name: "TESTING", time_zone: "America/Chicago", activities: [step("Scheduled step", "2029-12-30T16:00:00Z"), step("Open step", null)], needed_minutes: 120, scheduled_minutes: 60, done_minutes: 0 };

test("Study Plans actions are buttons, and removing or deleting is outlined in red", async ({ page }) => {
  await page.route("**/api/study-plans", (route) => route.fulfill({ json: { plans: [plan], preferences: { day_start: "09:00", day_end: "21:00", max_minutes_per_day: 240, buffer_minutes: 120 } } }));
  await page.route("**/api/priorities**", (route) => route.fulfill({ status: 502, json: {} }));
  await page.goto("/study-plans");
  const card = page.getByRole("region", { name: "Midterm exam" });
  for (const name of [/Change the time for Scheduled step/, /Pick the time for Open step/]) await looksLikeAButton(card.getByRole("button", { name }));
  await looksLikeAButton(card.getByRole("button", { name: "Clear time" }));
  const remove = card.getByRole("button", { name: /Remove step Scheduled step/ });
  await looksLikeAButton(remove);
  await looksLikeAButton(card.getByRole("button", { name: "Delete plan" }));
  expect(await remove.evaluate((el) => getComputedStyle(el).color)).toBe("rgb(180, 35, 24)");     // danger red, so a destructive action is obvious
});

test("Dashboard calls to action are buttons", async ({ page }) => {
  await page.route("**/api/courses", (route) => route.fulfill({ json: [{ id: 1, course_code: "2268-CSE-4321-002", name: "Testing" }] }));
  await page.route("**/api/courses/1/upcoming", (route) => route.fulfill({ json: [{ id: 1, name: "Quiz", due_at: new Date(Date.now() + 86400000).toISOString(), points_possible: 5, status: "open" }] }));
  await page.route("**/api/ingestion", (route) => route.fulfill({ json: { status: "completed" } }));
  await page.route("**/api/schedule", (route) => route.fulfill({ json: { courses: [{ course_id: 1, course_code: "2268-CSE-4321-002", name: "Testing", time_zone: "America/Chicago",
    meetings: [{ meeting_id: "1".padStart(32, "0"), course_id: 1, kind: "lecture", days: ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"], start_time: "00:10", end_time: "23:50", location: null, source: "manual", source_text: null, fingerprint: null }],
    suggestions: [], hints: [], dismissed_count: 0 }] } }));
  await page.goto("/");
  await looksLikeAButton(page.getByRole("link", { name: "View all upcoming" }));
  await looksLikeAButton(page.getByRole("link", { name: "View schedule" }));
});

test("Class Schedule actions are buttons, including the week arrows and the add buttons", async ({ page }) => {
  await page.route("**/api/schedule", (route) => route.fulfill({ json: { courses: [{ course_id: 1, course_code: "2268-CSE-4321-002", name: "Testing", time_zone: "America/Chicago",
    meetings: [{ meeting_id: "1".padStart(32, "0"), course_id: 1, kind: "lecture", days: ["TUE"], start_time: "14:00", end_time: "15:20", location: "WH 311", source: "manual", source_text: null, fingerprint: null }],
    suggestions: [], hints: [], dismissed_count: 0 }] } }));
  await page.goto("/class-schedule");
  for (const name of ["Add class time", "Add study block", "Edit", "Delete", "Next week"]) await looksLikeAButton(page.getByRole("button", { name, exact: true }).first());
});

test("the course page back link is a button", async ({ page }) => {
  const course = { course_id: 1, course_code: "2268-CSE-4321-002", name: "2268-CSE-4321-002-TESTING", time_zone: "America/Chicago", canvas_url: null, syllabus_url: null, synced_at: null,
    counts: { assignments: 0, modules: 0, files: 0, pages: 0, quizzes: 0, announcements: 0 }, meetings: [], announcements: [], modules: [], latest_announcement: null };
  await page.route("**/api/course-catalog", (route) => route.fulfill({ json: { courses: [course] } }));
  await page.route("**/api/courses/1/details", (route) => route.fulfill({ json: course }));
  await page.route("**/api/priorities**", (route) => route.fulfill({ status: 502, json: {} }));
  await page.route("**/api/search/status", (route) => route.fulfill({ json: { courses: [] } }));
  await page.goto("/courses/1");
  await looksLikeAButton(page.getByRole("link", { name: "← All courses" }));
});
