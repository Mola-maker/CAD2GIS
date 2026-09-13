// Production desktop UI in Windows Chromium; local HTTP fixtures exercise delayed
// responses and failed image bytes. This does not contact an AI provider or convert CAD.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const repo=path.resolve(__dirname,'../..'),source=path.resolve(process.env.CAD2GIS_UI_SOURCE||path.join(repo,'src/cad2gis/desktop'));
const output=path.resolve(process.argv[2]||path.join(repo,'.omo/evidence/desktop-async-lifecycle'));
fs.mkdirSync(output,{recursive:true});
const results={scope:'Production desktop app.js and bundled Framework7; local API/image fixtures; no remote model',checks:[],errors:[]};
const profile={base_url:'https://example.invalid/v1',model:'fixture',capability:'json_object',token_field:'max_tokens',timeout_s:90,max_tokens:4096,max_requests:32,has_key:false,configured:false,issue:'UI fixture'};
const mime={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.woff2':'font/woff2','.svg':'image/svg+xml'};
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CAD2GIS_BROWSER_EXECUTABLE?{executablePath:process.env.CAD2GIS_BROWSER_EXECUTABLE}:{channel:process.env.CAD2GIS_BROWSER_CHANNEL||'msedge'})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
  let reportLoads=0,broken=false,reportError=false,heldImage=null,metadataQueue=[],agentQueue=[];
  page.on('pageerror',error=>results.errors.push(error.message));
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url()),json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(url.hostname!=='desktop-lifecycle.invalid')return route.abort();
   if(url.pathname.startsWith('/_app/assets/')){
    const file=path.resolve(source,url.pathname.slice('/_app/assets/'.length));
    if(file.startsWith(source+path.sep)&&fs.existsSync(file))return route.fulfill({contentType:mime[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
   }
   if(url.pathname==='/_app/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(source,'index.html'))});
   if(url.pathname==='/api/run'){
    if(metadataQueue.length)return metadataQueue.shift()(route,json);
    return json({svg_candidates:{report_url:'/report.html'}});
   }
   if(url.pathname==='/report.html'){
    reportLoads++;return route.fulfill(reportError?{status:404,contentType:'text/html',body:'<!doctype html><h1>Report not found</h1>'}:{contentType:'text/html',body:'<!doctype html><title>SVG 对应复核</title><h1>源实体对应复核 · HTTP fixture</h1><img src="/symbol.svg" width="140" height="140">'});
   }
   if(url.pathname==='/symbol.svg'){
    if(heldImage)await heldImage;
    return route.fulfill(broken?{status:404,contentType:'application/json',body:'{"detail":"SHA-256 mismatch"}'}:{contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="42" fill="#0077aa"/></svg>'});
   }
   const key=url.pathname.slice('/_app/api/'.length);
   if(key==='status')return json({remote:false,roots:['/fixture'],provider_configured:{deepseek:false,'new-api':false},semantic_extraction:{available:true}});
   if(key==='ai/config')return json({profiles:{deepseek:profile,'new-api':profile},active:'deepseek',editable:true,storage:'UI fixture'});
   if(key==='files')return json({path:'/fixture',parent:null,page:1,total:0,has_more:false,entries:[]});
   if(key==='jobs')return json({jobs:[]});
   if(key==='conversations')return json({items:[],has_more:false});
   if(key.startsWith('conversations/'))return json({messages:[{role:'assistant',text:'Fixture conversation'}],plan:[],artifacts:[],before:0,has_more:false});
   if(key.startsWith('agent/')){
    if(agentQueue.length)return agentQueue.shift()(route,json);
    return json({id:key.slice(6),conversation:'chat',status:'running',events:[],usage:{requests:1,tool_calls:0}});
   }
   return json({detail:'Unexpected fixture URL: '+url.pathname},404);
  });
  const check=async(name,fn)=>{try{await fn();results.checks.push({name,status:'PASS'});}catch(error){results.checks.push({name,status:'FAIL',error:error.message});}console.log(JSON.stringify(results.checks.at(-1)));};
  await page.goto('https://desktop-lifecycle.invalid/_app/');
  await page.waitForFunction(()=>appStatus&&!document.querySelector('#workspace').hidden);
  await page.evaluate(()=>{clearTimeout(timer);reviewId='current';reviewRevision='one';showPage('svg');});
  await page.frameLocator('#svg-frame').locator('img').waitFor();
  await page.waitForFunction(()=>document.querySelector('#svg-frame').contentDocument?.images[0]?.complete);
  await check('refresh_reloads_unchanged_report_url',async()=>{
   const before=reportLoads;await page.locator('#refresh-svg').click();await page.waitForTimeout(300);
   assert.ok(reportLoads>before,`Report load count remained ${reportLoads}`);
  });
  await check('failed_image_is_visible_as_error',async()=>{
   broken=true;await page.evaluate(()=>{reviewRevision='broken';return loadSvg();});
   await page.waitForFunction(()=>document.querySelector('#svg-frame').contentDocument?.images[0]?.complete);
   await page.waitForTimeout(100);
   assert.match(await page.locator('#svg-status').innerText(),/加载失败/);
   assert.ok(await page.locator('#svg-frame').isHidden());
  });
  await page.screenshot({path:path.join(output,'svg-image-error.png')});
  await check('html_error_document_cannot_report_success',async()=>{
   reportError=true;await page.evaluate(()=>{reviewRevision='http-error';return loadSvg();});await page.waitForTimeout(200);
   assert.match(await page.locator('#svg-status').innerText(),/加载失败/);
   assert.ok(await page.locator('#svg-frame').isHidden());reportError=false;
  });
  reportError=false;
  await check('missing_svg_report_does_not_invalidate_verified_map_revision',async()=>{
   metadataQueue.push((route,json)=>json({osm_matching:{match_sha256:'known-map'},svg_candidates:{report_url:null,report_blocker:'SVG report is unavailable'}}));
   assert.equal(await page.evaluate(()=>loadSvg()),'known-map');
   assert.match(await page.locator('#svg-status').innerText(),/unavailable/);
  });
  await check('stale_metadata_error_cannot_replace_current_report',async()=>{
   let release,started;const entered=new Promise(resolve=>{started=resolve;});
   metadataQueue.push(async(route,json)=>{started();await new Promise(resolve=>{release=resolve;});return json({detail:'obsolete metadata failure'},409);});
   await page.evaluate(()=>{window.__oldSvg=loadSvg().catch(error=>{document.querySelector('#svg-status').textContent=error.message;});});
   await entered;broken=false;
   await page.evaluate(()=>{reviewId='new';reviewRevision='new';return loadSvg();});
   await page.waitForFunction(()=>document.querySelector('#svg-frame').contentDocument?.images[0]?.naturalWidth>0);
   release();await page.evaluate(()=>window.__oldSvg);
   assert.doesNotMatch(await page.locator('#svg-status').innerText(),/obsolete/);
   assert.ok(await page.locator('#svg-frame').isVisible());
  });
  await check('reopening_loading_report_does_not_mark_pending_images_failed',async()=>{
   let release;heldImage=new Promise(resolve=>{release=resolve;});
   const imageRequest=page.waitForRequest(request=>new URL(request.url()).pathname==='/symbol.svg');
   await page.evaluate(()=>{reviewRevision='slow-image';return loadSvg();});await imageRequest;
   await page.evaluate(()=>loadSvg());
   assert.doesNotMatch(await page.locator('#svg-status').innerText(),/加载失败/);
   heldImage=null;release();
   await page.waitForFunction(()=>document.querySelector('#svg-status').textContent.includes('1 张图像已加载'));
   assert.ok(await page.locator('#svg-frame').isVisible());
  });
  await check('late_agent_terminal_event_cannot_clear_new_run',async()=>{
   let release,started;const entered=new Promise(resolve=>{started=resolve;});
   agentQueue.push(async(route,json)=>{started();await new Promise(resolve=>{release=resolve;});return json({id:'old-run',conversation:'chat',status:'succeeded',events:[{kind:'tool_result',name:'old result'}],usage:{requests:1,tool_calls:1}});});
   await page.evaluate(()=>{conversation='chat';agentRun='old-run';window.__oldAgent=pollAgent();});await entered;
   agentQueue.push((route,json)=>json({id:'new-run',conversation:'chat',status:'running',events:[{kind:'tool_call',name:'new tool input',detail:{path:'current.dwg'}}],progress:'Current model output',usage:{requests:1,tool_calls:1}}));
   await page.evaluate(()=>{agentRun='new-run';return pollAgent();});
   await page.evaluate(()=>clearTimeout(agentTimer));release();await page.evaluate(()=>window.__oldAgent);
   assert.equal(await page.evaluate(()=>agentRun),'new-run');
   assert.match(await page.locator('#agent-events').innerText(),/new tool input/);
   assert.equal(await page.locator('#agent-stream').innerText(),'Current model output');
   assert.ok(await page.locator('#send').isDisabled());
  });
  await page.evaluate(()=>{clearTimeout(agentTimer);showPage('chat');});
  await page.screenshot({path:path.join(output,'current-ai-events.png')});
  await check('no_browser_runtime_errors',async()=>assert.deepEqual(results.errors,[]));
 }finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));}
 assert.ok(results.checks.every(item=>item.status==='PASS'),'See results.json for failing browser scenarios');
})().catch(error=>{console.error(error);process.exitCode=1;});
