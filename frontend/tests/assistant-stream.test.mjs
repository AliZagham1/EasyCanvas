import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const turn = "turn-1";
const result = { course_id: 42, status: "answered", answer: "A grounded answer", answer_blocks: [{ text: "A grounded answer", source_ids: ["S1"] }],
  sources: [{ id: "S1", origins: [{ document_key: "file:1", title: "Lecture", locations: [{slide_number:3}] }] }], warnings: [], retrieval_status: "ready" };
const event = (kind,data)=>`event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`;
const start = event("session",{conversation_id:"session-1",turn_id:turn,course_id:42});
function load(body) {
  const bytes = new TextEncoder().encode(body);
  const response = {ok:true,headers:new Headers({"content-type":"text/event-stream"}),body:new ReadableStream({start(controller){
    for(let i=0;i<bytes.length;i+=7)controller.enqueue(bytes.slice(i,i+7));controller.close();
  }})};
  const context={exports:{},fetch:async()=>response,URL,TextDecoder};
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(new URL("../lib/assistant.ts",import.meta.url),"utf8"),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2017}}).outputText,context);
  return context.exports;
}

test("decodes fragmented UTF-8 SSE and forwards progressive snapshots before final",async()=>{
  const seen=[];
  const api=load(start+event("searching",{})+event("answer_delta",{blocks:["Résumé"]})+event("final",result));
  await api.streamAssistant(42,"Question",turn,null,e=>seen.push(e),new AbortController().signal);
  assert.deepEqual(Array.from(seen,e=>e.event),["session","searching","answer_delta","final"]);
  assert.equal(seen[2].data.blocks[0],"Résumé");
});

test("rejects incomplete, unknown, malformed, wrong-course and post-final streams safely",async()=>{
  for(const body of [start,start+"event: answer_delta\ndata: secret broken JSON\n\n",start+event("unknown",{}),start+event("final",{...result,course_id:43}),start+event("final",result)+event("answer_delta",{blocks:["late"]})]){
    await assert.rejects(load(body).streamAssistant(42,"Question",turn,null,()=>{},new AbortController().signal),/interrupted|validated/);
  }
});

test("source labels and locations omit internal chunk/block metadata",()=>{
  const api=load("");
  assert.equal(api.sourceLabel({source_role:"course_syllabus",title:"internal.docx"},"CSE 3311"),"CSE 3311 Syllabus");
  assert.equal(api.sourceLabel({title:"hw.txt",references:[{source_type:"assignment",title:"Static Analysis Homework"}]},"CSE 3311"),"Static Analysis Homework");
  assert.equal(api.studentSourceLocation([{block_number:145,location:"body",start_character:0,end_character:100}]),"");
  assert.equal(api.studentSourceLocation([{page_number:4},{page_number:4},{slide_number:12}]),"Page 4 · Slide 12");
});
