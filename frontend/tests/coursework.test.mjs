import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));

// Exercise the actual TypeScript helper without adding a test-runner dependency.
function loadCoursework(fetch) {
  const source = fs.readFileSync(path.join(testDirectory, "../lib/coursework.ts"), "utf8");
  const errors = [];
  const context = { exports: {}, fetch, console: { error: (...args) => errors.push(args) } };
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017 },
  }).outputText, context);
  return { ...context.exports, errors };
}

const ok = (data) => ({ ok: true, json: async () => data });
const courses = [
  { id: 12, course_code: "BIO 2401", name: "Biology" },
  { id: 29, course_code: "MATH 1301", name: "Mathematics" },
];

test("starts all requests before any finishes and retains sorted successful results", async () => {
  const pending = [];
  const urls = [];
  const api = loadCoursework((url) => {
    urls.push(url);
    return new Promise((resolve) => pending.push(resolve));
  });
  const resultPromise = api.getUpcomingAssignments(courses);
  assert.deepEqual(urls, [
    "http://127.0.0.1:8000/api/courses/12/upcoming",
    "http://127.0.0.1:8000/api/courses/29/upcoming",
  ]);
  pending[0](ok([{ id: 3, due_at: "2026-12-20T10:00:00Z" }]));
  pending[1](ok([{ id: 4, due_at: "2026-12-10T10:00:00Z" }]));
  const result = await resultPromise;
  assert.deepEqual(Array.from(result.assignments, (item) => item.id), [4, 3]);
  assert.equal(result.assignments[0].course_id, 29);
  assert.equal(result.assignments[0].course_code, "MATH 1301");
  assert.equal(result.failedCourseIds.length, 0);
});

test("one HTTP failure does not discard another course's assignments", async () => {
  const api = loadCoursework(async (url) => url.includes("/12/")
    ? ok([{ id: 3, due_at: "2026-12-20T10:00:00Z" }])
    : { ok: false, status: 403 });
  const result = await api.getUpcomingAssignments(courses);
  assert.equal(result.assignments.length, 1);
  assert.equal(result.assignments[0].course_id, 12);
  assert.deepEqual(Array.from(result.failedCourseIds), [29]);
  assert.equal(api.errors.length, 1);
  assert.match(api.errors[0][0], /29/);
});

test("distinguishes complete emptiness, partial emptiness, and total failure", async () => {
  const complete = await loadCoursework(async () => ok([])).getUpcomingAssignments(courses);
  assert.equal(complete.assignments.length, 0);
  assert.equal(complete.failedCourseIds.length, 0);
  const partial = await loadCoursework(async (url) => {
    if (url.includes("/29/")) throw new Error("Offline");
    return ok([]);
  }).getUpcomingAssignments(courses);
  assert.deepEqual(Array.from(partial.failedCourseIds), [29]);
  const failed = await loadCoursework(async () => { throw new Error("Offline"); })
    .getUpcomingAssignments(courses);
  assert.equal(failed.assignments.length, 0);
  assert.deepEqual(Array.from(failed.failedCourseIds), [12, 29]);
});

test("empty course list makes no assignment requests", async () => {
  let calls = 0;
  const result = await loadCoursework(() => { calls += 1; }).getUpcomingAssignments([]);
  assert.equal(calls, 0);
  assert.equal(result.assignments.length, 0);
  assert.equal(result.failedCourseIds.length, 0);
});

test("malformed assignment responses are isolated to their course", async () => {
  const api = loadCoursework(async (url) => url.includes("/12/")
    ? ok({ error: "unexpected response" })
    : ok([{ id: 4, due_at: "2026-12-10T10:00:00Z" }]));
  const result = await api.getUpcomingAssignments(courses);
  assert.equal(result.assignments.length, 1);
  assert.deepEqual(Array.from(result.failedCourseIds), [12]);
});

test("course request checks HTTP errors and invalid data", async () => {
  await assert.rejects(loadCoursework(async () => ({ ok: false, status: 401 })).getCourses(), /HTTP 401/);
  await assert.rejects(loadCoursework(async () => ok({})).getCourses(), /Invalid course list/);
  assert.equal((await loadCoursework(async () => ok(courses)).getCourses()).length, 2);
});

test("passes cancellation signal to requests without logging intentional cancellation", async () => {
  const controller = new AbortController();
  const api = loadCoursework(async (_url, options) => {
    assert.equal(options.signal, controller.signal);
    return ok([]);
  });
  await api.getCourses(controller.signal);
  await api.getUpcomingAssignments(courses, controller.signal);
  controller.abort();
  const aborted = loadCoursework(async () => { throw new Error("Aborted"); });
  await aborted.getUpcomingAssignments(courses, controller.signal);
  assert.equal(aborted.errors.length, 0);
});

test("deadlines include time and handle unavailable dates without inventing values", () => {
  const { formatDueDate } = loadCoursework();
  const dueAt = "2026-12-10T17:30:00Z";
  const formatted = formatDueDate(dueAt);
  assert.match(formatted, /2026/);
  assert.match(formatted, /\d+:30/);
  assert.equal(formatDueDate(null), "Due date unavailable");
  assert.equal(formatDueDate("invalid"), "Due date unavailable");
});

test("course accents do not change when courses reorder or new courses are added", () => {
  const { getCourseAccent } = loadCoursework();
  const original = new Map(courses.map((course) => [course.id, getCourseAccent(course.id)]));
  for (const course of [{ id: 123 }, ...courses.toReversed()]) {
    if (original.has(course.id)) assert.equal(getCourseAccent(course.id), original.get(course.id));
  }
});
