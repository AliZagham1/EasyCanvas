import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function load(context = {}) {
  const ctx = { exports: {}, Date, Intl, ...context };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL("../lib/study-plans.ts", import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017 } }).outputText, ctx);
  return ctx.exports;
}

test("minutes read naturally", () => {
  const { formatMinutes } = load();
  assert.equal(formatMinutes(45), "45 min");
  assert.equal(formatMinutes(60), "1 h");
  assert.equal(formatMinutes(90), "1 h 30 min");
  assert.equal(formatMinutes(0), "0 min");
  assert.equal(formatMinutes(-5), "0 min");
});

test("a session shows its day and time range, with the meridiem once when shared", () => {
  const { formatSession } = load();
  const same = formatSession("2026-11-18T15:00:00Z", "2026-11-18T16:00:00Z");
  assert.match(same, / · /);
  assert.match(same, / – /);
  assert.equal(formatSession("nope", "2026-11-18T16:00:00Z"), "Time unavailable");
});

test("the plan summary counts steps, what is left, and what is scheduled", () => {
  const { planSummary } = load();
  const step = (over) => ({ done: false, start_at: null, ...over });
  assert.equal(planSummary({ activities: [], needed_minutes: 0, scheduled_minutes: 0 }), "No steps yet");
  assert.equal(planSummary({ activities: [step({}), step({ start_at: "x" })], needed_minutes: 90, scheduled_minutes: 45 }), "2 steps · 1 h 30 min left · 1 of 2 scheduled");
  assert.equal(planSummary({ activities: [step({ done: true }), step({})], needed_minutes: 30, scheduled_minutes: 0 }), "2 steps · 1 done · 30 min left · 0 of 1 scheduled");
  assert.equal(planSummary({ activities: [step({ done: true })], needed_minutes: 0, scheduled_minutes: 0 }), "1 step · 1 done");
});

test("local date and time become an ISO instant, and incomplete input becomes null", () => {
  const { localToIso } = load();
  assert.match(localToIso("2026-12-01", "15:30"), /^2026-12-0[12]T\d\d:30:00\.000Z$/);
  assert.equal(localToIso("", "15:30"), null);
  assert.equal(localToIso("2026-12-01", ""), null);
  assert.equal(localToIso("nope", "15:30"), null);
});

test("requests go to the study-plans API and surface the backend's message", async () => {
  const calls = [];
  const ok = load({ fetch: async (url, init) => { calls.push([url, init]); return { ok: true, json: async () => ({ plans: [], preferences: {} }) }; } });
  await ok.getPlans();
  await ok.createPlan({ course_id: 1, title: "Exam", due_at: "2026-12-01T00:00:00Z" });
  await ok.acceptTimes("p", [{ activity_id: "a", start_at: "s", end_at: "e" }]);
  assert.equal(calls[0][0], "http://127.0.0.1:8000/api/study-plans");
  assert.equal(calls[1][1].method, "POST");
  assert.deepEqual(JSON.parse(calls[2][1].body), { sessions: [{ activity_id: "a", start_at: "s", end_at: "e" }] });
  const failing = load({ fetch: async () => ({ ok: false, json: async () => ({ detail: "That deadline has already passed." }) }) });
  await assert.rejects(failing.createPlan({ course_id: 1 }), /already passed/);
  const generic = load({ fetch: async () => ({ ok: false, json: async () => { throw new Error("no body"); } }) });
  await assert.rejects(generic.getPlans(), /study plan request failed/);
});

test("the time picker starts at the next sensible hour and understands existing sessions", () => {
  const { defaultPickerTime, pickerTimeFor, endTimeText } = load();
  assert.deepEqual({ ...defaultPickerTime(new Date(2026, 10, 17, 14, 20)) }, { date: "2026-11-17", time: "15:00" });
  assert.deepEqual({ ...defaultPickerTime(new Date(2026, 10, 17, 21, 30)) }, { date: "2026-11-18", time: "09:00" });    // late evening: tomorrow morning
  assert.deepEqual({ ...defaultPickerTime(new Date(2026, 10, 17, 20, 10)) }, { date: "2026-11-18", time: "09:00" });
  assert.deepEqual({ ...defaultPickerTime(new Date(2026, 10, 17, 3, 10)) }, { date: "2026-11-17", time: "09:00" });     // small hours: that morning
  assert.deepEqual({ ...defaultPickerTime(new Date(2026, 10, 17, 6, 30)) }, { date: "2026-11-17", time: "07:00" });
  assert.deepEqual({ ...pickerTimeFor(new Date(2026, 10, 18, 9, 5).toISOString()) }, { date: "2026-11-18", time: "09:05" });
  assert.match(endTimeText("2026-11-18", "15:00", 90), /4:30/);
  assert.equal(endTimeText("", "15:00", 90), "");
});

test("the saved-time message says what was replaced and what it overlaps", () => {
  const { savedTimeMessage } = load();
  assert.equal(savedTimeMessage({ replaced: [], overlaps: [] }), "Time saved. It now appears on your Class Schedule.");
  assert.match(savedTimeMessage({ replaced: [{ activity_id: "a", title: "Read (Homework 7)" }], overlaps: [] }), /replaced Read \(Homework 7\), which is now not scheduled/);
  assert.match(savedTimeMessage({ replaced: [], overlaps: [{ kind: "class", title: "CSE 3302 lecture", when: "x" }] }), /overlaps CSE 3302 lecture, which stays where it is/);
});

test("a booked time (409 with conflicts) becomes a TimeConflict; other failures stay ordinary errors", async () => {
  const conflicts = [{ kind: "class", title: "CSE 3302 lecture", when: "2:00 PM – 3:20 PM" }];
  const calls = [];
  const booked = load({ fetch: async (url, init) => { calls.push([url, init]); return { ok: false, status: 409, json: async () => ({ detail: "You already have something at this time.", conflicts }) }; } });
  await assert.rejects(booked.setStepTime("p", "s", "2026-11-18T16:00:00Z", false), (error) => error.name === "TimeConflict" && error.conflicts[0].title === "CSE 3302 lecture");
  assert.equal(calls[0][0], "http://127.0.0.1:8000/api/study-plans/p/activities/s/time");
  assert.equal(calls[0][1].method, "PUT");
  assert.deepEqual(JSON.parse(calls[0][1].body), { start_at: "2026-11-18T16:00:00Z", replace_conflicts: false });
  const plain409 = load({ fetch: async () => ({ ok: false, status: 409, json: async () => ({ detail: "Something else." }) }) });
  await assert.rejects(plain409.setStepTime("p", "s", "x", false), (error) => error.name !== "TimeConflict" && /Something else/.test(error.message));
  const past = load({ fetch: async () => ({ ok: false, status: 422, json: async () => ({ detail: "That time has already passed." }) }) });
  await assert.rejects(past.setStepTime("p", "s", "x", true), /already passed/);
});
