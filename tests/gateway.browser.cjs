/* Real local MCP demo UI: start examples/mcp_gateway_demo.py --serve first.
 * Use NODE_PATH for external Playwright tooling and GATEWAY_ADMIN_TOKEN in memory.
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {chromium}=require('playwright');
async function main(){
 const token=process.env.GATEWAY_ADMIN_TOKEN;
 assert.ok(token,'Set GATEWAY_ADMIN_TOKEN to the local demo token');
 const origin=process.env.GATEWAY_URL||'http://127.0.0.1:8780';
 const artifacts=path.join(os.tmpdir(),'agent-plane-console-qa');await fs.mkdir(artifacts,{recursive:true});
 const browser=await chromium.launch();
 const page=await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
 const errors=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r));
 try{
  await page.goto(origin+'/flow');await page.locator('#token').fill(token);await page.locator('#connect-button').click();
  await page.waitForFunction(()=>document.querySelector('#status').textContent==='EVIDENCE LOADED');
  assert.equal(await page.locator('#outcomes button').count(),3);
  for(let i=0;i<5;i++){await page.locator(`[data-step="${i}"]`).click();assert.equal(await page.locator(`[data-step="${i}"]`).getAttribute('aria-current'),'step');}
  await page.locator('#outcomes button.allow').click();await page.waitForFunction(()=>document.querySelector('#evidence').textContent.includes('successful result'));
  await page.screenshot({path:path.join(artifacts,'gateway-flow.png'),fullPage:true});
  await page.locator('#outcomes button.deny').click();assert.ok((await page.locator('#evidence').innerText()).includes('No upstream dispatch recorded'));
  await page.locator('#outcomes button.approval_required').click();assert.ok((await page.locator('#evidence').innerText()).includes('No upstream dispatch recorded'));
  assert.equal(await page.locator('#token').inputValue(),'');
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(artifacts,'gateway-flow-mobile.png'),fullPage:true});
  await page.locator('#disconnect').click();assert.equal(await page.locator('#outcomes button').count(),0);
  await page.setViewportSize({width:1440,height:1050});await page.goto(origin+'/console');
  await page.locator('#live-mode').click();await page.locator('#admin-token').fill(token);await page.locator('#connection-form button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelector('#connection-label').textContent.includes('CONNECTED'));
  assert.equal(await page.locator('.event-row').count(),3,'Execution receipts should not duplicate decisions');
  await page.locator('.event-row').filter({has:page.locator('.badge.allow')}).click();
  await page.locator('#tab-audit').click();
  await page.waitForFunction(()=>document.querySelector('#inspector-content').textContent.includes('completed'));
  await page.screenshot({path:path.join(artifacts,'gateway-console.png'),fullPage:true});
  const download=page.waitForEvent('download');await page.locator('#export-evidence').click();
  const saved=path.join(artifacts,'gateway-evidence.json');await (await download).saveAs(saved);
  const exported=await fs.readFile(saved,'utf8');assert.ok(!exported.includes(token));
  const data=JSON.parse(exported);assert.equal(data.related_execution_evidence.length,2);assert.equal(data.supplemental_context.execution_status,'completed');
  assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
  assert.ok(requests.every(r=>r.method()==='GET'),'Browser flow must remain read-only');
  assert.deepEqual(errors,[]);
  console.log('PASS: five-step flow, three real outcomes, execution receipts, mobile layout, read-only requests, credential-free export');
  console.log('Screenshots:',artifacts);
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exit(1)});
