import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import ts from "typescript";

// Transpile the real libraries next to each other so the import between them resolves.
const dir = fs.mkdtempSync(path.join(path.resolve("tests"), ".courses-"));
const emit = (src, out, fix = (s) => s) => fs.writeFileSync(path.join(dir, out), fix(ts.transpileModule(fs.readFileSync(src, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText));
emit("lib/priorities.ts", "priorities.mjs");
emit("lib/courses.ts", "courses.mjs", (s) => s.replace('"./priorities"', '"./priorities.mjs"'));
const C = await import(pathToFileURL(path.join(dir, "courses.mjs")));
test.after(() => fs.rmSync(dir, { recursive: true, force: true }));

const NOW = Date.parse("2026-11-17T18:00:00Z");
const at = (hours) => new Date(NOW + hours * 3_600_000).toISOString();
let seq = 0;
const item = (course, over = {}) => ({ id: ++seq, course_id: course, course_code: `C${course}`, course_name: "n", name: `T${seq}`, html_url: "https://c/x",
  due_at: at(48), lock_at: null, unlock_at: null, locked_for_user: false, points_possible: 1, submission_types: [], is_group: false,
  submission_status: "incomplete", submitted_at: null, missing: false, late: false, ...over });

test("workload counts only the chosen course, using the Priorities rules", () => {
  const items = [item(1, { due_at: at(-5) }), item(1, { due_at: at(6) }), item(1, { due_at: at(50) }), item(1, { due_at: at(200) }),
    item(1, { submission_status: "submitted" }), item(1, { due_at: null }), item(2, { due_at: at(-5) }), item(2, { due_at: at(3) })];
  assert.deepEqual({ ...C.courseWorkload(items, 1, NOW) }, { overdue: 1, within24h: 1, within72h: 1, completed: 1, upcoming: 3, unscheduled: 1 });
  assert.deepEqual({ ...C.courseWorkload(items, 2, NOW) }, { overdue: 1, within24h: 1, within72h: 0, completed: 0, upcoming: 1, unscheduled: 0 });
  assert.deepEqual({ ...C.courseWorkload(items, 99, NOW) }, { overdue: 0, within24h: 0, within72h: 0, completed: 0, upcoming: 0, unscheduled: 0 });
});

test("closed and excused work never counts as upcoming", () => {
  const items = [item(1, { lock_at: at(-1), due_at: at(-2) }), item(1, { submission_status: "excused" }), item(1, { locked_for_user: true, due_at: at(5) })];
  const w = C.courseWorkload(items, 1, NOW);
  assert.deepEqual([w.overdue, w.upcoming, w.within24h], [0, 0, 0]);
});

test("workload wording is honest and ranks urgency", () => {
  const base = { overdue: 0, within24h: 0, within72h: 0, upcoming: 0, unscheduled: 0, completed: 0 };
  assert.deepEqual({ ...C.describeWorkload(base) }, { text: "Nothing due", tone: "calm" });
  assert.deepEqual({ ...C.describeWorkload({ ...base, upcoming: 2 }) }, { text: "2 upcoming, none due within 3 days", tone: "calm" });
  assert.deepEqual({ ...C.describeWorkload({ ...base, within72h: 3, upcoming: 3 }) }, { text: "3 due in the next 3 days", tone: "soon" });
  assert.deepEqual({ ...C.describeWorkload({ ...base, within24h: 1, within72h: 2 }) }, { text: "1 due within 24 hours", tone: "soon" });
  assert.deepEqual({ ...C.describeWorkload({ ...base, overdue: 2 }) }, { text: "2 overdue", tone: "alert" });
  assert.deepEqual({ ...C.describeWorkload({ ...base, overdue: 1, within24h: 1, within72h: 1 }) }, { text: "1 overdue · 2 due in the next 3 days", tone: "alert" });
});

test("dates and summaries format safely", () => {
  assert.equal(C.formatPosted(null), "");
  assert.equal(C.formatPosted("garbage"), "");
  assert.match(C.formatPosted("2026-11-10T10:00:00Z"), /Nov 1\d, 2026/);
  assert.equal(C.formatSynced(null), "Not synced yet");
  assert.equal(C.plural(1, "module"), "1 module");
  assert.equal(C.plural(1200, "file"), "1,200 files");
  assert.equal(C.materialsSummary({ assignments: 3, modules: 1, files: 2, pages: 0, quizzes: 0, announcements: 0 }), "1 module · 2 files · 0 announcements");
});

test("API client: catalog, details, a missing course is distinct from a failure, and bad shapes are rejected", async () => {
  const calls = []; let reply;
  globalThis.fetch = async (url, init) => { calls.push([url, init]); return reply; };
  reply = { ok: true, status: 200, json: async () => ({ courses: [{ course_id: 1 }] }) };
  assert.equal((await C.getCatalog()).length, 1);
  assert.equal(calls[0][0], "http://127.0.0.1:8000/api/course-catalog");
  assert.equal(calls[0][1].cache, "no-store");
  reply = { ok: true, status: 200, json: async () => ({ course_id: 7, modules: [], announcements: [] }) };
  assert.equal((await C.getCourseDetails(7)).course_id, 7);
  assert.equal(calls[1][0], "http://127.0.0.1:8000/api/courses/7/details");
  reply = { ok: false, status: 404, json: async () => ({}) };
  await assert.rejects(C.getCourseDetails(7), (error) => error instanceof C.NotFoundError);
  reply = { ok: false, status: 503, json: async () => ({}) };
  await assert.rejects(C.getCourseDetails(7), (error) => !(error instanceof C.NotFoundError) && /HTTP 503/.test(error.message));
  reply = { ok: true, status: 200, json: async () => ({ nope: 1 }) };
  await assert.rejects(C.getCatalog(), /Invalid course catalog/);
  await assert.rejects(C.getCourseDetails(7), /Invalid course details/);
});
