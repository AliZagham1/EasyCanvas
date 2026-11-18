import { test, expect } from '@playwright/test';
import { mockConsent } from './consent';
import type { Page } from '@playwright/test';

test.beforeEach(async ({ page }) => { await mockConsent(page); });

function course(status = 'pending', remaining = 4) {
  return {course_id:42,title:'2268-CSE-3311-001',eligible_chunks:1203,indexed_chunks:1203-remaining,
    pending_chunks:remaining,failed_chunks:status==='failed' ? remaining : 0,remaining_chunks:remaining,status,automatic:false,last_successful_at:null,
    documents:[{document_key:'file:1',title:'Assignment instructions',eligible_chunks:4,indexed_chunks:4-remaining,
      pending_chunks:remaining,failed_chunks:0,remaining_chunks:remaining,extraction_status:'ok'}]};
}
const total = {estimated_cost_usd:3.42,calls:8,unpriced_calls:0,input_tokens:16915,output_tokens:1360};
function preferences(warning: number|null = null, automatic = true, vision = true) {
  const features={embedding:{...total,estimated_cost_usd:.0001},vision:{...total,estimated_cost_usd:0},assistant_other:{...total,estimated_cost_usd:3.4199}};
  return {settings:{auto_index:automatic,vision_enabled:vision,monthly_warning_usd:warning},usage:{today:{...total,estimated_cost_usd:.12},this_month:total,
    total:{...total,estimated_cost_usd:7.42,calls:12},...features,monthly_breakdown:features,
    warning_reached:warning!==null && 3.42>=warning,recording_started_at:'2026-11-03T12:00:00Z',notice:'Local estimate'}};
}
async function settings(page: Page, options: {keyStatus?:{configured:boolean;source:string|null;hint:string|null}; byKey?:unknown[]; warning?:number|null; automatic?:boolean; vision?:boolean; status?:string; readyOnPoll?:boolean; saveFails?:boolean} = {}) {
  let data: ReturnType<typeof preferences> & {api_key?: unknown} = preferences(options.warning??null, options.automatic??true, options.vision??true);
  let keyStatus = options.keyStatus ?? {configured:true,source:'environment',hint:'1111'};
  const keyCalls: {method:string;body:{api_key?:string}|null}[] = [];
  data.api_key = keyStatus; if(options.byKey) (data.usage as {by_key?: unknown[]}).by_key = options.byKey;
  let status = {...course(options.status),automatic:data.settings.auto_index};
  let polls=0, indexPosts=0;
  await page.route('**/api/openai-key', route => {
    const method=route.request().method(); const body=method==='PUT' ? route.request().postDataJSON() : null;
    keyCalls.push({method,body});
    if(method==='PUT') {
      if(String(body.api_key).startsWith('sk-bad')) return route.fulfill({status:422,json:{detail:'OpenAI rejected this key. Check it and try again.'}});
      keyStatus={configured:true,source:'saved',hint:String(body.api_key).slice(-4)};
    }
    if(method==='DELETE') keyStatus={configured:true,source:'environment',hint:'1111'};
    data.api_key=keyStatus;
    return route.fulfill({json:keyStatus});
  });
  await page.route('**/api/search/settings', route => {
    if(route.request().method()==='PATCH') {
      if(options.saveFails) return route.fulfill({status:503,json:{detail:'private provider detail'}});
      const changes=route.request().postDataJSON();
      data={...data,settings:{...data.settings,...changes}};
      data.usage.warning_reached=data.settings.monthly_warning_usd!==null && 3.42>=data.settings.monthly_warning_usd;
      if(changes.auto_index===true) {status={...status,status:'updating',automatic:true};polls=0;}
    }
    return route.fulfill({json:data});
  });
  await page.route('**/api/search/status', route => {
    if(status.status==='updating' && options.readyOnPoll && ++polls>1) status={...course('ready',0),automatic:true};
    return route.fulfill({json:{courses:[status]}});
  });
  await page.route('**/api/courses/42/search-index', route => {
    expect(route.request().method()).toBe('POST');indexPosts++;
    status={...status,status:'updating'};return route.fulfill({status:202,json:status});
  });
  await page.route('**/api/storage', route => route.fulfill({json:{courses:[],preview_id:'a'.repeat(64),eligible_bytes:0}}));
  await page.goto('/settings');
  return {getIndexPosts:()=>indexPosts,keyCalls};
}

test('switch, compact course counts, document details, and manual queue persist', async ({page}) => {
  await settings(page);
  const automatic=page.getByRole('switch',{name:'Keep Study Assistant search automatically updated'});
  await expect(automatic).toBeChecked();
  await automatic.uncheck();await expect(automatic).not.toBeChecked();
  await page.getByText('CSE 3311',{exact:true}).click();
  await expect(page.getByText('1,199 / 1,203',{exact:true})).toBeVisible();
  await expect(page.getByText('4 passages pending',{exact:true})).toBeVisible();
  await expect(page.getByText('0 / 4 searchable')).toBeVisible();
  await expect(page.getByRole('progressbar',{name:'CSE 3311 search progress'})).toHaveAttribute('value','1199');
  await page.getByRole('button',{name:'Update course search',exact:true}).click();
  await expect(page.getByText('Updating…',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Update course search',exact:true})).toBeDisabled();
  await page.reload();await expect(automatic).not.toBeChecked();
});

test('OFF to ON refreshes queued work immediately and polls through ready without a manual indexing request',async({page})=>{
  const calls=await settings(page,{automatic:false,readyOnPoll:true});
  await page.getByRole('switch',{name:'Keep Study Assistant search automatically updated'}).check();
  await expect(page.getByText('Updating…',{exact:true})).toBeVisible();
  await expect(page.getByText('Ready',{exact:true})).toBeVisible({timeout:15000});
  expect(calls.getIndexPosts()).toBe(0);
  await expect(page.getByRole('progressbar',{name:'CSE 3311 search progress'})).toHaveCount(0);
});

test('no warning has no fake percentage and a warning can be saved and reopened', async ({page}) => {
  await settings(page);
  await expect(page.locator('.api-usage-amount strong')).toHaveText('$3.42');
  await expect(page.getByRole('progressbar',{name:'Monthly EasyCanvas tracked usage'})).toHaveCount(0);
  await expect(page.getByText(/OpenAI.s billing dashboard is the authoritative account total/)).toBeVisible();
  await expect(page.getByText(/Tracking began Nov 3, 2026/)).toBeVisible();
  await page.getByText('Set monthly warning',{exact:true}).click();
  await page.getByLabel('Monthly API usage warning (USD)').fill('10');
  await page.getByRole('button',{name:'Save warning'}).click();
  expect(await page.getByRole('progressbar',{name:'Monthly EasyCanvas tracked usage'}).evaluate(node=>(node as HTMLProgressElement).value)).toBeCloseTo(34.2,4);
  await expect(page.getByText('34.2%',{exact:true})).toBeVisible();
  await page.reload();await page.getByText('Edit monthly warning',{exact:true}).click();
  await expect(page.getByLabel('Monthly API usage warning (USD)')).toHaveValue('10');
});

test('configured warning shows correct progress with a smaller monthly breakdown and token counts',async({page})=>{
  await settings(page,{warning:10});
  await expect(page.getByText('/ $10.00 warning',{exact:true})).toBeVisible();
  await expect(page.getByText('34.2%',{exact:true})).toBeVisible();
  await expect(page.getByText('8 tracked calls',{exact:true})).toBeVisible();
  await expect(page.getByText('16,915',{exact:true})).toBeVisible();
  await expect(page.getByText('1,360',{exact:true})).toBeVisible();
  await expect(page.getByText('Embeddings',{exact:true})).toBeVisible();
  await expect(page.getByText('Study Assistant / LLM',{exact:true})).toBeVisible();
  await expect(page.locator('.api-usage-breakdown')).toHaveAccessibleName('Tracked usage this month');
});

test('zero warning has no division by zero and does not disable API controls',async({page})=>{
  await settings(page,{warning:0});
  await expect(page.getByText('Warning level is $0.00. Any tracked cost reaches this warning.')).toBeVisible();
  await expect(page.getByText('Monthly warning reached. API requests are still allowed.')).toBeVisible();
  await expect(page.getByRole('progressbar',{name:'Monthly EasyCanvas tracked usage'})).toHaveCount(0);
  await expect(page.getByRole('switch',{name:'Keep Study Assistant search automatically updated'})).toBeEnabled();
});

test('failed indexing keeps a working retry and a failed save rolls back the switch',async({page})=>{
  await settings(page,{status:'failed',saveFails:true});
  await expect(page.getByText('Indexing failed',{exact:true})).toBeVisible();
  await page.getByText('CSE 3311',{exact:true}).click();
  await page.getByRole('button',{name:'Retry indexing'}).click();
  await expect(page.getByText('Updating…',{exact:true})).toBeVisible();
  await page.getByRole('switch',{name:'Keep Study Assistant search automatically updated'}).uncheck();
  await expect(page.getByText('Search settings could not be saved. Please retry.')).toBeVisible();
  await expect(page.getByRole('switch',{name:'Keep Study Assistant search automatically updated'})).toBeChecked();
  await expect(page.getByText('private provider detail')).toHaveCount(0);
});

for(const width of [1280,768,390]) test(`compact search settings fit at ${width}px`, async ({page}) => {
  await page.setViewportSize({width,height:1000}); await settings(page,{warning:10});
  await page.getByText('CSE 3311',{exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:`/private/tmp/easycanvas-settings-polished-${width}.png`,fullPage:true});
});

test('assistant course readiness updates without losing conversation context',async ({page})=>{
  let status=course();
  await page.route('**/api/courses',route=>route.fulfill({json:[{id:42,course_code:'TEST 100',name:'Test course'}]}));
  await page.route('**/api/courses/42/search-index',route=>{
    if(route.request().method()==='POST') status=course('updating');
    return route.fulfill({json:status});
  });
  await page.route('**/api/courses/42/conversations**',route=>route.fulfill({json:route.request().method()==='POST'
    ? {conversation_id:'00000000-0000-4000-8000-000000000042',title:'New conversation',messages:[],course_id:42} : []}));
  await page.goto('/study-assistant');await page.getByLabel('Course',{exact:true}).selectOption('42');
  await expect(page.getByText('1,199 / 1,203 passages searchable')).toBeVisible();
  await page.getByRole('button',{name:'Update course search',exact:true}).click();
  await expect(page.getByText('Updating course search — 4 passages remaining')).toBeVisible();
  await expect(page.getByRole('combobox',{name:'Conversation',exact:true})).toBeEnabled();
});

test('vision and embeddings switches show how each combination affects retrieval and responses',async({page})=>{
  await settings(page);
  const embeddings=page.getByRole('switch',{name:'Keep Study Assistant search automatically updated'});
  const vision=page.getByRole('switch',{name:'Read scanned pages and images (Vision)'});
  const notice=page.locator('.search-impact-notice');
  await expect(embeddings).toBeChecked();await expect(vision).toBeChecked();
  await expect(notice).toHaveCount(0);
  await vision.uncheck();await expect(vision).not.toBeChecked();
  await expect(notice).toContainText('Vision is off');
  await expect(notice).toContainText('affect Study Assistant retrieval and responses');
  await embeddings.uncheck();
  await expect(notice).toContainText('Both are off');
  await vision.check();
  await expect(notice).toContainText('Automatic search updates are off');
  await expect(notice).toContainText('Vision still reads images');
  await embeddings.check();
  await expect(notice).toHaveCount(0);
  await page.reload();await expect(notice).toHaveCount(0);
});

test('a vision setting saved as OFF loads as OFF with its warning',async({page})=>{
  await settings(page,{vision:false});
  await expect(page.getByRole('switch',{name:'Read scanned pages and images (Vision)'})).not.toBeChecked();
  await expect(page.locator('.search-impact-notice')).toContainText('Vision is off');
});

test('an older backend without the vision setting does not show a false "Vision is off" warning',async({page})=>{
  await settings(page);
  await page.unroute('**/api/search/settings');
  const legacy=preferences(null,true,true);delete (legacy.settings as {vision_enabled?:boolean}).vision_enabled;
  await page.route('**/api/search/settings',route=>route.fulfill({json:legacy}));
  await page.reload();
  const vision=page.getByRole('switch',{name:'Read scanned pages and images (Vision)'});
  await expect(vision).toBeDisabled();
  await expect(page.locator('.search-impact-notice')).toHaveCount(0);
  await expect(page.getByText('Restart the backend to manage this here.')).toBeVisible();
});

test('saving a key verifies it, masks it, clears the field, and never shows the key again',async({page})=>{
  const calls=await settings(page);
  await expect(page.getByText('Using the key from the backend .env file ending 1111.')).toBeVisible();
  const input=page.getByLabel('Use your own key');
  await expect(input).toHaveAttribute('type','password');
  await expect(page.getByRole('button',{name:'Save and verify'})).toBeDisabled();
  const secret='sk-proj-ZZZZZZZZZZZZZZZZZZZZZZZZZZ7777';
  await input.fill(secret);
  await page.getByRole('button',{name:'Save and verify'}).click();
  await expect(page.getByText('Using the key you saved here ending 7777.')).toBeVisible();
  await expect(page.getByText('Key verified and saved.')).toBeVisible();
  await expect(input).toHaveValue('');
  await expect(page.locator('body')).not.toContainText(secret);
  expect(await page.content()).not.toContain(secret);
  expect(calls.keyCalls.find(c=>c.method==='PUT')?.body).toEqual({api_key:secret});
  await page.reload();
  await expect(page.locator('body')).not.toContainText(secret);
  await expect(page.getByRole('button',{name:'Remove saved key'})).toBeVisible();
});

test('a rejected key shows the backend message and keeps the previous key in use',async({page})=>{
  await settings(page);
  await page.getByLabel('Use your own key').fill('sk-bad-KEYKEYKEYKEYKEYKEYKEYKEY');
  await page.getByRole('button',{name:'Save and verify'}).click();
  await expect(page.getByRole('alert').filter({hasText:'OpenAI rejected this key. Check it and try again.'})).toBeVisible();
  await expect(page.getByText('Using the key from the backend .env file ending 1111.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Remove saved key'})).toHaveCount(0);
});

test('removing the saved key returns to the .env key',async({page})=>{
  await settings(page,{keyStatus:{configured:true,source:'saved',hint:'2222'}});
  await expect(page.getByText('Using the key you saved here ending 2222.')).toBeVisible();
  await page.getByRole('button',{name:'Remove saved key'}).click();
  await expect(page.getByText('Using the key from the backend .env file ending 1111.')).toBeVisible();
  await expect(page.getByRole('button',{name:'Remove saved key'})).toHaveCount(0);
});

test('no key set explains what needs one',async({page})=>{
  await settings(page,{keyStatus:{configured:false,source:null,hint:null}});
  await expect(page.getByText(/No API key is set/)).toBeVisible();
});

test('a previous key keeps its own dollars and calls in the usage list',async({page})=>{
  const mk=(cost:number,calls:number)=>({...total,estimated_cost_usd:cost,calls});
  await settings(page,{keyStatus:{configured:true,source:'saved',hint:'2222'},byKey:[
    {hint:'2222',current:true,this_month:mk(0,0),total:mk(0,0),last_used:'2026-11-17T22:00:00Z'},
    {hint:'1111',current:false,this_month:mk(0.29,750),total:mk(0.29,750),last_used:'2026-11-17T21:00:00Z'}]});
  await page.getByText('Usage by API key (2)').click();
  const rows=page.locator('.api-usage-keys li');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText('Key ending 2222 (in use)');
  await expect(rows.nth(0)).toContainText('$0.00 this month · 0 calls');
  await expect(rows.nth(1)).toContainText('Key ending 1111');
  await expect(rows.nth(1)).toContainText('$0.29 this month · 750 calls');
});
