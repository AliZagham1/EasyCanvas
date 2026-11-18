import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockConsent } from "./consent";

test.use({ timezoneId: "America/Chicago" });
test.beforeEach(async ({ page }) => { await mockConsent(page); });

const NOW = new Date("2026-11-17T18:00:00Z");                       // Tuesday 12:00 in Chicago; this week is Nov 16 to Nov 22
const meeting = { meeting_id: "1".padStart(32, "0"), course_id: 1, kind: "lecture", days: ["TUE"], start_time: "14:00", end_time: "15:20", location: "WH 311",
  source: "manual", source_text: null, fingerprint: null };
const schedule = { courses: [{ course_id: 1, course_code: "2268-CSE-4321-002", name: "2268-CSE-4321-002-TESTING", time_zone: "America/Chicago",
  meetings: [meeting], suggestions: [], hints: [], dismissed_count: 0 }] };

let n = 0;
const item = (name: string, due: string | null, over: Record<string, unknown> = {}) => ({ id: ++n, course_id: 1, course_code: "2268-CSE-4321-002", course_name: "Testing", name,
  html_url: `https://canvas.example/courses/1/assignments/${n}`, due_at: due, lock_at: null, unlock_at: null, locked_for_user: false, points_possible: 10,
  submission_types: ["online_upload"], is_group: false, submission_status: "incomplete", submitted_at: null, missing: false, late: false, ...over });

const assignments = () => [
  item("Wednesday quiz", "2026-11-18T23:00:00Z"),                                   // Wed 5:00 PM local
  item("Submitted homework", "2026-11-19T23:00:00Z", { submission_status: "submitted" }),
  item("Overdue lab", "2026-11-16T16:00:00Z"),                                      // Mon 10:00 AM, already past
  item("Undated reading", null),
  item("Next week project", "2026-11-25T05:59:00Z"),                                // Tue Nov 24, 11:59 PM local
  ...[1, 2, 3, 4, 5].map((i) => item(`Friday task ${i}`, `2026-11-20T${String(14 + i).padStart(2, "0")}:00:00Z`)),
];

async function open(page: Page, priorities: "ok" | "fail" = "ok") {
  await page.route("**/api/schedule", (route) => route.fulfill({ json: schedule }));
  await page.route("**/api/priorities**", (route) => priorities === "fail" ? route.fulfill({ status: 502, json: {} })
    : route.fulfill({ json: { fetched_at: NOW.toISOString(), cached: false, courses: [{ id: 1, course_code: "a", name: "a" }], assignments: assignments(), failed_course_ids: [] } }));
  await page.clock.setFixedTime(NOW);
  await page.goto("/class-schedule");
}

test("the week shows only unsubmitted, not-yet-due deadlines, on the right day, linking to Canvas", async ({ page }) => {
  await open(page);
  await expect(page.locator(".week-label")).toHaveText("Nov 16 – Nov 22");
  const wed = page.locator(".schedule-due-cell").nth(2);                                    // Mon Tue Wed ...
  await expect(wed.locator(".schedule-due")).toHaveCount(1);
  await expect(wed).toContainText("Wednesday quiz");
  await expect(wed).toContainText("5:00 PM");
  const link = wed.getByRole("link", { name: /Wednesday quiz, CSE 4321, due Wednesday at 5:00 PM/ });
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", /noopener/);
  const shown = page.locator(".schedule-head");
  await expect(shown).not.toContainText("Submitted homework");                              // completed work is not a deadline
  await expect(shown).not.toContainText("Overdue lab");                                    // already past
  await expect(shown).not.toContainText("Undated reading");                                // no date to place
  await expect(shown).not.toContainText("Next week project");                              // a different week
  await expect(page.getByText(/6 confirmed class times|1 confirmed class time/)).toContainText("deadlines");
});

test("a crowded day shows three deadlines and a link to the rest", async ({ page }) => {
  await open(page);
  const fri = page.locator(".schedule-due-cell").nth(4);
  await expect(fri.locator(".schedule-due")).toHaveCount(3);
  await expect(fri.getByRole("link", { name: "+2 more" })).toHaveAttribute("href", "/upcoming");
});

test("the week selector moves forward, returns to this week, and cannot go into the past", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("button", { name: "Previous week" })).toBeDisabled();
  await page.getByRole("button", { name: "Next week" }).click();
  await expect(page.locator(".week-label")).toHaveText("Nov 23 – Nov 29");
  await expect(page.getByRole("heading", { name: /Week of Nov 23/ })).toBeVisible();
  await expect(page.locator(".schedule-head")).toContainText("Next week project");
  await expect(page.locator(".schedule-head")).not.toContainText("Wednesday quiz");
  await expect(page.locator(".schedule-block")).toHaveCount(1);                              // classes repeat every week
  await page.getByRole("button", { name: "This week" }).click();
  await expect(page.getByRole("heading", { name: "This week" })).toBeVisible();
  await expect(page.locator(".schedule-head")).toContainText("Wednesday quiz");
});

test("classes still work, with a quiet note, when deadlines cannot be loaded", async ({ page }) => {
  await open(page, "fail");
  await expect(page.locator(".schedule-block")).toHaveCount(1);
  await expect(page.getByRole("status").filter({ hasText: "Deadlines could not be loaded" })).toBeVisible();
  await expect(page.locator(".schedule-due")).toHaveCount(0);
});

test("on a phone the agenda lists deadlines; on a wide screen they are not read twice", async ({ page }) => {
  await open(page);
  await expect(page.locator(".agenda-due")).toHaveCount(6);                                  // 1 on Wednesday and 5 on Friday
  await expect(page.locator(".agenda-due").first()).toBeHidden();                            // wide screen: the Due row carries them
  await page.setViewportSize({ width: 390, height: 800 });
  await expect(page.locator(".schedule-head")).toBeHidden();
  await expect(page.locator(".agenda-due").first()).toBeVisible();
  await expect(page.getByRole("listitem", { name: "Wednesday" })).toContainText("Due 5:00 PM");
  await expect(page.getByRole("listitem", { name: "Wednesday" })).toContainText("Wednesday quiz");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
