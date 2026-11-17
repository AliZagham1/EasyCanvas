import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
const source = readFileSync(new URL('../lib/search-index.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const api = await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
test('partial coverage names four pending passages, never an entirely unindexed course', () => {
  assert.equal(api.searchStatusLabel({status:'updating',remaining_chunks:4,indexed_chunks:1199,eligible_chunks:1203}), 'Updating course search — 4 passages remaining');
  assert.match(api.searchStatusLabel({status:'failed',remaining_chunks:4}), /4 passages remaining/);
  assert.equal(api.searchStatusLabel({status:'ready'}), 'Study Assistant ready');
});
test('manual updates use the backend only and settings preserve explicit OFF', async () => {
  const calls=[];
  const previous=global.fetch;
  global.fetch=async (url, init) => {calls.push([url,init]); return {ok:true,json:async()=>({})};};
  try { await api.updateSearch(42); await api.saveSearchSettings({auto_index:false,monthly_warning_usd:2}); }
  finally {global.fetch=previous;}
  assert.equal(calls[0][0],'http://127.0.0.1:8000/api/courses/42/search-index');
  assert.equal(calls[0][1].method,'POST');
  assert.deepEqual(JSON.parse(calls[1][1].body),{auto_index:false,monthly_warning_usd:2});
});
test('monthly warning progress is real, bounded, and absent without a positive denominator', () => {
  assert.equal(api.usageWarningProgress(3.42, null), null);
  assert.equal(api.usageWarningProgress(3.42, 0), null);
  const progress=api.usageWarningProgress(3.42,10);
  assert.ok(Math.abs(progress.value-34.2)<1e-8);
  assert.equal(progress.label,'34.2%');
  assert.deepEqual(api.usageWarningProgress(12,10),{value:100,label:'120.0%'});
  assert.equal(api.usageWarningProgress(.0017,10).label,'<0.1%');
});
test('money labels retain small tracked costs and compact course states', () => {
  assert.equal(api.formatApiCost(3.42),'$3.42');
  assert.equal(api.formatApiCost(.0017),'$0.0017');
  assert.equal(api.formatApiCost(0),'$0.00');
  assert.equal(api.formatApiCost(.00001),'<$0.0001');
  assert.equal(api.compactSearchStatus({status:'ready'}),'Ready');
  assert.equal(api.compactSearchStatus({status:'updating'}),'Updating…');
  assert.equal(api.compactSearchStatus({status:'pending',remaining_chunks:4}),'4 passages pending');
  assert.equal(api.compactSearchStatus({status:'failed'}),'Indexing failed');
});
test('impact notice appears whenever embeddings or vision is off and names the retrieval effect', () => {
  assert.equal(api.searchImpactNotice(true, true), null);
  const noEmbeddings = api.searchImpactNotice(false, true);
  const noVision = api.searchImpactNotice(true, false);
  const neither = api.searchImpactNotice(false, false);
  for (const lines of [noEmbeddings, noVision, neither]) assert.match(lines[0], /affect Study Assistant retrieval and responses/);
  assert.match(noEmbeddings[0], /search updates are off/);
  assert.match(noEmbeddings[1], /Update course search/);
  assert.match(noEmbeddings[1], /Vision still reads images/);
  assert.match(noVision[0], /Vision is off/);
  assert.match(noVision[1], /Scanned pages, images/);
  assert.match(neither[0], /Both are off/);
  assert.equal(new Set([noEmbeddings[0], noVision[0], neither[0]]).size, 3);
});
test('vision choice is saved on its own without touching the embedding setting', async () => {
  const calls=[]; const previous=global.fetch;
  global.fetch=async (url, init) => {calls.push([url,init]); return {ok:true,json:async()=>({})};};
  try { await api.saveSearchSettings({vision_enabled:false}); } finally {global.fetch=previous;}
  assert.equal(calls[0][1].method,'PATCH');
  assert.deepEqual(JSON.parse(calls[0][1].body),{vision_enabled:false});
});
