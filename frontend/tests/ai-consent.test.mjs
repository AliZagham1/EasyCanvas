import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function load(file, context = {}) {
  const ctx = { exports: {}, URL, TextDecoder, ...context };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL(`../lib/${file}`, import.meta.url), "utf8"),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017 } }).outputText, ctx);
  return ctx.exports;
}
const view = (over = {}) => ({ status: "undecided", enabled: false, decided_at: null, version: null, current_version: 1, needs_decision: true, ...over });

test("only an enabled, valid answer opens AI; loading, errors and missing data never do", () => {
  const { consentGate } = load("ai-consent.ts");
  assert.equal(consentGate(null, true, false), "loading");
  assert.equal(consentGate(null, false, true), "unavailable");
  assert.equal(consentGate(null, false, false), "unavailable");
  assert.equal(consentGate(view(), false, false), "disabled");
  assert.equal(consentGate(view({ status: "declined", needs_decision: false }), false, false), "disabled");
  assert.equal(consentGate(view({ status: "granted", enabled: true, needs_decision: false }), false, false), "enabled");
});

test("malformed consent responses are rejected so the UI keeps AI off", () => {
  const { parseConsent } = load("ai-consent.ts");
  for (const bad of [null, {}, { enabled: "yes" }, view({ status: "maybe" }), view({ needs_decision: undefined })]) assert.throws(() => parseConsent(bad));
  assert.equal(parseConsent(view()).enabled, false);
});

test("a first-time student needs a decision; a declined or withdrawn one does not", () => {
  const { consentStatusLabel } = load("ai-consent.ts");
  assert.match(consentStatusLabel(view()), /no choice made yet/);
  assert.match(consentStatusLabel(view({ status: "declined", needs_decision: false })), /off/);
  assert.match(consentStatusLabel(view({ status: "withdrawn", needs_decision: false })), /withdrawn/);
  assert.equal(consentStatusLabel(view({ status: "granted", enabled: true, needs_decision: false })), "AI features are on");
});

test("saving a decision sends the version and surfaces a stale-version conflict", async () => {
  const calls = [];
  const ok = load("ai-consent.ts", { fetch: async (url, init) => { calls.push([url, init]); return { ok: true, status: 200, json: async () => view({ status: "granted", enabled: true, needs_decision: false }) }; } });
  assert.equal((await ok.saveConsent("granted", 1)).enabled, true);
  assert.equal(calls[0][1].method, "PUT");
  assert.deepEqual(JSON.parse(calls[0][1].body), { decision: "granted", version: 1 });
  const stale = load("ai-consent.ts", { fetch: async () => ({ ok: false, status: 409, json: async () => ({}) }) });
  await assert.rejects(stale.saveConsent("granted", 0), /changed/);
});

test("withdrawal text says what it does and does not do", () => {
  const { WITHDRAWAL_NOTE } = load("ai-consent.ts");
  assert.match(WITHDRAWAL_NOTE, /cannot recall data that was already sent/);
  assert.match(WITHDRAWAL_NOTE, /does not delete/);
});

test("a 403 consent refusal becomes a distinct error, not a generic assistant failure", async () => {
  const refusal = { ok: false, status: 403, headers: new Headers(), clone() { return this; }, json: async () => ({ code: "ai_consent_required" }) };
  const api = load("assistant.ts", { fetch: async () => refusal });
  await assert.rejects(api.askAssistant(42, "q"), (e) => e.name === "ConsentRequiredError");
  await assert.rejects(api.streamAssistant(42, "q", "t", null, () => {}, new AbortController().signal), (e) => e.name === "ConsentRequiredError");
  const other = load("assistant.ts", { fetch: async () => ({ ok: false, status: 503, headers: new Headers(), clone() { return this; }, json: async () => ({}) }) });
  await assert.rejects(other.askAssistant(42, "q"), (e) => e.name !== "ConsentRequiredError");
});

test("an in-stream consent error surfaces as ConsentRequiredError", async () => {
  const body = `event: session\ndata: ${JSON.stringify({ conversation_id: "s", turn_id: "t", course_id: 42 })}\n\nevent: error\ndata: ${JSON.stringify({ message: "x", code: "ai_consent_required" })}\n\n`;
  const bytes = new TextEncoder().encode(body);
  const response = { ok: true, status: 200, headers: new Headers({ "content-type": "text/event-stream" }), clone() { return this; }, json: async () => ({}),
    body: new ReadableStream({ start(c) { c.enqueue(bytes); c.close(); } }) };
  const api = load("assistant.ts", { fetch: async () => response });
  await assert.rejects(api.streamAssistant(42, "q", "t", null, () => {}, new AbortController().signal), (e) => e.name === "ConsentRequiredError");
});
