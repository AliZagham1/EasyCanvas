import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function load(fetch = async () => { throw new Error("no network in ranking tests"); }) {
  const context = { exports: {}, fetch, Intl, Date, URL };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL("../lib/priorities.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017 },
  }).outputText, context);
  return context.exports;
}
const P = load();
const CHI = "America/Chicago"; // UTC-6 in November
const NOW = Date.parse("2026-11-17T18:00:00Z"); // 12:00 local in Chicago
const H = 3_600_000;
const at = (hours) => new Date(NOW + hours * H).toISOString();
let seq = 0;
const item = (over = {}) => ({
  id: ++seq, course_id: 1, course_code: "CSE 3311", course_name: "Software", name: `Task ${seq}`,
  html_url: `https://canvas.example/courses/1/assignments/${seq}`, due_at: at(48), lock_at: null, unlock_at: null,
  locked_for_user: false, points_possible: 10, submission_types: ["online_upload"], is_group: false,
  submission_status: "incomplete", submitted_at: null, missing: false, late: false, ...over,
});
const view = (items, filters, sort, zone = CHI, now = NOW) => P.buildPriorityView(items, now, filters, sort, zone);
// Results come from a vm context; copy into main-realm arrays so deepEqual compares by value.
const names = (list) => Array.from(list, (i) => i.name);

test("High, Medium and Low use the exact 24h and 72h boundaries", () => {
  const items = [
    item({ name: "eight", due_at: at(8) }), item({ name: "h24", due_at: at(24) }), item({ name: "h24p", due_at: at(24 + 1 / 60) }),
    item({ name: "h72", due_at: at(72) }), item({ name: "h72p", due_at: at(72 + 1 / 60) }), item({ name: "week", due_at: at(170) }),
  ];
  const levels = Object.fromEntries(Array.from(view(items).upcoming, (i) => [i.name, i.level]));
  assert.deepEqual(levels, { eight: "high", h24: "high", h24p: "medium", h72: "medium", h72p: "low", week: "low" });
});

test("upcoming work is sorted by earliest deadline, regardless of input order", () => {
  const items = [item({ name: "c", due_at: at(100) }), item({ name: "a", due_at: at(5) }), item({ name: "b", due_at: at(30) })];
  assert.deepEqual(names(view(items).upcoming), ["a", "b", "c"]);
  assert.deepEqual(names(view([...items].reverse()).upcoming), ["a", "b", "c"]);
});

test("overdue open work has its own section, most recently overdue first, labeled High", () => {
  const result = view([item({ name: "soon", due_at: at(1) }), item({ name: "old", due_at: at(-24 * 40) }), item({ name: "recent", due_at: at(-3) }), item({ name: "mid", due_at: at(-72) })]);
  assert.deepEqual(names(result.overdue), ["recent", "mid", "old"]);
  assert.deepEqual(names(result.upcoming), ["soon"]);
  assert.ok(result.overdue.every((i) => i.overdue && i.level === "high"));
  assert.equal(result.overdue[0].dueLabel, "Overdue by 3 hours");
  assert.equal(result.overdue[2].dueLabel, "Overdue by 40 days");
  assert.equal(result.overdue[0].section, "overdue");
});

test("a deadline of exactly now counts as overdue", () => {
  assert.deepEqual(names(view([item({ name: "now", due_at: at(0) })]).overdue), ["now"]);
});

test("summary counts are mutually exclusive, so nothing is counted twice", () => {
  const items = [item({ due_at: at(-2) }), item({ due_at: at(5) }), item({ due_at: at(30) }), item({ due_at: at(60) }), item({ due_at: at(200) }),
    item({ submission_status: "submitted" }), item({ due_at: null })];
  const { counts } = view(items);
  assert.deepEqual({ ...counts }, { overdue: 1, within24h: 1, within72h: 2, completed: 1 });
});

test("deadline text names the time left and has no grade language", () => {
  const [a] = view([item({ due_at: at(8) })]).upcoming;
  assert.equal(a.dueLabel, "Due in 8 hours");
  assert.equal(view([item({ due_at: at(0.5) })]).upcoming[0].dueLabel, "Due in 30 minutes");
  assert.equal(JSON.stringify(a).includes("grade_weight"), false);
});

test("submitted and graded work is completed, hidden on request, and counted", () => {
  const items = [item({ name: "done", submission_status: "submitted" }), item({ name: "graded", submission_status: "graded" }), item({ name: "open" })];
  const shown = view(items);
  assert.deepEqual(names(shown.completed).sort(), ["done", "graded"]);
  assert.equal(shown.counts.completed, 2);
  assert.deepEqual(names(view(items, { courseId: "all", level: "all", showCompleted: false }).completed), []);
  assert.deepEqual(names(shown.upcoming), ["open"]);
});

test("missing due dates are unscheduled, never given urgency", () => {
  const result = view([item({ name: "nodue", due_at: null }), item({ name: "badstring", due_at: "garbage" })]);
  assert.equal(result.upcoming.length + result.overdue.length, 0);
  assert.deepEqual(names(result.unscheduled), ["badstring", "nodue"]);
  assert.equal(result.unscheduled[0].level, null);
});

test("on-paper, unknown and missing statuses are labeled and never completed", () => {
  const result = view([
    item({ name: "paper", submission_status: "unverified", submission_types: ["on_paper"], due_at: at(10) }),
    item({ name: "mystery", submission_status: "unknown", due_at: at(11) }),
    item({ name: "missing", missing: true, due_at: at(12) }),
    item({ name: "plain", due_at: at(13) }),
  ]);
  assert.equal(result.completed.length, 0);
  const labels = Object.fromEntries(Array.from(result.upcoming, (i) => [i.name, i.statusLabel]));
  assert.deepEqual(labels, { paper: "Not tracked online", mystery: "Status unavailable", missing: "Missing", plain: "Not submitted" });
  assert.equal(result.upcoming.length, 4); // still ranked by deadline, not dropped
});

test("closed, locked, excused and not-yet-open work is not actionable", () => {
  const result = view([
    item({ name: "closed", due_at: at(-5), lock_at: at(-1) }),
    item({ name: "locked", due_at: at(5), locked_for_user: true }),
    item({ name: "excused", submission_status: "excused" }),
    item({ name: "future", unlock_at: at(30), locked_for_user: true, due_at: at(60) }),
    item({ name: "open", due_at: at(5), lock_at: at(20) }),
  ]);
  assert.deepEqual(names(result.upcoming), ["open"]);
  assert.equal(result.overdue.length, 0); // a closed overdue assignment is not recommended as work
  assert.deepEqual(names(result.notActionable).sort(), ["closed", "excused", "future", "locked"]);
  const label = (n) => result.notActionable.find((i) => i.name === n).dueLabel;
  assert.deepEqual([label("closed"), label("locked"), label("future"), label("excused")], ["Closed", "Locked", "Not open yet", ""]);
  assert.equal(result.notActionable.find((i) => i.name === "excused").statusLabel, "Excused");
  assert.ok(result.notActionable.every((i) => i.level === null));
});

test("time zone decides 'tomorrow' and local days", () => {
  const lateNow = Date.parse("2026-11-18T05:00:00Z"); // 23:00 Nov 17 in Chicago
  const label = (due, zone = CHI) => view([item({ due_at: due })], undefined, undefined, zone, lateNow).upcoming[0].dueLabel;
  assert.equal(label("2026-11-18T07:00:00Z"), "Due in 2 hours");
  assert.equal(label("2026-11-19T05:30:00Z"), "Due tomorrow"); // 23:30 Nov 18 local, 24.5h away
  assert.equal(label("2026-11-19T07:00:00Z"), "Due in 2 days"); // 01:00 Nov 19 local
  // Tokyo (UTC+9): both instants fall on Nov 19 local, the day after Nov 18 14:00 local "now".
  assert.equal(label("2026-11-19T05:30:00Z", "Asia/Tokyo"), "Due tomorrow");
  assert.equal(label("2026-11-19T07:00:00Z", "Asia/Tokyo"), "Due tomorrow"); // 16:00 Nov 19 in Tokyo
  assert.doesNotThrow(() => view([item({ due_at: at(5) })], undefined, undefined, "Not/AZone"));
});

test("course filter, priority filter and course sort", () => {
  const items = [
    item({ name: "m-low", course_id: 2, course_code: "MATH 1", due_at: at(100) }),
    item({ name: "c-high", course_id: 1, due_at: at(9) }),
    item({ name: "m-high", course_id: 2, course_code: "MATH 1", due_at: at(20) }),
    item({ name: "c-over", course_id: 1, due_at: at(-4) }),
    item({ name: "c-done", course_id: 1, submission_status: "graded" }),
  ];
  const all = { courseId: "all", level: "all", showCompleted: true };
  assert.deepEqual(names(view(items, { ...all, courseId: 1 }).upcoming), ["c-high"]);
  assert.deepEqual(view(items, { ...all, courseId: 1 }).counts.overdue, 1);
  assert.deepEqual(names(view(items, { ...all, courseId: 2 }).completed), []);
  assert.deepEqual(names(view(items, { ...all, level: "low" }).upcoming), ["m-low"]);
  const high = view(items, { ...all, level: "high" });
  assert.deepEqual([names(high.overdue), names(high.upcoming)], [["c-over"], ["c-high", "m-high"]]); // overdue counts as High
  assert.equal(high.completed.length + high.unscheduled.length + high.notActionable.length, 0);
  assert.deepEqual(names(view(items, all, "course").upcoming), ["c-high", "m-high", "m-low"]);
  assert.deepEqual(names(view(items, all, "due").upcoming), ["c-high", "m-high", "m-low"]);
});

test("Canvas link is preserved and a missing link stays missing", () => {
  const withLink = item({ html_url: "https://canvas.example/courses/9/assignments/77" });
  const [a, b] = view([withLink, item({ html_url: null, due_at: at(60) })]).upcoming;
  assert.equal(a.html_url, "https://canvas.example/courses/9/assignments/77");
  assert.equal(b.html_url, null);
});

test("API client: refresh query, error status and malformed payloads", async () => {
  const payload = { fetched_at: "t", cached: false, courses: [], assignments: [], failed_course_ids: [] };
  let seen;
  const ok = load(async (url, options) => { seen = [url, options]; return { ok: true, json: async () => payload }; });
  assert.deepEqual(await ok.getPriorities(), payload);
  assert.equal(seen[0], "http://127.0.0.1:8000/api/priorities");
  await ok.getPriorities({ refresh: true });
  assert.equal(seen[0], "http://127.0.0.1:8000/api/priorities?refresh=true");
  assert.equal(seen[1].cache, "no-store");
  await assert.rejects(load(async () => ({ ok: false, status: 502 })).getPriorities(), /HTTP 502/);
  await assert.rejects(load(async () => ({ ok: true, json: async () => ({ assignments: "x" }) })).getPriorities(), /Invalid priorities response/);
});

test("urgency groups use the same 24 hour and 72 hour limits as the priority levels", () => {
  const { urgencyBucket } = load();
  const now = Date.parse("2026-11-18T12:00:00Z");
  const hours = (n) => now + n * 3_600_000;
  assert.equal(urgencyBucket(hours(1), now), "soon");
  assert.equal(urgencyBucket(hours(24), now), "soon");
  assert.equal(urgencyBucket(hours(24.1), now), "next");
  assert.equal(urgencyBucket(hours(72), now), "next");
  assert.equal(urgencyBucket(hours(72.1), now), "later");
  assert.equal(urgencyBucket(hours(-2), now), "soon");   // a deadline that just passed is still the most urgent group
});
