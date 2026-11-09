import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

function load(fetch) {
  const context = { exports: {}, fetch, URL };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL("../lib/assistant.ts", import.meta.url), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2017 },
  }).outputText, context);
  return context.exports;
}
const response = { course_id: 42, status: "answered", answer: "Answer [S1]", answer_blocks: [{ text: "Answer", source_ids: ["S1"] }],
  sources: [{ id: "S1", origins: [{ document_key: "file:1", title: "Lecture", source_type: "file", url: "https://canvas.example/file/1", locations: [{ slide_number: 6 }] }] }],
  warnings: ["images_not_extracted"], retrieval_status: "ready" };

test("history requests stay on FastAPI, preserve citations and disable stale caching", async () => {
  const saved = {conversation_id:"thread",course_id:42,title:"Homework",messages:[{turn_id:"turn",role:"assistant",content:"Answer",response}]};
  const api=load(async(url,options)=>{
    assert.equal(url,"http://127.0.0.1:8000/api/courses/42/conversations/thread");
    assert.equal(options.method,"GET");assert.equal(options.cache,"no-store");
    return {ok:true,json:async()=>saved};
  });
  assert.deepEqual((await api.conversationHistory(42,"thread")).messages[0].response.sources,response.sources);
});

test("new conversation uses POST and rejects history from another course",async()=>{
  const api=load(async(url,options)=>{
    assert.equal(options.method,"POST");
    return {ok:true,json:async()=>({conversation_id:"other",course_id:43,title:"Other",messages:[]})};
  });
  await assert.rejects(()=>api.conversationHistory(42,undefined,true),/unavailable/);
});

test("history errors do not expose backend response details",async()=>{
  const api=load(async()=>({ok:false,json:async()=>({detail:"private provider failure"})}));
  await assert.rejects(()=>api.conversationHistory(42),/Unable to load conversation history/);
});

test("rename sends only the trimmed title and keeps the same conversation identity",async()=>{
  const api=load(async(url,options)=>{
    assert.equal(url,"http://127.0.0.1:8000/api/courses/42/conversations/thread");
    assert.equal(options.method,"PATCH");assert.equal(options.body,JSON.stringify({title:"Review"}));
    return {ok:true,json:async()=>({conversation_id:"thread",course_id:42,title:"Review"})};
  });
  assert.equal((await api.manageConversation(42,"thread"," Review ")).title,"Review");
});

test("delete targets only the selected thread and checks the returned identity",async()=>{
  const api=load(async(url,options)=>{
    assert.equal(options.method,"DELETE");assert.equal(options.body,undefined);
    return {ok:true,json:async()=>({conversation_id:"other",course_id:43,deleted:true})};
  });
  await assert.rejects(()=>api.manageConversation(42,"thread"),/verify/);
});

test("management errors do not expose provider or storage internals",async()=>{
  const api=load(async()=>({ok:false,status:503,json:async()=>({detail:"private details"})}));
  await assert.rejects(()=>api.manageConversation(42,"thread"),/Unable to update/);
});

test("POSTs only the question to the selected FastAPI course and preserves the response", async () => {
  const controller = new AbortController();
  const api = load(async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8000/api/courses/42/assistant");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.equal(options.body, JSON.stringify({ question: "Explain FCFS" }));
    assert.equal(options.signal, controller.signal);
    return { ok: true, json: async () => response };
  });
  assert.equal(await api.askAssistant(42, "Explain FCFS", controller.signal), response);
});

test("accepts all backend outcomes with clear status messages", async () => {
  for (const status of ["answered", "insufficient_context", "needs_structured_data", "structured_data_needed", "indexing_needed", "refused", "clarification_needed"]) {
    const api = load(async () => ({ ok: true, json: async () => ({ ...response, status }) }));
    assert.equal((await api.askAssistant(42, "Question")).status, status);
    if (status !== "answered") assert.ok(api.assistantStatusMessage(status));
  }
});

test("hides raw backend errors and rejects unexpected/course-mismatched responses", async () => {
  await assert.rejects(load(async () => ({ ok: false })).askAssistant(42, "Question"), /assistant is unavailable/);
  for (const patch of [{ course_id: 43 }, { status: "unexpected" }, { sources: null }, { warnings: null }]) {
    await assert.rejects(load(async () => ({ ok: true, json: async () => ({ ...response, ...patch }) })).askAssistant(42, "Question"), /unavailable response/);
  }
});

test("source links permit only safe HTTP(S) URLs", () => {
  const { safeSourceUrl } = load();
  for (const url of ["javascript:alert(1)", "data:text/html,bad", "file:///etc/passwd", "/relative", "https://user:password@example.com", "invalid", null]) assert.equal(safeSourceUrl(url), undefined);
  assert.equal(safeSourceUrl("https://canvas.example/files/1"), "https://canvas.example/files/1");
});

test("formats page, slide, Word, notebook and text locations without inventing metadata", () => {
  const { formatSourceLocation } = load();
  assert.equal(formatSourceLocation({ page_number: 2 }), "Page 2");
  assert.equal(formatSourceLocation({ slide_number: 6 }), "Slide 6");
  assert.equal(formatSourceLocation({ block_number: 3, location: "body" }), "Block 3 · body");
  assert.equal(formatSourceLocation({ cell_number: 4 }), "Cell 4");
  assert.equal(formatSourceLocation({ start_character: 0, end_character: 100 }), "Characters 0–100");
  assert.equal(formatSourceLocation({}), "");
});
