/* Optional browser regression suite; the console itself has no JS dependencies.
 * Install Playwright in a temporary directory, expose its node_modules through
 * NODE_PATH, install Chromium, and run against a local agentplane server:
 *   node tests/console.browser.cjs
 * CONSOLE_URL defaults to http://127.0.0.1:8765/console.
 * Screenshots and downloads are written to the OS temp directory.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const url = process.env.CONSOLE_URL || 'http://127.0.0.1:8765/console';
const artifactDir = path.join(os.tmpdir(), 'agent-plane-console-qa');
const token = 'console-test-token-not-for-export';
const lease = {id:'lease-live',subject:'live-agent',task:'live-task',actions:['branch.list'],resources:['github://acme/demo/*'],protected_resources:['github://acme/demo/main'],max_uses:{'branch.list':5},require_approval:[],expires_at:'2027-01-01T00:00:00Z',maximum_impact:'reversible',child_authority:'none',revoked:false};
function event(id='az_live', overrides={}) {
 return {decision_id:id,created_at:'2026-09-10T14:32:08',agent_id:'live-agent',user_id:'live-user',tenant:'acme',model_requested:'authorize:branch.list',model_used:'github://acme/demo/branches',decision:'allow',reason:'ACTION_WITHIN_TASK_AUTHORITY',rules_matched:['lease-live'],policy_version:null,event_hash:'sample-test-hash',prev_hash:'previous-test-hash',signature:'sample-test-signature',...overrides};
}
async function textIncludes(page, selector, expected) {
 await page.waitForFunction(({selector,expected})=>document.querySelector(selector)?.textContent.includes(expected),{selector,expected});
}
async function check(name, run) { await run(); console.log(`PASS ${name}`); }
async function connect(page) {
 await page.locator('#live-mode').click();
 await page.locator('#admin-token').fill(token);
 await page.locator('#connection-form button[type=submit]').click();
}
async function refresh(page) {
 const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/audit');
 await page.locator('#refresh-button').click();
 await response;
 await page.waitForFunction(()=>!document.querySelector('#refresh-button').disabled);
}
async function main() {
 await fs.mkdir(artifactDir,{recursive:true});
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']});
 const page=await context.newPage(), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const fixture={events:[event()],status:200,networkFail:false,leaseStatus:200,leaseDelay:0,auditCalls:0,activeAudit:0,maxAudit:0,auditDelay:0,requests:[],pendingLeases:[]};
 await page.route('**/healthz',r=>r.fulfill({json:{status:'ok',policy_version:'bundle-current'}}));
 await page.route('**/admin/policies',r=>r.fulfill({json:{policy_version:'bundle-current',rules:['current-policy']}}));
 await page.route('**/v1/audit?*',async r=>{
  fixture.auditCalls++;fixture.activeAudit++;fixture.maxAudit=Math.max(fixture.maxAudit,fixture.activeAudit);
  fixture.requests.push(r.request());
  try {
   if(fixture.auditDelay)await new Promise(resolve=>setTimeout(resolve,fixture.auditDelay));
   if(fixture.networkFail)await r.abort('failed');
   else await r.fulfill({status:fixture.status,json:fixture.status===200?{events:fixture.events}:{detail:'Unauthorized'}});
  } finally {fixture.activeAudit--;}
 });
 await page.route('**/v1/leases/*',async r=>{
  fixture.requests.push(r.request());
  const id=decodeURIComponent(new URL(r.request().url()).pathname.split('/').pop());
  if(id==='lease-slow') {fixture.pendingLeases.push(r);return;}
  if(fixture.leaseDelay)await new Promise(resolve=>setTimeout(resolve,fixture.leaseDelay));
  await r.fulfill({status:fixture.leaseStatus,json:fixture.leaseStatus===200?{...lease,id}:{detail:'lease not found'}});
 });
 // Freeze wall time so polling is driven explicitly, without time-sensitive tests.
 await page.clock.install({time:new Date('2026-09-10T14:35:00Z')});
 await page.clock.pauseAt(new Date('2026-09-10T14:35:00Z'));
 try {
  await page.goto(url);
  await check('default demo: eight events, protected branch denial, five inspector tabs',async()=>{
   assert.equal(await page.locator('.event-row').count(),8);
   assert.equal(await page.locator('[role=tab]').count(),5);
   await textIncludes(page,'#decision-summary','RESOURCE_PROTECTED');
   assert.equal(await page.locator('#tab-lease').getAttribute('aria-selected'),'true');
   assert.equal(fixture.auditCalls,0);
   await page.screenshot({path:path.join(artifactDir,'desktop.png'),fullPage:true});
  });
  await check('selection updates chain, graph and inspector; tabs persist across all outcomes',async()=>{
   await page.locator('.event-row').nth(1).click();
   await textIncludes(page,'#execution','branch.list');
   await textIncludes(page,'#decision-summary','ACTION_WITHIN_TASK_AUTHORITY');
   await page.locator('#tab-consequence').click();
   assert.equal(await page.locator('.consequence-step').count(),6);
   await textIncludes(page,'#inspector-content','Branch metadata returned');
   await page.locator('.event-row').nth(2).click();
   await textIncludes(page,'#execution','APPROVAL REQUIRED');
   assert.equal(await page.locator('#tab-consequence').getAttribute('aria-selected'),'true');
   await textIncludes(page,'#inspector-content','Approval checkpoint');
   await textIncludes(page,'#graph-canvas','release-agent');
   await page.screenshot({path:path.join(artifactDir,'approval-consequence.png'),fullPage:true});
  });
  await check('graph highlights topology growth and replays only the selected evidence',async()=>{
   await page.locator('.event-row').nth(5).click();
   assert.equal(await page.locator('[data-node=parent]').getAttribute('data-change'),'added');
   await textIncludes(page,'#graph-activity-text','1 added');
   assert.ok(await page.locator('.graph-pulse').count()>0);
   assert.ok(await page.locator('[data-node=human]').evaluate(node=>node.getAnimations().some(a=>a.effect.getKeyframes().some(f=>f.transform))));
   await page.screenshot({path:path.join(artifactDir,'dynamic-graph.png'),fullPage:true,animations:'disabled'});
   const selected=await page.locator('#inspector-event-id').textContent();
   await page.locator('#replay-graph').click();
   await textIncludes(page,'#graph-activity-text','Sample path replay');
   assert.equal(await page.locator('#inspector-event-id').textContent(),selected);
   assert.equal(fixture.auditCalls,0);
   await page.locator('#graph-motion').click();
   assert.equal(await page.locator('.graph-pulse').count(),0);
   assert.equal(await page.locator('#graph-canvas').evaluate(node=>node.getAnimations({subtree:true}).length),0);
   await page.locator('.event-row').nth(2).click();
   await textIncludes(page,'#graph-activity-text','1 removed');
   assert.equal(await page.locator('.graph-pulse').count(),0);
   await page.locator('#graph-motion').click();
  });
  await check('reduced motion keeps change evidence without animation',async()=>{
   await page.emulateMedia({reducedMotion:'reduce'});
   await textIncludes(page,'#graph-motion','Reduced motion');
   await page.locator('.event-row').nth(5).click();
   await textIncludes(page,'#graph-activity-text','1 added');
   assert.equal(await page.locator('.graph-pulse').count(),0);
   assert.equal(await page.locator('#graph-canvas').evaluate(node=>node.getAnimations({subtree:true}).length),0);
   await page.emulateMedia({reducedMotion:'no-preference'});
   await page.locator('.event-row').nth(2).click();
  });
  await check('keyboard node inspection, tab navigation, and delegation attenuation',async()=>{
   await page.locator('[data-node=lease]').focus();await page.keyboard.press('Enter');
   await textIncludes(page,'#graph-details','lease-release-cleanup');
   await page.locator('#tab-consequence').focus();await page.keyboard.press('ArrowRight');
   assert.equal(await page.locator('#tab-delegation').getAttribute('aria-selected'),'true');
   await page.locator('.event-row').nth(5).click();
   await textIncludes(page,'#inspector-content','lease-branch-reader');
   await textIncludes(page,'#graph-canvas','PARENT AUTHORITYLEASE');
   await textIncludes(page,'#inspector-content','Child authority is narrowed');
   await page.locator('#tab-lease').click();await page.locator('.event-row').nth(6).click();
   await textIncludes(page,'#inspector-content','Expired');
  });
  await check('filters preserve selected evidence and expose empty matches',async()=>{
   await page.locator('#decision-filter').selectOption('approval_required');
   assert.equal(await page.locator('.event-row').count(),1);
   await textIncludes(page,'#status-message','hidden by the stream filter');
   await page.locator('#event-search').fill('no-such-event');
   await textIncludes(page,'#stream-feed','No matching decisions');
   await page.locator('#decision-filter').selectOption('all');await page.locator('#event-search').fill('');
  });
  await check('copy and export preserve original evidence and sample provenance',async()=>{
   await page.locator('#copy-id').click();
   assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'az_demo_008415');
   const download=page.waitForEvent('download');await page.locator('#export-evidence').click();
   const saved=path.join(artifactDir,'demo-evidence.json');await (await download).saveAs(saved);
   const data=JSON.parse(await fs.readFile(saved,'utf8'));
   assert.match(data.provenance,/illustrative/);assert.equal(data.original_evidence.decision_id,'az_demo_008415');
   assert.equal(data.supplemental_context.execution_status,'not recorded');
  });
  await check('mobile contains overflow within graph and chain; tabs stay accessible',async()=>{
   await page.locator('.event-row').first().click();await page.setViewportSize({width:390,height:844});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert.ok(await page.locator('#graph-canvas').evaluate(e=>e.scrollWidth>e.clientWidth));
   await page.locator('#fit-graph').click();await page.clock.runFor(3100);
   await page.screenshot({path:path.join(artifactDir,'mobile.png'),fullPage:true});
   await page.setViewportSize({width:1440,height:1000});
  });
  await check('live connection isolates sample data and labels current lease context',async()=>{
   await connect(page);await textIncludes(page,'#inspector-content','CURRENT LEASE');
   assert.equal(await page.locator('.event-row').count(),1);
   await textIncludes(page,'#execution','live-task');
   assert.ok(!(await page.locator('main').innerText()).includes('Morgan Chen'));
   await page.locator('#tab-consequence').click();await textIncludes(page,'#inspector-content','No consequence evidence');
   await page.locator('#tab-delegation').click();await textIncludes(page,'#inspector-content','Delegation ancestry not recorded');
   await page.locator('#tab-audit').click();await textIncludes(page,'#inspector-content','SIGNATURE PRESENT');
   await page.locator('#tab-grant').click();await textIncludes(page,'#inspector-content','Not included in audit event');
   assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
   assert.ok(fixture.requests.every(r=>r.method()==='GET'));
   assert.ok(fixture.requests.every(r=>r.headers()['x-admin-token']===token));
  });
  await check('pause/resume controls polling and prevents overlapping audit requests',async()=>{
   await page.locator('#pause-button').click();const before=fixture.auditCalls;
   await page.clock.runFor(12000);assert.equal(fixture.auditCalls,before);
   await page.locator('#pause-button').click();await page.waitForResponse(r=>new URL(r.url()).pathname==='/v1/audit');
   fixture.auditDelay=100;
   await page.clock.runFor(8000);
   await page.waitForFunction(()=>!document.querySelector('#refresh-button').disabled);
   assert.equal(fixture.maxAudit,1);fixture.auditDelay=0;
  });
  await check('new events preserve selection, including outside the polling window',async()=>{
   fixture.events=[event('az_new',{model_requested:'authorize:branch.delete',decision:'deny',reason:'RESOURCE_PROTECTED'}),event()];
   await refresh(page);await textIncludes(page,'#inspector-event-id','az_live');
   fixture.events=[fixture.events[0]];await refresh(page);
   await textIncludes(page,'#status-message','outside the latest polling window');
   await textIncludes(page,'#inspector-event-id','az_live');
  });
  await check('unchanged polling preserves graph DOM and never restarts path motion',async()=>{
   await page.locator('#pause-button').click();
   await page.clock.runFor(3000);
   await page.locator('.authority-svg').evaluate(node=>node.dataset.retained='yes');
   await refresh(page);
   await page.waitForResponse(r=>new URL(r.url()).pathname.startsWith('/v1/leases/'));
   assert.equal(await page.locator('.authority-svg').getAttribute('data-retained'),'yes');
   assert.equal(await page.locator('.graph-pulse').count(),0);
   await page.locator('#pause-button').click();
  });
  await check('current lease changes highlight affected nodes without changing the decision',async()=>{
   const selected=await page.locator('#inspector-event-id').textContent();
   lease.resources.push('github://acme/another/*');
   await refresh(page);
   await textIncludes(page,'#graph-canvas','2 resource patterns');
   await textIncludes(page,'#graph-activity-text','Evidence context updated');
   assert.equal(await page.locator('[data-node=resources]').getAttribute('data-change'),'updated');
   assert.equal(await page.locator('[data-node=lease]').getAttribute('data-change'),'updated');
   assert.equal(await page.locator('#inspector-event-id').textContent(),selected);
   lease.resources.pop();
  });
  await check('graph keyboard focus survives live refresh',async()=>{
   await page.locator('[data-node=lease]').focus();await page.keyboard.press('Enter');
   await page.clock.runFor(4100);
   await page.waitForFunction(()=>!document.querySelector('#refresh-button').disabled);
   assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('data-node')),'lease');
  });
  await check('authentication and network failures retain evidence with explicit stale status',async()=>{
   fixture.status=401;await refresh(page);await textIncludes(page,'#status-message','Authentication failed');
   await textIncludes(page,'#connection-label','STALE');
   fixture.status=200;fixture.networkFail=true;
   await page.locator('#refresh-button').click();await textIncludes(page,'#status-message','Runtime unavailable');
   await textIncludes(page,'#inspector-event-id','az_live');fixture.networkFail=false;
  });
  await check('missing lease and absent historical fields remain unknown',async()=>{
   fixture.events=[event('az_missing')];fixture.leaseStatus=404;await refresh(page);
   await page.locator('.event-row').click();await page.locator('#tab-lease').click();
   await textIncludes(page,'#inspector-content','Referenced lease is unavailable (404)');
   await textIncludes(page,'#execution','Not recorded');fixture.leaseStatus=200;
  });
  await check('late lease response cannot replace a more recently selected event',async()=>{
   fixture.events=[event('az_slow',{rules_matched:['lease-slow']}),event('az_fast',{rules_matched:['lease-fast']})];await refresh(page);
   await page.locator('.event-row').first().click();await page.locator('.event-row').nth(1).click();
   await textIncludes(page,'#inspector-content','lease-fast');
   for(const r of fixture.pendingLeases.splice(0))await r.fulfill({json:{...lease,id:'lease-slow'}}).catch(()=>{});
   await textIncludes(page,'#inspector-event-id','az_fast');assert.ok(!(await page.locator('#execution').innerText()).includes('lease-slow'));
  });
  await check('untrusted strings are inert and live export excludes the connection token',async()=>{
   const malicious='<img src=x onerror="window.consoleInjected=true">';
   fixture.events=[event('az_injection',{agent_id:malicious,reason:malicious,rules_matched:[],model_used:'github://acme/'+('long-resource-'.repeat(40))})];await refresh(page);await page.locator('.event-row').click();
   assert.equal(await page.locator('main img').count(),0);assert.equal(await page.evaluate(()=>window.consoleInjected),undefined);
   await textIncludes(page,'#execution',malicious);
   const download=page.waitForEvent('download');await page.locator('#export-evidence').click();
   const saved=path.join(artifactDir,'live-evidence.json');await (await download).saveAs(saved);
   const exported=await fs.readFile(saved,'utf8');assert.ok(!exported.includes(token));
   assert.equal(JSON.parse(exported).original_evidence.agent_id,malicious);
   await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.setViewportSize({width:1440,height:1000});
  });
  await check('explicit delegation evidence renders parent-child pairs without invented ancestry',async()=>{
   fixture.events=[event('az_delegation',{model_requested:'lease-delegate:lease-parent',model_used:'lease-live',rules_matched:['lease-parent']}),event('az_other_child',{model_requested:'lease-delegate:lease-parent',model_used:'lease-other',rules_matched:['lease-parent']}),event('az_parent_action',{rules_matched:['lease-parent']})];
   await refresh(page);await page.locator('.event-row').first().click();await page.locator('#tab-delegation').click();
   await textIncludes(page,'#inspector-content','lease-parent');await textIncludes(page,'#inspector-content','lease-live');
   assert.equal(await page.locator('.tree').count(),1);
   await page.locator('.event-row').nth(2).click();
   assert.equal(await page.locator('.tree').count(),2);
   assert.deepEqual(await page.locator('.tree').evaluateAll(paths=>paths.map(p=>p.querySelectorAll('.tree-node').length)),[2,2]);
  });
  await check('disconnect clears credentials and evidence; empty live history has a real empty state',async()=>{
   await page.locator('#connection-button').click();await page.locator('#disconnect-button').click();
   assert.equal(await page.locator('.event-row').count(),0);await textIncludes(page,'#execution','—');
   fixture.events=[];await page.locator('#connection-button').click();await page.locator('#admin-token').fill(token);await page.locator('#connection-form button[type=submit]').click();
   await textIncludes(page,'#stream-feed','The audit stream is empty');
   await page.locator('#demo-mode').click();assert.equal(await page.locator('.event-row').count(),8);
  });
  assert.deepEqual(errors,[],'No uncaught browser errors');
  console.log(`Browser checks complete. Artifacts: ${artifactDir}`);
 } finally {await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
