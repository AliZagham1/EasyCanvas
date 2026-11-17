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
  submission_status: "incomplete", submitted_at: null, missing: false, late: false,
  grade_weight: { percent: null, reason: "Course grading settings were not available." }, ...over,
});
const w = (percent) => ({ percent, reason: null });
const view = (items, filters, sort, zone = CHI, now = NOW) => P.buildPriorityView(items, now, filters, sort, zone);
const names = (list) => Array.from(list, (i) => i.name); // main-realm array (results come from a vm context)

test("urgency tiers and exact boundaries", () => {
  const items = [
    item({ name: "eight", due_at: at(8) }), item({ name: "h24", due_at: at(24) }), item({ name: "h24p", due_at: at(24 + 1 / 60) }),
    item({ name: "h72", due_at: at(72) }), item({ name: "h72p", due_at: at(72 + 1 / 60) }), item({ name: "week", due_at: at(170) }),
  ];
  const levels = Object.fromEntries(view(items).active.map((i) => [i.name, i.level]));
  assert.deepEqual(levels, { eight: "high", h24: "high", h24p: "medium", h72: "medium", h72p: "low", week: "low" });
});

test("overdue incomplete open work is High, first, and explained", () => {
  const result = view([item({ name: "soon", due_at: at(1) }), item({ name: "late", due_at: at(-30) }), item({ name: "slightly", due_at: at(-0.5) })]);
  assert.deepEqual(names(result.active), ["slightly", "late", "soon"]);
  assert.ok(result.active[0].overdue && result.active[0].level === "high");
  assert.match(result.active[1].explanation, /^High priority — Overdue by 30 hours; Canvas still accepts submissions\./);
  assert.equal(result.counts.overdue, 2);
});

test("summary counts are mutually exclusive, so no assignment is counted twice", () => {
  const items = [item({ due_at: at(-2) }), item({ due_at: at(5) }), item({ due_at: at(30) }), item({ due_at: at(60) }), item({ due_at: at(200) }),
    item({ submission_status: "submitted" }), item({ due_at: null })];
  const { counts, active } = view(items);
  assert.deepEqual({ ...counts }, { overdue: 1, within24h: 1, within72h: 2, completed: 1 });
  assert.equal(counts.overdue + counts.within24h + counts.within72h, active.filter((i) => i.level !== "low").length);
});

test("explanations name the deadline and say when grade weight is unavailable", () => {
  const [a] = view([item({ due_at: at(8) })]).active;
  assert.equal(a.explanation, "High priority — Due in 8 hours. Grade weight unavailable — ranked by deadline.");
  const [b] = view([item({ due_at: at(48), grade_weight: w(10) })]).active;
  assert.equal(b.explanation, "Medium priority — Due in 2 days.");
});

test("submitted and graded work is completed, hidden on request, and counted", () => {
  const items = [item({ name: "done", submission_status: "submitted" }), item({ name: "graded", submission_status: "graded" }), item({ name: "open" })];
  const shown = view(items);
  assert.deepEqual(names(shown.completed).sort(), ["done", "graded"]);
  assert.equal(shown.counts.completed, 2);
  assert.deepEqual(names(view(items, { courseId: "all", level: "all", showCompleted: false }).completed), []);
  assert.deepEqual(names(shown.active), ["open"]);
});

test("same local day: higher verified weight outranks earlier-due work", () => {
  const result = view([item({ name: "five", due_at: at(8), grade_weight: w(5) }), item({ name: "twentyfive", due_at: at(10), grade_weight: w(25) })]);
  assert.deepEqual(names(result.active), ["twentyfive", "five"]);
  assert.match(result.active[0].explanation, /Ranked above earlier work due the same day because of higher estimated grade weight/);
  assert.match(result.active[1].explanation, /Ranked after heavier-weighted work due the same day/);
  assert.equal(result.active[0].level, "high");
});

test("imminent deadlines are protected from heavier same-day work", () => {
  const near = item({ name: "near", due_at: at(2), grade_weight: w(1) });
  const heavy = item({ name: "heavy", due_at: at(9), grade_weight: w(40) });
  assert.deepEqual(names(view([heavy, near]).active), ["near", "heavy"]);
  // Exactly at the threshold is still protected; one minute later it is not.
  assert.equal(P.IMMINENT_HOURS, 3);
  assert.deepEqual(names(view([item({ name: "edge", due_at: at(3), grade_weight: w(1) }), heavy]).active), ["edge", "heavy"]);
  assert.deepEqual(names(view([item({ name: "past", due_at: at(3 + 1 / 60), grade_weight: w(1) }), heavy]).active), ["heavy", "past"]);
});

test("unavailable weights keep deadline order and are never treated as zero", () => {
  const items = [item({ name: "a", due_at: at(7), grade_weight: w(5) }), item({ name: "u", due_at: at(8) }), item({ name: "b", due_at: at(9), grade_weight: w(25) })];
  assert.deepEqual(names(view(items).active), ["b", "u", "a"]); // unknown item never moves
  const unknown = [item({ name: "x", due_at: at(9) }), item({ name: "y", due_at: at(8) })];
  assert.deepEqual(names(view(unknown).active), ["y", "x"]);
  const zero = [item({ name: "z", due_at: at(7), grade_weight: w(0) }), item({ name: "u2", due_at: at(8) })];
  assert.deepEqual(names(view(zero).active), ["z", "u2"]);
});

test("grade weight never reorders across different days or urgency tiers", () => {
  const nextDay = [item({ name: "today", due_at: at(8), grade_weight: w(1) }), item({ name: "tomorrow", due_at: at(20), grade_weight: w(60) })];
  assert.deepEqual(names(view(nextDay).active), ["today", "tomorrow"]);
  const oneHourVsThreeDays = [item({ name: "heavy3d", due_at: at(71), grade_weight: w(60) }), item({ name: "hour", due_at: at(1), grade_weight: w(1) })];
  assert.deepEqual(names(view(oneHourVsThreeDays).active), ["hour", "heavy3d"]);
});

test("time zone decides what 'same day' means", () => {
  // 04:30Z and 06:30Z on Nov 18. Chicago: Nov 17 22:30 and Nov 18 00:30 (different days). Tokyo: both Nov 18.
  const items = () => [item({ name: "light", due_at: "2026-11-18T04:30:00Z", grade_weight: w(2) }), item({ name: "heavy", due_at: "2026-11-18T06:30:00Z", grade_weight: w(30) })];
  assert.deepEqual(names(view(items(), undefined, undefined, CHI).active), ["light", "heavy"]);
  assert.deepEqual(names(view(items(), undefined, undefined, "Asia/Tokyo").active), ["heavy", "light"]);
});

test("'tomorrow' follows the local calendar, not 24-hour blocks", () => {
  const lateNow = Date.parse("2026-11-18T05:00:00Z"); // 23:00 Nov 17 in Chicago
  const [a] = view([item({ due_at: "2026-11-18T07:00:00Z" })], undefined, undefined, CHI, lateNow).active; // 01:00 Nov 18, 2h away
  assert.match(a.explanation, /Due in 2 hours/);
  const [b] = view([item({ due_at: "2026-11-19T07:00:00Z" })], undefined, undefined, CHI, lateNow).active; // 01:00 Nov 19
  assert.match(b.explanation, /Medium priority — Due in 2 days/);
  const [c] = view([item({ due_at: "2026-11-19T05:30:00Z" })], undefined, undefined, CHI, lateNow).active; // 23:30 Nov 18, 24.5h away
  assert.match(c.explanation, /Due tomorrow/);
});

test("invalid time zone falls back instead of throwing", () => {
  assert.doesNotThrow(() => view([item({ due_at: at(5) })], undefined, undefined, "Not/AZone"));
});

test("missing due dates are unscheduled, never given urgency", () => {
  const result = view([item({ name: "nodue", due_at: null }), item({ name: "badstring", due_at: "garbage" })]);
  assert.equal(result.active.length, 0);
  assert.deepEqual(names(result.unscheduled), ["badstring", "nodue"]);
  assert.equal(result.unscheduled[0].level, null);
  assert.deepEqual({ ...result.counts }, { overdue: 0, within24h: 0, within72h: 0, completed: 0 });
});

test("unknown and unverified submission status are labeled and never completed", () => {
  const result = view([
    item({ name: "paper", submission_status: "unverified", submission_types: ["on_paper"], due_at: at(10) }),
    item({ name: "mystery", submission_status: "unknown", due_at: at(11) }),
    item({ name: "missing", missing: true, due_at: at(12) }),
  ]);
  assert.equal(result.completed.length, 0);
  const byName = Object.fromEntries(result.active.map((i) => [i.name, i.statusLabel]));
  assert.match(byName.paper, /Not tracked online/);
  assert.equal(byName.mystery, "Submission status unavailable");
  assert.equal(byName.missing, "Marked missing in Canvas");
});

test("closed, locked, excused and not-yet-open work is not actionable", () => {
  const result = view([
    item({ name: "closed", due_at: at(-5), lock_at: at(-1) }),
    item({ name: "locked", due_at: at(5), locked_for_user: true }),
    item({ name: "excused", submission_status: "excused" }),
    item({ name: "future", unlock_at: at(30), locked_for_user: true, due_at: at(60) }),
    item({ name: "open", due_at: at(5), lock_at: at(20) }),
  ]);
  assert.deepEqual(names(result.active), ["open"]);
  assert.deepEqual(names(result.notActionable).sort(), ["closed", "excused", "future", "locked"]);
  const closed = result.notActionable.find((i) => i.name === "closed");
  assert.equal(closed.level, null);
  assert.match(closed.explanation, /not accepting submissions/);
  assert.match(result.notActionable.find((i) => i.name === "future").explanation, /Not available yet/);
});

test("course filter, priority filter and deadline sort", () => {
  const items = [
    item({ name: "m-low", course_id: 2, course_code: "MATH 1", due_at: at(100) }),
    item({ name: "c-high", course_id: 1, due_at: at(9), grade_weight: w(5) }),
    item({ name: "c-high2", course_id: 1, due_at: at(7), grade_weight: w(25) }),
    item({ name: "m-high", course_id: 2, course_code: "MATH 1", due_at: at(20) }),
  ];
  assert.deepEqual(names(view(items, { courseId: 1, level: "all", showCompleted: true }).active), ["c-high2", "c-high"]);
  assert.deepEqual(names(view(items, { courseId: "all", level: "low", showCompleted: true }).active), ["m-low"]);
  assert.deepEqual(names(view(items, undefined, "deadline").active), ["c-high2", "c-high", "m-high", "m-low"]);
  const hidden = view([...items, item({ name: "x", due_at: null })], { courseId: "all", level: "high", showCompleted: true });
  assert.equal(hidden.unscheduled.length, 0);
});

test("ranking is deterministic regardless of input order", () => {
  const items = [1, 2, 3, 4, 5, 6].map((n) => item({ name: `n${n}`, due_at: at(5 + (n % 3)), grade_weight: n % 2 ? w(n) : undefined }));
  const forward = names(view(items).active);
  assert.deepEqual(names(view([...items].reverse()).active), forward);
});

test("Canvas link is preserved and a missing link stays missing", () => {
  const withLink = item({ html_url: "https://canvas.example/courses/9/assignments/77" });
  const [a, b] = view([withLink, item({ html_url: null, due_at: at(60) })]).active;
  assert.equal(a.html_url, "https://canvas.example/courses/9/assignments/77");
  assert.equal(b.html_url, null);
});

test("grade weight text never turns unavailable into zero", () => {
  assert.equal(P.formatGradeWeight({ percent: null, reason: "x" }), "Unavailable");
  assert.equal(P.formatGradeWeight(undefined), "Unavailable");
  assert.equal(P.formatGradeWeight({ percent: 12.5, reason: null }), "~12.5%");
  assert.equal(P.formatGradeWeight({ percent: 0, reason: null }), "~0%");
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
