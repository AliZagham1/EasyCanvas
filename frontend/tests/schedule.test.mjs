import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function load(fetch = async () => { throw new Error("no network"); }) {
  const context = { exports: {}, fetch, Intl, Date, URL };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL("../lib/schedule.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017 },
  }).outputText, context);
  return context.exports;
}
const S = load();
const plain = (value) => JSON.parse(JSON.stringify(value)); // vm-realm objects -> main-realm values for deepEqual
let seq = 0;
const meeting = (days, start, end, over = {}) => ({ meeting_id: String(++seq).padStart(32, "0"), course_id: 1, kind: "lecture", days, start_time: start, end_time: end,
  location: null, source: "manual", source_text: null, fingerprint: null, ...over });
const course = (meetings, over = {}) => ({ course_id: 1, course_code: "2268-CSE-4321-002", name: "Testing", time_zone: "America/Chicago", meetings, suggestions: [], hints: [], dismissed_count: 0, ...over });
const CHI_TUE_NOON = Date.parse("2026-11-17T18:00:00Z"); // Tuesday 12:00 in Chicago (UTC-6), 03:00 Wednesday in Tokyo

test("times and days are formatted for reading", () => {
  assert.equal(S.formatTime("00:00"), "12:00 AM");
  assert.equal(S.formatTime("12:05"), "12:05 PM");
  assert.equal(S.formatTime("15:30"), "3:30 PM");
  assert.equal(S.formatRange("09:30", "10:50"), "9:30 AM – 10:50 AM");
  assert.equal(S.formatDays(["THU", "TUE"]), "Tue, Thu");
  assert.equal(S.formatDays(["MON", "WED", "FRI"]), "Mon, Wed, Fri");
  assert.equal(S.formatDays(["MON", "TUE", "WED", "THU", "FRI"]), "Mon–Fri");
  assert.equal(S.courseLabel({ course_code: "2268-CSE-4321-002", name: "x" }), "CSE 4321");
  assert.equal(S.courseLabel({ course_code: null, name: "Seminar" }), "Seminar");
});

test("week layout shows Mon-Fri, adds weekend days only when used, and grows the hour range", () => {
  const empty = S.layoutWeek([]);
  assert.deepEqual(plain([empty.days, empty.startHour, empty.endHour]), [["MON", "TUE", "WED", "THU", "FRI"], 8, 18]);
  const wide = S.layoutWeek([meeting(["SAT"], "07:30", "09:00"), meeting(["TUE"], "17:30", "20:20")]);
  assert.deepEqual(plain([wide.days.includes("SAT"), wide.days.includes("SUN"), wide.startHour, wide.endHour]), [true, false, 7, 21]);
});

test("overlapping meetings sit side by side; back-to-back meetings do not", () => {
  const a = meeting(["TUE"], "09:00", "10:30"), b = meeting(["TUE"], "10:00", "11:00"), c = meeting(["TUE"], "11:00", "12:00");
  const blocks = S.layoutWeek([a, b, c]).blocks.filter((block) => block.day === "TUE");
  const byId = Object.fromEntries(blocks.map((block) => [block.meeting.meeting_id, block]));
  assert.deepEqual(plain([byId[a.meeting_id].column, byId[a.meeting_id].columns, byId[b.meeting_id].column, byId[b.meeting_id].columns]), [0, 2, 1, 2]);
  assert.deepEqual(plain([byId[c.meeting_id].column, byId[c.meeting_id].columns]), [0, 1]); // starts when b ends
  const triple = [meeting(["MON"], "09:00", "10:00"), meeting(["MON"], "09:15", "10:00"), meeting(["MON"], "09:30", "10:00")];
  assert.deepEqual(plain(S.layoutWeek(triple).blocks.filter((x) => x.day === "MON").map((x) => [x.column, x.columns])), [[0, 3], [1, 3], [2, 3]]);
});

test("a meeting appears on each of its days, and layout is deterministic", () => {
  const list = [meeting(["TUE", "THU"], "11:00", "12:20"), meeting(["TUE"], "11:30", "12:00")];
  const first = S.layoutWeek(list), second = S.layoutWeek([...list].reverse());
  assert.equal(first.blocks.filter((block) => block.meeting.meeting_id === list[0].meeting_id).length, 2);
  const key = (layout) => plain(layout.blocks.map((block) => [block.day, block.meeting.meeting_id, block.column, block.columns]).sort());
  assert.deepEqual(key(first), key(second));
});

test("next class: in progress first, then the soonest, using the course's own time zone", () => {
  const during = meeting(["TUE", "THU"], "11:00", "12:20"), later = meeting(["TUE"], "14:00", "15:15");
  let next = S.nextClass([course([later, during])], CHI_TUE_NOON);
  assert.equal(next.meeting.meeting_id, during.meeting_id);
  assert.deepEqual(plain([next.inProgress, next.minutes]), [true, 20]);
  assert.equal(S.describeNext(next), "In progress, ends in 20 min");
  next = S.nextClass([course([later])], CHI_TUE_NOON);
  assert.deepEqual(plain([next.inProgress, next.minutes, next.day]), [false, 120, "TUE"]);
  assert.equal(S.describeNext(next), "Starts in 2 h");
  const after = Date.parse("2026-11-17T19:00:00Z"); // 13:00 Tuesday: the 11:00 class has ended
  assert.equal(S.nextClass([course([during])], after).day, "THU");
  assert.equal(S.describeNext(S.nextClass([course([during])], after)), "On Thursday");
  assert.equal(S.describeNext({ ...S.nextClass([course([during])], after), daysAhead: 1, minutes: 20 * 60 }), "Tomorrow");
  assert.equal(S.describeNext({ ...S.nextClass([course([during])], after), daysAhead: 1, minutes: 90 }), "Starts in 1 h 30 min"); // just after midnight
});

test("next class wraps past the weekend and handles a class that starts exactly now", () => {
  const friday = Date.parse("2026-11-20T23:00:00Z"); // Friday 17:00 Chicago
  const next = S.nextClass([course([meeting(["MON"], "09:00", "10:00")])], friday);
  assert.deepEqual(plain([next.day, next.minutes]), ["MON", 2 * 1440 + 16 * 60]);
  const exactly = S.nextClass([course([meeting(["TUE"], "12:00", "13:00")])], CHI_TUE_NOON);
  assert.deepEqual(plain([exactly.inProgress, exactly.minutes]), [true, 60]); // 12:00 start == now: it has begun
  const justEnded = S.nextClass([course([meeting(["TUE"], "11:00", "12:00")])], CHI_TUE_NOON);
  assert.deepEqual(plain([justEnded.inProgress, justEnded.minutes]), [false, 7 * 1440 - 60]); // ended at 12:00: next is next week
});

test("time zones decide which weekday 'today' is, and a bad zone falls back instead of throwing", () => {
  const tuesdayClass = meeting(["TUE"], "09:00", "10:00");
  const tokyo = S.nextClass([course([tuesdayClass], { time_zone: "Asia/Tokyo" })], CHI_TUE_NOON); // it is already Wednesday 03:00 in Tokyo
  assert.ok(tokyo.minutes > 6 * 1440 && tokyo.minutes < 7 * 1440, String(tokyo.minutes));
  const chicago = S.nextClass([course([tuesdayClass])], CHI_TUE_NOON);
  assert.ok(chicago.minutes > 6 * 1440);                // 09:00 Tuesday already passed in Chicago too
  assert.doesNotThrow(() => S.nextClass([course([tuesdayClass], { time_zone: "Not/AZone" })], CHI_TUE_NOON));
  assert.doesNotThrow(() => S.nextClass([course([tuesdayClass], { time_zone: null })], CHI_TUE_NOON));
});

test("no confirmed meetings means no next class, and suggestions are never counted", () => {
  assert.equal(S.nextClass([], CHI_TUE_NOON), null);
  const suggestionsOnly = course([], { suggestions: [{ fingerprint: "a".repeat(16), days: ["TUE"], start_time: "13:00", end_time: "14:00", location: null, kind: "lecture", source_text: "x", confidence: "high" }] });
  assert.equal(S.nextClass([suggestionsOnly], CHI_TUE_NOON), null);
});

test("the soonest meeting across several courses wins", () => {
  const a = course([meeting(["TUE"], "15:00", "16:00", { course_id: 1 })]);
  const b = course([meeting(["TUE"], "13:00", "14:00", { course_id: 2 })], { course_id: 2, course_code: "2268-CSE-3302-002" });
  assert.equal(S.nextClass([a, b], CHI_TUE_NOON).course.course_id, 2);
});

test("API client: creating from a suggestion sends its origin; errors show only safe backend text", async () => {
  const calls = []; let reply = { ok: true, json: async () => ({ meeting_id: "x" }) };
  const api = load(async (url, init) => { calls.push([url, init]); return reply; });
  await api.createMeeting(7, { days: ["TUE"], start_time: "09:00", end_time: "10:00", location: "NH 1" }, { fingerprint: "a".repeat(16), source_text: "Tue 9-10" });
  assert.equal(calls[0][0], "http://127.0.0.1:8000/api/schedule/meetings");
  assert.deepEqual(JSON.parse(calls[0][1].body), { course_id: 7, days: ["TUE"], start_time: "09:00", end_time: "10:00", location: "NH 1", source: "syllabus", source_text: "Tue 9-10", fingerprint: "a".repeat(16) });
  await api.createMeeting(7, { days: ["TUE"], start_time: "09:00", end_time: "10:00" });
  assert.equal(JSON.parse(calls[1][1].body).source, undefined);                     // manual entry carries no origin
  await api.updateMeeting("m1", { start_time: "10:00" }); await api.deleteMeeting("m1"); await api.dismissSuggestion(7, "b".repeat(16)); await api.restoreSuggestions(7);
  assert.deepEqual(calls.slice(2).map((c) => [c[0].replace("http://127.0.0.1:8000/api/schedule", ""), c[1].method]),
    [["/meetings/m1", "PUT"], ["/meetings/m1", "DELETE"], ["/suggestions/dismiss", "POST"], ["/courses/7/dismissed", "DELETE"]]);
  reply = { ok: false, json: async () => ({ detail: "The end time must be after the start time." }) };
  await assert.rejects(api.createMeeting(7, { days: ["TUE"], start_time: "10:00", end_time: "09:00" }), /end time must be after/);
  reply = { ok: false, json: async () => ({ detail: [{ loc: ["body", "days"], msg: "x", input: "secret-ish" }] }) };
  await assert.rejects(api.createMeeting(7, { days: [], start_time: "1", end_time: "2" }), (error) => { assert.doesNotMatch(error.message, /secret-ish/); assert.match(error.message, /Check the values/); return true; });
  reply = { ok: false, json: async () => { throw new Error("html"); } };
  await assert.rejects(api.deleteMeeting("m1"), /schedule request failed/);
});

test("getSchedule validates the response shape", async () => {
  const ok = load(async () => ({ ok: true, json: async () => ({ courses: [] }) }));
  assert.deepEqual(plain(await ok.getSchedule()), { courses: [] });
  await assert.rejects(load(async () => ({ ok: true, json: async () => ({ nope: 1 }) })).getSchedule(), /Invalid schedule response/);
});

test("course titles drop the Canvas term and code prefix", () => {
  assert.equal(S.courseTitle("2268-CSE-3380-003-LINEAR ALGEBRA FOR CSE"), "LINEAR ALGEBRA FOR CSE");
  assert.equal(S.courseTitle("2268-CSE-3311-001-OBJECT-ORIENTED SOFTWARE ENGRG"), "OBJECT-ORIENTED SOFTWARE ENGRG");
  assert.equal(S.courseTitle("Intro to Databases"), "Intro to Databases");
  assert.equal(S.courseTitle(null), "");
  assert.equal(S.courseTitle("2268-CSE-3380-003-"), "2268-CSE-3380-003-");   // nothing left: keep the original
});
