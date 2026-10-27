import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function loadSync(fetch) {
  const source = fs.readFileSync(new URL("../lib/course-sync.ts", import.meta.url), "utf8");
  const context = { exports: {}, fetch };
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017 },
  }).outputText, context);
  return context.exports;
}

test("requests backend-owned syncing without sending course IDs or Canvas credentials", async () => {
  const calls = [];
  const api = loadSync(async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => ({ status: "queued", courses: [] }) };
  });
  const controller = new AbortController();
  await api.requestCourseSync(controller.signal);
  assert.equal(calls[0].url, "http://127.0.0.1:8000/api/ingestion/sync");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.signal, controller.signal);
  assert.equal(calls[0].options.body, undefined);
});

test("status requests bypass caching and reject failures or malformed responses", async () => {
  const api = loadSync(async (_url, options) => {
    assert.equal(options.cache, "no-store");
    return { ok: true, json: async () => ({ status: "running", courses: [] }) };
  });
  assert.equal((await api.fetchCourseSync()).status, "running");
  await assert.rejects(loadSync(async () => ({ ok: false, status: 503 })).fetchCourseSync(), /HTTP 503/);
  await assert.rejects(loadSync(async () => ({ ok: true, json: async () => ({}) })).fetchCourseSync(), /Invalid/);
  await assert.rejects(loadSync(async () => ({ ok: false, status: 503 })).requestCourseSync(), /HTTP 503/);
});

test("progress distinguishes discovering courses from a real zero-course result", () => {
  const { describeCourseSync } = loadSync();
  assert.match(describeCourseSync({ status: "running", total_courses: null, courses: [] }), /Finding/);
  assert.match(describeCourseSync({ status: "completed", total_courses: 0, courses: [] }), /no current courses/);
  assert.match(describeCourseSync({ status: "running", total_courses: 2, current_course_id: 9, courses: [{ course_id: 8 }, { course_id: 9, course_code: "Test course" }] }), /course 2 of 2/);
});

test("finished partial coverage never promises complete course knowledge", () => {
  const { describeCourseSync, isCourseSyncActive } = loadSync();
  const partial = { status: "completed", total_courses: 3, completed_courses: 2, failed_courses: 1, coverage: "partial", courses: [] };
  assert.match(describeCourseSync(partial), /2 of 3/);
  assert.match(describeCourseSync(partial), /could not be read/);
  assert.equal(isCourseSyncActive(partial), false);
  assert.equal(isCourseSyncActive({ status: "queued" }), true);
  assert.match(describeCourseSync({ status: "error", courses: [] }), /try again/);
});
