import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

const sid = "00000000-0000-4000-8000-000000000042";
type StoredThread = {conversation_id:string;course_id:number;title:string;messages:Record<string,unknown>[]};
let histories: StoredThread[] = [];
let nextThread=42;
const source = { id: "S1", origins: [{ document_key: "file:1", title: "syllabus.docx", source_role: "course_syllabus", source_type: "file",
  url: "https://canvas.example/files/1", locations: [{ block_number: 145, location: "body", start_character: 0, end_character: 100 }] }] };
function final(course = 42) {
  return { course_id: course, status: "answered", answer: "Four warnings", answer_blocks: [{ text: "## Requirements\n\nSelect **four warnings**.\n\n- Use two categories\n\n1. Run an analyzer\n\n```python\nprint('example')\n```", source_ids: ["S1"] }],
    sources: [source], warnings: [], retrieval_status: "ready" };
}
function frames(payload: {turn_id: string; question?:string; conversation_id?:string}, finish: unknown = final()) {
  const thread = histories.find(c=>c.conversation_id===(payload.conversation_id || sid));
  if(thread && payload.question && !thread.messages.some(m=>m.turn_id===payload.turn_id)) {
    thread.title=payload.question;
    thread.messages.push({message_id:payload.turn_id+"u",turn_id:payload.turn_id,role:"user",content:payload.question},
      {message_id:payload.turn_id+"a",turn_id:payload.turn_id,role:"assistant",content:"Answer",response:finish});
  }
  return `event: session\ndata: ${JSON.stringify({conversation_id:payload.conversation_id || sid,turn_id:payload.turn_id,course_id:thread?.course_id || 42})}\n\nevent: searching\ndata: {}\n\nevent: generating\ndata: {}\n\nevent: answer_delta\ndata: {"blocks":["Select four"]}\n\nevent: final\ndata: ${JSON.stringify(finish)}\n\n`;
}
async function setup(page: Page) {
  histories=[];
  nextThread=42;
  await page.route("**/api/courses/*/conversations**",route=>{
    const path=new URL(route.request().url()).pathname.split("/");const course=Number(path[3]);const id=path[5];
    if(route.request().method()==="PATCH") {
      const found=histories.find(c=>c.course_id===course && c.conversation_id===id);
      if(!found) return route.fulfill({status:404,json:{detail:"Not found"}});
      found.title=route.request().postDataJSON().title.trim();
      return route.fulfill({json:{conversation_id:found.conversation_id,course_id:course,title:found.title}});
    }
    if(route.request().method()==="DELETE") {
      histories=histories.filter(c=>!(c.course_id===course && c.conversation_id===id));
      return route.fulfill({json:{conversation_id:id,course_id:course,deleted:true}});
    }
    if(route.request().method()==="POST") {
      const item={conversation_id:`00000000-0000-4000-8000-${String(nextThread++).padStart(12,"0")}`,course_id:course,title:"New conversation",messages:[]};
      histories.unshift(item);return route.fulfill({status:201,json:item});
    }
    if(id) {
      const found=histories.find(c=>c.course_id===course && c.conversation_id===id);
      if(found) histories=[found,...histories.filter(c=>c!==found)];
      return route.fulfill({json:found});
    }
    return route.fulfill({json:histories.filter(c=>c.course_id===course)});
  });
  await page.route("http://127.0.0.1:8000/api/courses", r => r.fulfill({json: [{id:42,course_code:"CSE 3311",name:"Software Engineering"},{id:43,course_code:"CSE 3320",name:"Operating Systems"}]}));
  await page.goto("/study-assistant");
  await page.getByLabel("Course",{exact:true}).selectOption("42");
  await expect(page.getByRole("combobox",{name:"Conversation",exact:true})).toBeEnabled();
}
async function question(page: Page, text: string) {
  await page.getByRole("textbox").fill(text);
  await page.getByRole("button",{name:"Send",exact:true}).click();
}

test("thread, follow-up context, Markdown and source labels",async ({page})=>{
  const requests: Record<string,unknown>[]=[];
  await page.route("**/api/courses/42/assistant/stream",async route=>{
    const data=route.request().postDataJSON();requests.push(data);
    await route.fulfill({contentType:"text/event-stream",body:frames(data)});
  });
  await setup(page);await question(page,"Explain the homework.");
  await expect(page.getByRole("heading",{name:"Requirements",exact:true})).toBeVisible();
  await expect(page.locator("pre code")).toContainText("print('example')");
  await page.getByRole("link",{name:"[S1]",exact:true}).click();
  await expect(page.getByRole("link",{name:"CSE 3311 Syllabus (opens in a new tab)"})).toBeVisible();
  await expect(page.getByText("Block 145",{exact:false})).toHaveCount(0);
  await question(page,"How many warnings?");
  await expect(page.locator(".conversation-turn")).toHaveCount(2);
  await expect(page.locator(".conversation-turn").last().getByRole("heading",{name:"Requirements"})).toBeVisible();
  expect(requests[0].conversation_id).toBe(sid);expect(requests[1].conversation_id).toBe(sid);
});

test("immediate question/searching state and duplicate prevention",async ({page})=>{
  let release!:()=>void; const held=new Promise<void>(r=>{release=r;});let calls=0;
  await page.route("**/assistant/stream",async route=>{calls++;await held;await route.fulfill({contentType:"text/event-stream",body:frames(route.request().postDataJSON())});});
  await setup(page);await question(page,"Explain this assignment.");
  await expect(page.getByText("Explain this assignment.",{exact:true})).toBeVisible();
  await expect(page.getByText("Searching course material...",{exact:true}).first()).toBeVisible();
  await page.getByRole("textbox").fill("Duplicate");await page.getByRole("textbox").press("Enter");
  expect(calls).toBe(1);release();await expect(page.locator(".conversation-markdown")).toBeVisible();
});

test("course switch aborts and isolates the conversation",async ({page})=>{
  let release!:()=>void;const held=new Promise<void>(r=>{release=r;});
  await page.route("**/assistant/stream",async route=>{await held;await route.fulfill({contentType:"text/event-stream",body:frames(route.request().postDataJSON())}).catch(()=>{});});
  await setup(page);await question(page,"Static analysis homework");
  await page.getByLabel("Course",{exact:true}).selectOption("43");release();
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
  await expect(page.getByRole("heading",{name:"Ask about CSE 3320"})).toBeVisible();
});

test("course history restores messages and New conversation preserves separate threads",async ({page})=>{
  await page.route("**/assistant/stream",route=>route.fulfill({contentType:"text/event-stream",body:frames(route.request().postDataJSON())}));
  await setup(page);await question(page,"Explain the first homework.");
  await expect(page.locator(".conversation-markdown")).toBeVisible();
  await page.getByLabel("Course",{exact:true}).selectOption("43");
  await expect(page.getByRole("combobox",{name:"Conversation",exact:true})).toBeEnabled();
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
  await page.getByLabel("Course",{exact:true}).selectOption("42");
  await expect(page.locator(".conversation-turn")).toHaveCount(1);
  await expect(page.locator(".conversation-user")).toContainText("Explain the first homework.");
  await page.getByRole("button",{name:"New conversation",exact:true}).click();
  await expect(page.getByRole("combobox",{name:"Conversation",exact:true})).toBeEnabled();
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
  await page.getByRole("combobox",{name:"Conversation",exact:true}).selectOption(sid);
  await expect(page.locator(".conversation-turn")).toHaveCount(1);
  await expect(page.locator(".conversation-user")).toContainText("Explain the first homework.");
  await page.reload();await page.getByLabel("Course",{exact:true}).selectOption("42");
  await expect(page.locator(".conversation-turn")).toHaveCount(1);
  await page.screenshot({path:"test-results/conversation-history-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole("combobox",{name:"Conversation",exact:true})).toBeVisible();
  await expect(page.getByRole("textbox")).toBeVisible();
  await page.screenshot({path:"test-results/conversation-history-mobile.png",fullPage:true});
});

test("interrupted/malformed stream discards draft and permits retry",async ({page})=>{
  let calls=0;
  await page.route("**/assistant/stream",async route=>{calls++;const data=route.request().postDataJSON();
    await route.fulfill({contentType:"text/event-stream",body:calls===1 ? frames(data).split("event: final")[0] : frames(data)});
  });
  await setup(page);await question(page,"Question");
  await expect(page.getByRole("alert").first()).toContainText("interrupted");
  await expect(page.locator(".conversation-markdown")).toHaveCount(0);
  await page.getByRole("button",{name:"Retry response"}).click();
  await expect(page.locator(".conversation-markdown")).toBeVisible();expect(calls).toBe(2);
});

test("safe provider error and Enter/Shift+Enter behavior",async ({page})=>{
  await page.route("**/assistant/stream",route=>route.fulfill({status:503,json:{detail:"secret upstream internals"}}));
  await setup(page);const input=page.getByRole("textbox");await input.fill("First line");await input.press("Shift+Enter");
  await input.press("B");await expect(input).toHaveValue("First line\nB");await input.press("Enter");
  await expect(page.getByRole("alert").first()).toContainText("unavailable");
  await expect(page.getByText("secret upstream internals",{exact:false})).toHaveCount(0);
});

test("insufficient, structured-data and indexing outcomes",async ({page})=>{
  let status="insufficient_context";
  await page.route("**/assistant/stream",route=>route.fulfill({contentType:"text/event-stream",body:frames(route.request().postDataJSON(),{...final(),status,answer_blocks:[],sources:[]})}));
  await setup(page);
  for(const value of ["insufficient_context","needs_structured_data","indexing_needed"]){
    status=value;await question(page,"Question "+value);
    const turn=page.locator(".conversation-turn").last();
    await expect(turn.locator(".conversation-assistant > p").first()).toBeVisible();
    await expect(turn.locator(".conversation-sources")).toHaveCount(0);
  }
});

test("routing clarification and refresh messages remain specific",async ({page})=>{
  let status = "clarification_needed";
  let answer = "Which Iteration 3 assignment do you mean: Presentation or Review?";
  await page.route("**/assistant/stream",route=>route.fulfill({contentType:"text/event-stream",body:frames(route.request().postDataJSON(),
    {...final(),status,answer,answer_blocks:[],sources:[]})}));
  await setup(page);await question(page,"Explain the Iteration 3 rubric.");
  await expect(page.locator(".conversation-turn").last()).toContainText(answer);
  status = "indexing_needed";answer = "This course's saved material is being refreshed. Please retry shortly.";
  await question(page,"Explain Practice Questions 10.");
  await expect(page.locator(".conversation-turn").last()).toContainText(answer);
});

test("Stop response discards the draft and supports retry",async ({page})=>{
  let release!:()=>void;const held=new Promise<void>(r=>{release=r;});let calls=0;
  await page.route("**/assistant/stream",async route=>{
    calls++;if(calls===1)await held;
    await route.fulfill({contentType:"text/event-stream",body:frames(route.request().postDataJSON())}).catch(()=>{});
  });
  await setup(page);await question(page,"Explain the assignment.");
  await page.getByRole("button",{name:"Stop response"}).click();
  await expect(page.locator(".conversation-error")).toContainText("Response stopped");
  await expect(page.locator(".conversation-sources")).toHaveCount(0);
  release();await page.getByRole("button",{name:"Retry response"}).click();
  await expect(page.locator(".conversation-markdown")).toBeVisible();
  expect(calls).toBe(2);
});

test("rename persists through course switching and reload without clearing messages",async({page})=>{
  await page.route("**/assistant/stream",r=>r.fulfill({contentType:"text/event-stream",body:frames(r.request().postDataJSON())}));
  await setup(page);await question(page,"Explain the homework.");await expect(page.locator(".conversation-markdown")).toBeVisible();
  await page.getByRole("button",{name:"Rename",exact:true}).click();
  await page.getByLabel("Conversation name",{exact:true}).fill("Static analysis review");
  await page.getByRole("dialog",{name:"Rename conversation",exact:true}).getByRole("button",{name:"Save",exact:true}).click();
  const history=page.getByRole("combobox",{name:"Conversation",exact:true});
  await expect(history.locator("option:checked")).toHaveText("Static analysis review");await expect(history).toHaveValue(sid);
  await expect(page.locator(".conversation-turn")).toHaveCount(1);
  await page.getByLabel("Course",{exact:true}).selectOption("43");await expect(history).toBeEnabled();
  await page.getByLabel("Course",{exact:true}).selectOption("42");await expect(history.locator("option:checked")).toHaveText("Static analysis review");
  await expect(page.locator(".conversation-turn")).toHaveCount(1);
  await page.reload();await page.getByLabel("Course",{exact:true}).selectOption("42");
  await expect(history.locator("option:checked")).toHaveText("Static analysis review");await expect(page.locator(".conversation-turn")).toHaveCount(1);
  await page.screenshot({path:"test-results/conversation-management-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});await expect(page.getByRole("button",{name:"Rename",exact:true})).toBeVisible();
  await page.screenshot({path:"test-results/conversation-management-mobile.png",fullPage:true});
});

test("confirmed deletion restores another thread then creates a clean replacement",async({page})=>{
  await page.route("**/assistant/stream",r=>r.fulfill({contentType:"text/event-stream",body:frames(r.request().postDataJSON())}));
  await setup(page);await question(page,"First assignment");await expect(page.locator(".conversation-markdown")).toBeVisible();
  await page.getByRole("button",{name:"New conversation",exact:true}).click();
  const history=page.getByRole("combobox",{name:"Conversation",exact:true});await expect(history).toBeEnabled();
  const second=await history.inputValue();await question(page,"Second assignment");await expect(page.locator(".conversation-markdown")).toBeVisible();
  await page.getByRole("button",{name:"Delete conversation",exact:true}).click();
  await page.getByRole("dialog",{name:"Delete conversation?",exact:true}).getByRole("button",{name:"Cancel",exact:true}).click();
  await expect(history).toHaveValue(second);await expect(page.locator(".conversation-user")).toContainText("Second assignment");
  await page.getByRole("button",{name:"Delete conversation",exact:true}).click();
  await page.getByRole("dialog",{name:"Delete conversation?",exact:true}).getByRole("button",{name:"Delete",exact:true}).click();
  await expect(history).toHaveValue(sid);await expect(page.locator(".conversation-user")).toContainText("First assignment");
  await expect(history.locator(`option[value="${second}"]`)).toHaveCount(0);
  await page.getByRole("button",{name:"Delete conversation",exact:true}).click();
  await page.getByRole("dialog",{name:"Delete conversation?",exact:true}).getByRole("button",{name:"Delete",exact:true}).click();
  await expect(history).toBeEnabled();await expect(history).not.toHaveValue(sid);await expect(history).not.toHaveValue(second);
  await expect(page.locator(".conversation-turn")).toHaveCount(0);
});

test("rename cancellation and safe failure preserve the selected chat",async({page})=>{
  await setup(page);const history=page.getByRole("combobox",{name:"Conversation",exact:true});
  await page.getByRole("button",{name:"Rename",exact:true}).click();await page.getByLabel("Conversation name",{exact:true}).fill(" ");
  await expect(page.getByRole("dialog").getByRole("button",{name:"Save",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Cancel",exact:true}).click();await expect(history).toHaveValue(sid);
  await page.route("**/conversations/*",route=>route.request().method()==="PATCH" ? route.fulfill({status:503,json:{detail:"private details"}}) : route.fallback());
  await page.getByRole("button",{name:"Rename",exact:true}).click();await page.getByLabel("Conversation name",{exact:true}).fill("Review");
  await page.getByRole("dialog").getByRole("button",{name:"Save",exact:true}).click();await expect(page.getByRole("dialog").getByRole("alert")).toContainText("Unable to update");
  await expect(page.getByText("private details",{exact:false})).toHaveCount(0);await expect(history).toHaveValue(sid);
});

test("in-app dialogs cancel without requests, trap focus and restore the trigger",async({page})=>{
  const nativeDialogs:string[]=[];
  page.on("dialog",async dialog=>{nativeDialogs.push(dialog.type());await dialog.dismiss();});
  await setup(page);
  const mutations:string[]=[];
  page.on("request",request=>{if(["PATCH","DELETE"].includes(request.method()))mutations.push(request.method());});
  const rename=page.getByRole("button",{name:"Rename",exact:true});
  await rename.click();
  const modal=page.getByRole("dialog",{name:"Rename conversation",exact:true});
  const input=modal.getByLabel("Conversation name",{exact:true});
  await expect(input).toHaveValue("New conversation");await expect(input).toBeFocused();
  await input.fill("Unsaved name");await page.keyboard.press("Escape");
  await expect(modal).not.toBeVisible();await expect(rename).toBeFocused();
  await rename.click();await expect(input).toHaveValue("New conversation");
  await modal.getByRole("button",{name:"Cancel",exact:true}).click();
  const remove=page.getByRole("button",{name:"Delete conversation",exact:true});await remove.click();
  const confirmation=page.getByRole("dialog",{name:"Delete conversation?",exact:true});
  await expect(confirmation).toContainText('"New conversation" will be permanently deleted. Your course materials will not be affected.');
  await expect(confirmation.getByRole("button",{name:"Cancel",exact:true})).toBeFocused();
  await page.keyboard.press("Shift+Tab");await expect(confirmation.getByRole("button",{name:"Delete",exact:true})).toBeFocused();
  await page.keyboard.press("Tab");await expect(confirmation.getByRole("button",{name:"Cancel",exact:true})).toBeFocused();
  await page.keyboard.press("Escape");await expect(confirmation).not.toBeVisible();await expect(remove).toBeFocused();
  expect(mutations).toEqual([]);expect(nativeDialogs).toEqual([]);
});

test("rename keeps title validation and uses the existing request identity",async({page})=>{
  await setup(page);
  let saved:Record<string,unknown>|undefined;
  page.on("request",request=>{if(request.method()==="PATCH")saved={url:request.url(),body:request.postDataJSON()};});
  await page.getByRole("button",{name:"Rename",exact:true}).click();const modal=page.getByRole("dialog");
  const input=modal.getByLabel("Conversation name",{exact:true});const save=modal.getByRole("button",{name:"Save",exact:true});
  await input.fill(" ");await expect(save).toBeDisabled();expect(saved).toBeUndefined();
  await expect(input).toHaveAttribute("maxlength","100");
  await input.fill("x".repeat(110));expect((await input.inputValue()).length).toBe(100);
  await input.fill("  Review — α²  ");await save.click();await expect(modal).not.toBeVisible();
  expect(saved).toEqual({url:`http://127.0.0.1:8000/api/courses/42/conversations/${sid}`,body:{title:"Review — α²"}});
  await expect(page.getByRole("combobox",{name:"Conversation",exact:true})).toHaveValue(sid);
});

test("delete failure stays in the modal and can be retried safely",async({page})=>{
  await setup(page);let calls=0;
  await page.route("**/conversations/*",route=>{
    if(route.request().method()!=="DELETE")return route.fallback();
    calls++;return calls===1 ? route.fulfill({status:503,json:{detail:"private failure details"}}):route.fallback();
  });
  await page.getByRole("button",{name:"Delete conversation",exact:true}).click();const modal=page.getByRole("dialog");
  await modal.getByRole("button",{name:"Delete",exact:true}).click();
  await expect(modal.getByRole("alert")).toContainText("Unable to update");await expect(modal).toBeVisible();
  await expect(page.getByText("private failure details",{exact:false})).toHaveCount(0);
  expect(histories[0].conversation_id).toBe(sid);
  await modal.getByRole("button",{name:"Delete",exact:true}).click();await expect(modal).not.toBeVisible();
  await expect(page.getByRole("combobox",{name:"Conversation",exact:true})).not.toHaveValue(sid);expect(calls).toBe(2);
});

for(const action of ["rename","delete"] as const) {
  test(`${action} pending state prevents duplicate requests and premature dismissal`,async({page})=>{
    await setup(page);let release!:()=>void;const held=new Promise<void>(r=>{release=r;});let calls=0;
    await page.route("**/conversations/*",async route=>{
      if(route.request().method()!==(action==="rename"?"PATCH":"DELETE"))return route.fallback();
      calls++;await held;return route.fallback();
    });
    await page.getByRole("button",{name:action==="rename"?"Rename":"Delete conversation",exact:true}).click();
    const modal=page.getByRole("dialog");if(action==="rename")await modal.getByLabel("Conversation name").fill("Review");
    const submit=modal.getByRole("button",{name:action==="rename"?"Save":"Delete",exact:true});await submit.click();
    await expect(submit).toBeDisabled();await expect(modal.getByRole("button",{name:"Cancel",exact:true})).toBeDisabled();
    await expect(modal.getByRole("status")).toContainText(action==="rename"?"Saving":"Deleting");
    await page.keyboard.press("Escape");await page.keyboard.press("Enter");await expect(modal).toBeVisible();expect(calls).toBe(1);
    release();await expect(modal).not.toBeVisible();
    await expect(page.getByRole("combobox",{name:"Conversation",exact:true})).toBeEnabled();
  });
}

test("actions stay separated and both modals fit desktop and mobile with long titles",async({page})=>{
  await setup(page);
  for(const width of [1280,390]) {
    await page.setViewportSize({width,height:844});
    const rename=page.getByRole("button",{name:"Rename",exact:true});const remove=page.getByRole("button",{name:"Delete conversation",exact:true});
    const a=await rename.boundingBox();const b=await remove.boundingBox();expect(a).not.toBeNull();expect(b).not.toBeNull();
    expect(b!.y>=a!.y+a!.height+4 || b!.x>=a!.x+a!.width+4).toBe(true);
    await rename.click();let modal=page.getByRole("dialog");await modal.getByLabel("Conversation name").fill("Long conversation title — "+"Coursework review ".repeat(4));
    const bounds=await modal.boundingBox();expect(bounds!.x).toBeGreaterThanOrEqual(16);expect(bounds!.x+bounds!.width).toBeLessThanOrEqual(width-16);
    expect(Math.abs(bounds!.y+bounds!.height/2-422)).toBeLessThan(2);
    await page.screenshot({path:`test-results/conversation-rename-${width}.png`,fullPage:true});
    await modal.getByRole("button",{name:"Save",exact:true}).click();await expect(modal).not.toBeVisible();
    await remove.click();modal=page.getByRole("dialog");
    const deleteBounds=await modal.boundingBox();expect(deleteBounds!.x).toBeGreaterThanOrEqual(16);expect(deleteBounds!.x+deleteBounds!.width).toBeLessThanOrEqual(width-16);
    expect(await modal.evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
    await page.screenshot({path:`test-results/conversation-delete-${width}.png`,fullPage:true});
    await modal.getByRole("button",{name:"Cancel",exact:true}).click();
  }
});
