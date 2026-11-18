import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";
import { mockConsent } from "./consent";

test.use({ timezoneId: "America/Chicago" });
test.beforeEach(async ({ page }) => { await mockConsent(page); });

const NOW = new Date("2026-11-17T18:00:00Z");                        // Tuesday 12:00 in Chicago
const DUE = "2026-11-21T05:59:00Z";                                   // Friday 11:59 PM local

type Step = { activity_id: string; plan_id: string; position: number; title: string; minutes: number; done: boolean; start_at: string | null; end_at: string | null };
type Plan = { plan_id: string; course_id: number; kind: string; assignment_id: number | null; title: string; due_at: string; course_code: string; course_name: string; time_zone: string; activities: Step[]; needed_minutes: number; scheduled_minutes: number; done_minutes: number };
const hex = (n: number) => String(n).padStart(32, "0");

/** A small stateful stand-in for the backend, so every click is checked end to end. */
async function mockPlans(page: Page, options: { plans?: Plan[]; failLoad?: boolean; unplaced?: boolean; conflict?: "class" | "session" } = {}) {
  const state = { plans: options.plans ?? [], next: 1, calls: [] as { method: string; path: string; body: unknown }[], prefs: { day_start: "09:00", day_end: "21:00", max_minutes_per_day: 240, buffer_minutes: 120 } };
  const totals = (plan: Plan) => {
    const open = plan.activities.filter((s) => !s.done);
    plan.needed_minutes = open.reduce((n, s) => n + s.minutes, 0);
    plan.scheduled_minutes = open.filter((s) => s.start_at).reduce((n, s) => n + s.minutes, 0);
    plan.done_minutes = plan.activities.filter((s) => s.done).reduce((n, s) => n + s.minutes, 0);
  };
  await page.route("**/api/study-plans**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/study-plans", "") || "/";
    const method = request.method();
    const body = request.postDataJSON?.() ?? null;
    state.calls.push({ method, path, body });
    if (options.failLoad) return route.fulfill({ status: 503, json: { detail: "down" } });
    const ids = path.split("/").filter(Boolean);
    const plan = state.plans.find((p) => p.plan_id === ids[0]);
    if (path === "/" && method === "GET") return route.fulfill({ json: { plans: state.plans, preferences: state.prefs } });
    if (path === "/preferences") {
      if (body.day_start >= body.day_end) return route.fulfill({ status: 422, json: { detail: "Study hours must cover at least one hour." } });
      state.prefs = { ...state.prefs, ...body }; return route.fulfill({ json: state.prefs });
    }
    if (path === "/" && method === "POST") {
      const made: Plan = { plan_id: hex(state.next++), course_id: body.course_id, kind: body.assignment_id ? "assignment" : "custom", assignment_id: body.assignment_id ?? null,
        title: body.assignment_id ? "Homework 7" : body.title, due_at: body.assignment_id ? DUE : body.due_at, course_code: "2268-CSE-4321-002", course_name: "TESTING",
        time_zone: "America/Chicago", activities: [], needed_minutes: 0, scheduled_minutes: 0, done_minutes: 0 };
      state.plans.push(made); return route.fulfill({ status: 201, json: made });
    }
    if (!plan) return route.fulfill({ status: 404, json: { detail: "Plan not found." } });
    if (ids.length === 1 && method === "DELETE") { state.plans = state.plans.filter((p) => p !== plan); return route.fulfill({ json: { ok: true } }); }
    if (ids[1] === "activities" && ids.length === 2 && method === "POST") {
      const step: Step = { activity_id: hex(100 + state.next++), plan_id: plan.plan_id, position: plan.activities.length, title: body.title, minutes: body.minutes, done: false, start_at: null, end_at: null };
      plan.activities.push(step); totals(plan); return route.fulfill({ status: 201, json: step });
    }
    const step = plan.activities.find((s) => s.activity_id === ids[2]);
    if (ids[1] === "activities" && ids.length === 3 && step) {
      if (method === "PATCH") { Object.assign(step, body); totals(plan); return route.fulfill({ json: step }); }
      if (method === "DELETE") { plan.activities = plan.activities.filter((s) => s !== step); totals(plan); return route.fulfill({ json: { ok: true } }); }
    }
    if (ids[1] === "activities" && ids[3] === "time" && step && method === "PUT") {
      if (Date.parse(body.start_at) < NOW.getTime()) return route.fulfill({ status: 422, json: { detail: "That time has already passed." } });
      const found: { kind: string; title: string; when: string; activity_id?: string }[] = options.conflict === "class" ? [{ kind: "class", title: "CSE 4321 lecture", when: "2:00 PM – 3:20 PM" }]
        : options.conflict === "session" ? [{ kind: "session", title: "Read (Homework 7)", when: "2:00 PM – 3:00 PM", activity_id: hex(555) }] : [];
      if (found.length && !body.replace_conflicts) return route.fulfill({ status: 409, json: { detail: "You already have something at this time.", conflicts: found } });
      step.start_at = body.start_at; step.end_at = new Date(Date.parse(body.start_at) + step.minutes * 60000).toISOString(); totals(plan);
      return route.fulfill({ json: { activity: step, replaced: found.filter((c) => c.kind === "session").map((c) => ({ activity_id: c.activity_id, title: c.title })), overlaps: found.filter((c) => c.kind !== "session") } });
    }
    if (ids[1] === "activities" && ids[3] === "time" && step) { step.start_at = step.end_at = null; totals(plan); return route.fulfill({ json: { ok: true } }); }
    if (ids[1] === "suggest") {
      const waiting = plan.activities.filter((s) => !s.done && !s.start_at);
      if (options.unplaced) return route.fulfill({ json: { plan_id: plan.plan_id, placements: [], unplaced: waiting.map((s) => ({ activity_id: s.activity_id, reason: `No free gap of ${s.minutes} minutes before the deadline.` })), needed_minutes: 120, free_minutes: 60, past_deadline: false } });
      let hour = 15;
      const placements = waiting.map((s) => { const start = new Date(`2026-11-18T${hour}:00:00Z`); hour += 2; return { activity_id: s.activity_id, start_at: start.toISOString(), end_at: new Date(start.getTime() + s.minutes * 60000).toISOString() }; });
      return route.fulfill({ json: { plan_id: plan.plan_id, placements, unplaced: [], needed_minutes: 100, free_minutes: 900, past_deadline: false } });
    }
    if (ids[1] === "times" && method === "PUT") {
      for (const session of body.sessions) { const s = plan.activities.find((a) => a.activity_id === session.activity_id)!; s.start_at = session.start_at; s.end_at = session.end_at; }
      totals(plan); return route.fulfill({ json: plan.activities });
    }
    return route.fulfill({ status: 404, json: { detail: "?" } });
  });
  const priorities = { fetched_at: NOW.toISOString(), cached: false, courses: [{ id: 1, course_code: "2268-CSE-4321-002", name: "TESTING" }],
    assignments: [{ id: 7, course_id: 1, course_code: "2268-CSE-4321-002", course_name: "Testing", name: "Homework 7", html_url: "https://canvas.example/a/7", due_at: DUE, lock_at: null, unlock_at: null,
      locked_for_user: false, points_possible: 10, submission_types: ["online_upload"], is_group: false, submission_status: "incomplete", submitted_at: null, missing: false, late: false }], failed_course_ids: [] };
  await page.route("**/api/priorities**", (route) => route.fulfill({ json: priorities }));
  await page.clock.setFixedTime(NOW);
  return state;
}

test("an empty list explains what to do and offers a new plan", async ({ page }) => {
  await mockPlans(page);
  await page.goto("/study-plans");
  await expect(page.getByRole("heading", { name: "Study Plans", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "No plans yet" })).toBeVisible();
  await expect(page.getByText(/uses AI/)).toHaveCount(0);                                 // no AI notice on this page
});

test("a plan for a Canvas assignment sends only the ids, then collects steps and times", async ({ page }) => {
  const state = await mockPlans(page);
  await page.goto("/study-plans");
  await page.getByRole("button", { name: "New plan" }).first().click();
  await expect(page.getByRole("combobox", { name: /^Assignment/ })).toContainText("Homework 7");
  await page.getByRole("button", { name: "Create plan" }).click();
  const card = page.getByRole("region", { name: "Homework 7" });
  await expect(card).toBeVisible();
  expect(state.calls.find((c) => c.method === "POST" && c.path === "/")!.body).toEqual({ course_id: 1, assignment_id: 7 });   // title and deadline come from Canvas
  await expect(card).toContainText("Due");
  await expect(card).toContainText("No steps yet");

  await card.getByPlaceholder(/Add a step/).fill("Read chapter 4");
  await card.getByLabel("Time for this step").selectOption("60");
  await card.getByRole("button", { name: "Add step" }).click();
  await card.getByPlaceholder(/Add a step/).fill("Practice problems");
  await card.getByLabel("Time for this step").selectOption("45");
  await card.getByRole("button", { name: "Add step" }).click();
  await expect(card).toContainText("2 steps · 1 h 45 min left · 0 of 2 scheduled");

  await card.getByRole("button", { name: "Suggest times" }).click();
  const proposal = card.getByRole("region", { name: "Proposed times" });
  await expect(proposal).toContainText("Read chapter 4");
  await expect(proposal).toContainText("Practice problems");
  expect(state.plans[0].activities.every((s) => !s.start_at)).toBe(true);                    // suggesting saves nothing
  await proposal.getByRole("button", { name: "Discard" }).click();
  await expect(proposal).toHaveCount(0);

  await card.getByRole("button", { name: "Suggest times" }).click();
  await card.getByRole("button", { name: "Save these times" }).click();
  await expect(card).toContainText("2 of 2 scheduled");
  await expect(page.getByText("Times saved.")).toBeVisible();
  expect(state.calls.find((c) => c.method === "PUT" && c.path.endsWith("/times"))!.body).toMatchObject({ sessions: [{ activity_id: expect.any(String) }, { activity_id: expect.any(String) }] });
  await expect(card.getByRole("button", { name: "Suggest times" })).toBeDisabled();           // nothing left to place
});

test("steps can be finished, cleared of their time, and removed", async ({ page }) => {
  const state = await mockPlans(page);
  await page.goto("/study-plans");
  await page.getByRole("button", { name: "New plan" }).first().click();
  await page.getByRole("button", { name: "Create plan" }).click();
  const card = page.getByRole("region", { name: "Homework 7" });
  await card.getByPlaceholder(/Add a step/).fill("Outline");
  await card.getByRole("button", { name: "Add step" }).click();
  await card.getByRole("button", { name: "Suggest times" }).click();
  await card.getByRole("button", { name: "Save these times" }).click();
  await card.getByRole("button", { name: "Clear time" }).click();
  await expect(card).toContainText("Not scheduled");
  await card.getByRole("checkbox", { name: /Outline: mark done/ }).click();
  await expect(card.getByRole("checkbox", { name: /Outline: mark not done/ })).toBeChecked();
  await expect(card).toContainText("1 step · 1 done");
  expect(state.calls.some((c) => c.method === "PATCH" && JSON.stringify(c.body) === '{"done":true}')).toBe(true);
  await card.getByRole("button", { name: "Remove step Outline" }).click();
  await expect(card).toContainText("No steps yet");
});

test("when there is not enough time the plan says so instead of squeezing steps in", async ({ page }) => {
  await mockPlans(page, { unplaced: true });
  await page.goto("/study-plans");
  await page.getByRole("button", { name: "New plan" }).first().click();
  await page.getByRole("button", { name: "Create plan" }).click();
  const card = page.getByRole("region", { name: "Homework 7" });
  await card.getByPlaceholder(/Add a step/).fill("Big project");
  await card.getByLabel("Time for this step").selectOption("120");
  await card.getByRole("button", { name: "Add step" }).click();
  await card.getByRole("button", { name: "Suggest times" }).click();
  await expect(card.getByRole("alert")).toContainText("you need 2 h and have 1 h open");
  await expect(card.getByRole("alert")).toContainText("No free gap of 120 minutes");
  await expect(card.getByRole("button", { name: "Save these times" })).toHaveCount(0);
});

test("an exam or other date is planned from a name, course and date; assignments already planned are not offered again", async ({ page }) => {
  const state = await mockPlans(page);
  await page.goto("/study-plans");
  await page.getByRole("button", { name: "New plan" }).first().click();
  await page.getByLabel("An exam or other date").check();
  await page.getByRole("button", { name: "Create plan" }).click();
  await expect(page.locator(".plan-form [role=alert]")).toContainText("Give the plan a name");
  await page.getByLabel("Name", { exact: true }).fill("Midterm exam");
  await page.getByLabel("Date", { exact: true }).fill("2026-12-01");
  await page.getByLabel("Time", { exact: true }).fill("09:00");
  await page.getByRole("button", { name: "Create plan" }).click();
  await expect(page.getByRole("region", { name: "Midterm exam" })).toBeVisible();
  expect(state.calls.find((c) => c.method === "POST" && c.path === "/")!.body).toMatchObject({ course_id: 1, title: "Midterm exam", due_at: "2026-12-01T15:00:00.000Z" });

  await page.getByRole("button", { name: "New plan" }).first().click();
  await page.getByLabel("A Canvas assignment").check();
  await page.getByRole("button", { name: "Create plan" }).click();                          // creates the Homework 7 plan
  await page.getByRole("button", { name: "New plan" }).first().click();
  await expect(page.getByLabel("A Canvas assignment")).toBeDisabled();                      // Homework 7 now has a plan, so none is left to offer
});

test("deleting a plan takes two steps", async ({ page }) => {
  await mockPlans(page);
  await page.goto("/study-plans");
  await page.getByRole("button", { name: "New plan" }).first().click();
  await page.getByRole("button", { name: "Create plan" }).click();
  const card = page.getByRole("region", { name: "Homework 7" });
  await card.getByRole("button", { name: "Delete plan" }).click();
  await card.getByRole("button", { name: "Keep it" }).click();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Delete plan" }).click();
  await card.getByRole("button", { name: "Yes, delete" }).click();
  await expect(page.getByRole("heading", { name: "No plans yet" })).toBeVisible();
});

test("preferences save, and a rejected value shows the backend's message", async ({ page }) => {
  const state = await mockPlans(page);
  await page.goto("/study-plans");
  await page.getByText("Planning preferences").click();
  await page.getByLabel("Most study per day (minutes)").fill("120");
  await page.getByRole("button", { name: "Save preferences" }).click();
  await expect(page.getByText("Preferences saved.")).toBeVisible();
  expect(state.prefs.max_minutes_per_day).toBe(120);
  await page.getByLabel("Study hours start").fill("22:00");                                 // after the end time (21:00)
  await page.getByRole("button", { name: "Save preferences" }).click();
  await expect(page.locator(".coverage-notice[role=alert]")).toContainText("Study hours must cover at least one hour.");
});

test("a backend failure is shown plainly", async ({ page }) => {
  await mockPlans(page, { failLoad: true });
  await page.goto("/study-plans");
  await expect(page.locator(".list-message[role=alert]")).toContainText("Unable to load study plans");
});

test("saved sessions appear on the Class Schedule week as study blocks, and finished ones do not", async ({ page }) => {
  const step = (id: string, title: string, start: string, end: string, done = false): Step => ({ activity_id: hex(id.length * 7), plan_id: hex(1), position: 0, title, minutes: 60, done, start_at: start, end_at: end });
  const plan: Plan = { plan_id: hex(1), course_id: 1, kind: "custom", assignment_id: null, title: "Exam", due_at: "2026-11-30T15:00:00Z", course_code: "2268-CSE-4321-002", course_name: "TESTING", time_zone: "America/Chicago",
    activities: [step("a", "Review slides", "2026-11-18T15:00:00Z", "2026-11-18T16:00:00Z"), step("bb", "Finished step", "2026-11-18T18:00:00Z", "2026-11-18T19:00:00Z", true), step("ccc", "Next week", "2026-11-25T15:00:00Z", "2026-11-25T16:00:00Z")],
    needed_minutes: 120, scheduled_minutes: 120, done_minutes: 60 };
  await mockPlans(page, { plans: [plan] });
  await page.route("**/api/schedule", (route) => route.fulfill({ json: { courses: [{ course_id: 1, course_code: "2268-CSE-4321-002", name: "2268-CSE-4321-002-TESTING", time_zone: "America/Chicago",
    meetings: [{ meeting_id: hex(9), course_id: 1, kind: "lecture", days: ["TUE"], start_time: "14:00", end_time: "15:20", location: "WH 311", source: "manual", source_text: null, fingerprint: null }], suggestions: [], hints: [], dismissed_count: 0 }] } }));
  await page.goto("/class-schedule");
  const blocks = page.locator(".schedule-block.is-study");
  await expect(blocks).toHaveCount(1);
  await expect(blocks.first()).toContainText("Study · CSE 4321");
  await expect(blocks.first()).toContainText("Review slides");
  await expect(page.getByText(/1 study session/)).toBeVisible();
  await page.getByRole("button", { name: "Next week" }).click();
  await expect(blocks).toHaveCount(1);
  await expect(blocks.first()).toContainText("Next week");
  await expect(page.locator(".schedule-block")).toHaveCount(2);                              // that week's study session and the repeating class
});

async function planWithStep(page: Page, options: Parameters<typeof mockPlans>[1] = {}) {
  const state = await mockPlans(page, options);
  await page.goto("/study-plans");
  await page.getByRole("button", { name: "New plan" }).first().click();
  await page.getByRole("button", { name: "Create plan" }).click();
  const card = page.getByRole("region", { name: "Homework 7" });
  await card.getByPlaceholder(/Add a step/).fill("Read chapter 4");
  await card.getByLabel("Time for this step").selectOption("60");
  await card.getByRole("button", { name: "Add step" }).click();
  await expect(card).toContainText("Read chapter 4");
  return { state, card };
}

test("you can pick your own time for a step, and it is saved in your local time", async ({ page }) => {
  const { state, card } = await planWithStep(page);
  await card.getByRole("button", { name: /Pick the time for Read chapter 4/ }).click();
  const picker = card.getByRole("group", { name: /Choose a time for Read chapter 4/ });
  await expect(picker).toContainText("It takes 1 h");
  await picker.getByLabel("Date").fill("2026-11-18");
  await picker.getByLabel("Start").fill("10:00");
  await expect(picker).toContainText("Ends at 11:00 AM");
  await picker.getByRole("button", { name: "Save time" }).click();
  await expect(page.getByText("Time saved. It now appears on your Class Schedule.")).toBeVisible();
  expect(state.calls.find((c) => c.method === "PUT" && c.path.endsWith("/time"))!.body).toEqual({ start_at: "2026-11-18T16:00:00.000Z", replace_conflicts: false });
  await expect(card).toContainText("Wed, Nov 18 · 10:00 – 11:00 AM");
  await expect(picker).toHaveCount(0);
  await card.getByRole("button", { name: /Change the time for Read chapter 4/ }).click();         // editing starts from the saved time
  await expect(card.getByLabel("Start")).toHaveValue("10:00");
});

test("a booked time warns first; Choose another time saves nothing; Add anyway keeps the class and says so", async ({ page }) => {
  const { state, card } = await planWithStep(page, { conflict: "class" });
  await card.getByRole("button", { name: /Pick the time for Read chapter 4/ }).click();
  await card.getByLabel("Date").fill("2026-11-18");
  await card.getByLabel("Start").fill("14:30");
  await card.getByRole("button", { name: "Save time" }).click();
  const warning = card.getByRole("alert").filter({ hasText: "You already have something at this time" });
  await expect(warning).toContainText("CSE 4321 lecture · 2:00 PM – 3:20 PM");
  await expect(warning).toContainText("keeps classes and study blocks where they are, and replaces any other study session");
  await expect(card).toContainText("Not scheduled");

  await card.getByRole("button", { name: "Choose another time" }).click();
  await expect(warning).toHaveCount(0);
  expect(state.calls.some((c) => c.method === "PUT" && c.path.endsWith("/time") && (c.body as { replace_conflicts: boolean }).replace_conflicts)).toBe(false);
  await expect(card).toContainText("Not scheduled");

  await card.getByRole("button", { name: "Save time" }).click();
  await card.getByRole("button", { name: "Add anyway" }).click();
  await expect(page.getByText(/It overlaps CSE 4321 lecture, which stays where it is/)).toBeVisible();
  await expect(card).toContainText("Wed, Nov 18 · 2:30 – 3:30 PM");
  expect(state.calls.filter((c) => c.method === "PUT" && c.path.endsWith("/time")).map((c) => (c.body as { replace_conflicts: boolean }).replace_conflicts)).toEqual([false, false, true]);
});

test("adding anyway over another study session says that session was replaced", async ({ page }) => {
  const { card } = await planWithStep(page, { conflict: "session" });
  await card.getByRole("button", { name: /Pick the time for Read chapter 4/ }).click();
  await card.getByLabel("Date").fill("2026-11-18");
  await card.getByLabel("Start").fill("14:00");
  await card.getByRole("button", { name: "Save time" }).click();
  await expect(card.getByRole("alert")).toContainText("Read (Homework 7) · 2:00 PM – 3:00 PM");
  await card.getByRole("button", { name: "Add anyway" }).click();
  await expect(page.getByText(/It replaced Read \(Homework 7\), which is now not scheduled/)).toBeVisible();
});

test("a time that has passed is refused with the backend's reason and no way to add it anyway", async ({ page }) => {
  const { card } = await planWithStep(page);
  await card.getByRole("button", { name: /Pick the time for Read chapter 4/ }).click();
  await card.getByLabel("Date").fill("2026-11-16");
  await card.getByLabel("Start").fill("10:00");
  await card.getByRole("button", { name: "Save time" }).click();
  await expect(card.getByRole("alert")).toContainText("That time has already passed.");
  await expect(card.getByRole("button", { name: "Add anyway" })).toHaveCount(0);
  await card.getByRole("button", { name: "Cancel" }).click();
  await expect(card.getByRole("group", { name: /Choose a time/ })).toHaveCount(0);
});
