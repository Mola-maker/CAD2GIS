/* Run against a local application and an existing synthetic conversion result. */
const {chromium}=require(process.env.CAD2GIS_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const [url,run,output]=process.argv.slice(2);
assert(url&&run&&output,'Usage: node smoke_review_workflow.cjs URL RUN OUTPUT');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 let page;
 const errors=[],failed=[],mutations=[];
 try{
  const context=await browser.newContext({viewport:{width:Number(process.env.CAD2GIS_VIEWPORT_WIDTH||1440),height:900},storageState:process.env.CAD2GIS_BROWSER_STATE||undefined});
  page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.url().startsWith(new URL(url).origin)&&r.status()>=400)failed.push([r.status(),r.url()]);});
  page.on('request',r=>{if(r.method()==='POST'&&/\/api\/(matching|review\/features|registration)/.test(r.url()))mutations.push(r.url());});
  await page.goto(url);
  await page.locator('#workspace').waitFor({state:'visible'});
  await context.storageState({path:path.join(output,'session.json')});
  await page.locator('.bottom-dock [data-page="osm"]').click();
  await page.locator('#existing-run').fill(run);
  await page.locator('#open-review').click();
  await page.waitForFunction(()=>!document.querySelector('#confirm-map').disabled,{},{timeout:60000});
  const map=page.frameLocator('#osm-frame');
  assert.equal(await map.locator('#hero-page').count(),0);
  assert.equal(await map.locator('script[src*="demo-fixture"],script[src*="hero-motion"]').count(),0);
  assert.equal(await map.locator('#stage-registration').isVisible(),false);
  const mapBox=await map.locator('#map').boundingBox();
  assert(mapBox.width>250&&mapBox.height>250,JSON.stringify(mapBox));
  await page.locator('#manual-gcp').click();
  await map.locator('#stage-registration').waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('#confirm-map').disabled);
  await page.locator('#manual-gcp').click();
  assert.equal(await map.locator('#stage-registration').isVisible(),false);
  // A repeated open must complete too, with visible feedback.
  await page.locator('#open-review').click();
  await page.waitForFunction(()=>!document.querySelector('#confirm-map').disabled,{},{timeout:60000});
  // Standalone/new-tab review must also support both directions of the GCP toggle.
  const standalone=await context.newPage();
  standalone.on('pageerror',e=>errors.push(e.message));
  await standalone.setViewportSize({width:850,height:900});
  await standalone.goto(await page.locator('#osm-frame').getAttribute('src'));
  await standalone.waitForFunction(()=>document.querySelector('#precision-view').textContent==='已应用结果');
  await standalone.locator('#toggle-manual-gcp').click();
  await standalone.locator('#stage-registration').waitFor({state:'visible'});
  await standalone.locator('#toggle-manual-gcp').click();
  await standalone.locator('#stage-registration').waitFor({state:'hidden'});
  await standalone.close();
  // A new matching revision must withdraw the old report and obtain a new binding.
  await page.waitForFunction(()=>document.querySelector('#svg-frame').getAttribute('src'));
  const oldReport=await page.locator('#svg-frame').getAttribute('src');
  // Dispatch from the child context so source-window validation is exercised.
  const child=page.frames().find(frame=>frame.url().includes('view='));
  await child.evaluate(()=>{const p=new URLSearchParams(location.search);parent.postMessage({type:'cad2gis-review-state',reviewId:p.get('review'),viewId:p.get('view'),ready:true,manual:false,revision:'browser-rematch-regression'},location.origin);});
  await page.waitForFunction(old=>{const src=document.querySelector('#svg-frame').getAttribute('src');return src&&src!==old;},oldReport);
  await child.evaluate(async()=>{const p=new URLSearchParams(location.search);const run=await (await fetch('/api/run?review='+encodeURIComponent(p.get('review')))).json();parent.postMessage({type:'cad2gis-review-state',reviewId:p.get('review'),viewId:p.get('view'),ready:true,manual:false,revision:(run.osm_matching?.record||run.osm_matching)?.match_sha256||'initial'},location.origin);});
  await page.waitForFunction(old=>document.querySelector('#svg-frame').getAttribute('src')===old,oldReport);
  await page.locator('#osm-frame').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,'map.png')});
  const scroll=await page.locator('#main-workspace').evaluate(el=>{el.scrollTop=el.scrollHeight;return {top:el.scrollTop,height:el.scrollHeight,client:el.clientHeight};});
  assert(scroll.top>0,JSON.stringify(scroll));
  await page.locator('#confirm-map').click();
  await page.locator('#tab-svg.tab-active').waitFor();
  const svg=page.frameLocator('#svg-frame');
  await svg.locator('img').first().waitFor();
  await page.waitForFunction(()=>{
   const imgs=[...document.querySelector('#svg-frame').contentDocument.querySelectorAll('img')];
   return imgs.length&&imgs.every(img=>img.complete&&img.naturalWidth>0);
  },{},{timeout:30000});
  const imageCount=await svg.locator('img').count();
  await page.screenshot({path:path.join(output,'svg.png')});
  await page.locator('#svg-next').click();
  await page.locator('#tab-sqlite.tab-active').waitFor();
  await page.waitForFunction(()=>Number(document.querySelector('#table-count').textContent)>0);
  assert.equal(mutations.length,0,'Viewing/accepting the current match must not run GCP fitting or rematching');
  assert.deepEqual(errors,[]);
  assert.deepEqual(failed,[]);
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({status:'passed',imageCount,mapBox,scroll,errors,failed,mutations},null,2));
  console.log(JSON.stringify({status:'passed',imageCount,mapBox,scroll}));
 }catch(error){
  if(page){await page.screenshot({path:path.join(output,'failure.png')});console.error(JSON.stringify({errors,failed,status:await page.locator('#review-load-status').textContent()}));}
  throw error;
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
