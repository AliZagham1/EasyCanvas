import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';

function course(status = 'pending', remaining = 4) {
  return {course_id:42,title:'2268-CSE-3311-001',eligible_chunks:1203,indexed_chunks:1203-remaining,
    pending_chunks:remaining,failed_chunks:status==='failed' ? remaining : 0,remaining_chunks:remaining,status,automatic:false,last_successful_at:null,
    documents:[{document_key:'file:1',title:'Assignment instructions',eligible_chunks:4,indexed_chunks:4-remaining,
      pending_chunks:remaining,failed_chunks:0,remaining_chunks:remaining,extraction_status:'ok'}]};
}
const total = {estimated_cost_usd:3.42,calls:8,unpriced_calls:0,input_tokens:16915,output_tokens:1360};
function preferences(warning: number|null = null, automatic = true) {
  const features={embedding:{...total,estimated_cost_usd:.0001},vision:{...total,estimated_cost_usd:0},assistant_other:{...total,estimated_cost_usd:3.4199}};
  return {settings:{auto_index:automatic,monthly_warning_usd:warning},usage:{today:{...total,estimated_cost_usd:.12},this_month:total,
    total:{...total,estimated_cost_usd:7.42,calls:12},...features,monthly_breakdown:features,
    warning_reached:warning!==null && 3.42>=warning,recording_started_at:'2026-11-03T12:00:00Z',notice:'Local estimate'}};
}
async function settings(page: Page, options: {warning?:number|null; automatic?:boolean; status?:string; readyOnPoll?:boolean; saveFails?:boolean} = {}) {
  let data = preferences(options.warning??null, options.automatic??true);
  let status = {...course(options.status),automatic:data.settings.auto_index};
  let polls=0, indexPosts=0;
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
  return {getIndexPosts:()=>indexPosts};
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
  await page.getByRole('switch').check();
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
  await expect(page.getByRole('switch')).toBeEnabled();
});

test('failed indexing keeps a working retry and a failed save rolls back the switch',async({page})=>{
  await settings(page,{status:'failed',saveFails:true});
  await expect(page.getByText('Indexing failed',{exact:true})).toBeVisible();
  await page.getByText('CSE 3311',{exact:true}).click();
  await page.getByRole('button',{name:'Retry indexing'}).click();
  await expect(page.getByText('Updating…',{exact:true})).toBeVisible();
  await page.getByRole('switch').uncheck();
  await expect(page.getByText('Search settings could not be saved. Please retry.')).toBeVisible();
  await expect(page.getByRole('switch')).toBeChecked();
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
