// Real desktop app.js + Framework7, with local API fixtures and the independent review document.
// No CAD conversion, model request, OSM request, or delivery approval is performed.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const repo=path.resolve(__dirname,'../..'),source=path.join(repo,'src/cad2gis/desktop'),mapSource=path.join(repo,'src/cad2gis/webdemo');
const output=path.resolve(process.argv[2]||path.join(repo,'tmp/desktop-review-flow-qa'));
fs.mkdirSync(output,{recursive:true});
const snapshot=Object.fromEntries(['app.js','style.css','index.html'].map(f=>[f,fs.readFileSync(path.join(source,f))]));
const mapHtml=fs.readFileSync(path.join(mapSource,'review.html'),'utf8');
// Exercise the real server's initial document without running the independent GIS renderer.
const mapShell=mapHtml.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const hashes=Object.fromEntries(Object.entries(snapshot).map(([f,b])=>[f,crypto.createHash('sha256').update(b).digest('hex')]));
const results={scope:'Real desktop code and bundled Framework7; local API fixtures; production independent review.html with GIS scripts disabled',hashes,checks:[],screenshots:[]};
const profile={base_url:'https://example.invalid/v1',model:'review-fixture',capability:'json_object',token_field:'max_tokens',timeout_s:90,max_tokens:4096,max_requests:32,has_key:false,configured:false,issue:'UI fixture only'};
const events=Array.from({length:40},(_,i)=>({kind:'tool_result',name:'转换证据 '+i,detail:JSON.stringify({artifacts:Array.from({length:100},(_,n)=>({id:n,path:'/fixture/'+'long-path-'.repeat(30),sha256:'a'.repeat(64)}))},null,2)}));
const chatArtifacts=[{kind:'qgis_package',run_dir:'/fixture/run',output_dir:'/fixture/package'}];
const mime={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.woff2':'font/woff2','.svg':'image/svg+xml'};
(async()=>{
 const launch={headless:true};
 if(process.env.CAD2GIS_BROWSER_EXECUTABLE)launch.executablePath=process.env.CAD2GIS_BROWSER_EXECUTABLE;
 else if(process.env.CAD2GIS_BROWSER_CHANNEL)launch.channel=process.env.CAD2GIS_BROWSER_CHANNEL;
 const browser=await chromium.launch(launch);
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),errors=[],unexpected=[];
  let reviewRequests=0,mapLoads=0,svgLoads=0,holdDelivery=false,releaseDelivery;
  const selectedRuns={},revisions={};
  const mapLoadsById={},runs={'/fixture/result':'fixture-review','/fixture/retry':'retry-review','/fixture/result-b':'fixture-review-b','/fixture/result-c':'fixture-review-b','/fixture/run':'chat-artifact-review'};
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.hostname!=='review-flow.invalid'){unexpected.push(url.href);return route.abort();}
   const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
   if(url.pathname.startsWith('/_app/api/')){
    const key=url.pathname.slice('/_app/api/'.length);
    if(key==='status')return json({remote:false,roots:['/fixture'],provider_configured:{deepseek:false,'new-api':false},semantic_extraction:{available:true},active_agent:'fixture-agent'});
    if(key==='ai/config')return json({profiles:{deepseek:profile,'new-api':profile},active:'deepseek',editable:true,storage:'UI fixture'});
    if(key==='files')return json({path:'/fixture',parent:null,page:1,total:0,has_more:false,entries:[]});
    if(key==='jobs')return json({jobs:[]});
    if(key==='conversations')return json({items:[{id:'fixture-chat',title:'长历史与人工复核回归'},{id:'fixture-empty',title:'尚无产物的另一对话'}],has_more:false});
    if(key==='conversations/fixture-chat')return json({messages:Array.from({length:30},(_,i)=>({role:'assistant',text:`历史消息 ${i}：查看源图、地图和符号后继续。`})),plan:[{status:'completed',text:'转换证据已整理'}],artifacts:chatArtifacts,before:0,has_more:false});
    if(key==='conversations/fixture-empty')return json({messages:[{role:'assistant',text:'此对话还没有生成交付文件。'}],plan:[],artifacts:[],before:0,has_more:false});
    if(key==='agent/fixture-agent')return json({id:'fixture-agent',conversation:'fixture-chat',status:'succeeded',events,progress:'',artifacts:chatArtifacts,usage:{requests:0,tool_calls:40}});
    if(key==='delivery/artifacts'){
     const run=url.searchParams.get('run');assert.ok(run in runs||run==='/fixture/package');
     if(run==='/fixture/package')return json({run_dir:run,run_status:'CONDITIONAL',archive:{name:'package.zip',size:16384},artifacts:[{name:'delivery.gpkg',size:1024},{name:'delivery.qgz',size:4096}]});
     if(holdDelivery&&run==='/fixture/result')await new Promise(resolve=>{releaseDelivery=resolve;});
     return json({run_status:'CONDITIONAL',artifacts:run==='/fixture/result-c'?[{name:'third-project.qgz',size:8192}]:run==='/fixture/result-b'?[{name:'second-project.qgz',size:8192}]:[{name:'delivery.gpkg',size:1024},{name:'project.qgz',size:4096}]});
    }
    if(key==='review'){
     reviewRequests++;const payload=route.request().postDataJSON();
     if(payload.run==='/fixture/missing')return json({detail:'找不到 run_manifest.json'},404);
     assert.ok(payload.run in runs);const id=runs[payload.run];selectedRuns[id]=payload.run;
     return json({url:`/?review=${id}#console-app`,review_id:id,run_dir:payload.run,districts:{scopes:[]}});
    }
    unexpected.push(url.pathname);return json({detail:'Unavailable in UI fixture'},404);
   }
   if(url.pathname==='/api/run'){
    const id=url.searchParams.get('review');
    return json({run_dir:selectedRuns[id],run_status:'CONDITIONAL',osm_matching:{match_sha256:revisions[id]||'initial'},svg_candidates:{report_url:'/fixture-svg/index.html'},delivery:{},artifacts:{}});
   }
   if(url.pathname==='/_app/')return route.fulfill({contentType:'text/html',body:snapshot['index.html']});
   if(url.pathname==='/'){
    const id=url.searchParams.get('review');assert.ok(Object.values(runs).includes(id));mapLoads++;mapLoadsById[id]=(mapLoadsById[id]||0)+1;
    if(id==='retry-review'&&mapLoadsById[id]===1)return route.fulfill({status:503,contentType:'text/html',body:'<!doctype html><h1>本地夹具：地图页面加载失败</h1>'});
    return route.fulfill({contentType:'text/html',body:mapShell});
   }
   if(url.pathname==='/fixture-svg/index.html'){
    svgLoads++;return route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="zh-CN"><title>SVG fixture</title><h1 id="svg-fixture">SVG 对应审阅报告 · 本地测试夹具</h1></html>'});
   }
   let file;
   if(url.pathname.startsWith('/_app/assets/')){
    const name=url.pathname.slice('/_app/assets/'.length);file=path.resolve(source,name);
    if(!file.startsWith(source+path.sep))return route.abort();
    if(snapshot[name])return route.fulfill({contentType:mime[path.extname(file)],body:snapshot[name]});
   }else if(url.pathname.startsWith('/assets/')){
    const relative=url.pathname.slice('/assets/'.length);
    file=[path.resolve(mapSource,relative),path.resolve(mapSource,'assets',relative)].find(candidate=>candidate.startsWith(mapSource+path.sep)&&fs.existsSync(candidate));
   }
   if(file&&fs.existsSync(file))return route.fulfill({contentType:mime[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
   unexpected.push(url.pathname);return route.fulfill({status:404,body:'Missing local fixture asset'});
  });
  const pass=(check,data={})=>{results.checks.push({check,status:'PASS',...data});console.log(JSON.stringify({check,status:'PASS'}));};
  const screenshot=async name=>{await page.screenshot({path:path.join(output,name)});results.screenshots.push(name);};
  const notifyMap=async(state,id,mark=true,runDir,overrides={})=>{
   if(runDir){selectedRuns[id]=runDir;revisions[id]='match:'+runDir;}
   await page.frameLocator('#osm-frame').locator('body').evaluate((body,{state,id,mark,runDir,revision,overrides})=>{
    if(mark)body.dataset.reviewState=state;
    parent.postMessage({type:'cad2gis-review-state',reviewId:id,viewId:new URLSearchParams(location.search).get('view'),state,runDir,revision,ready:state==='ready',manual:false,message:state==='error'?'本地夹具：地图要素初始化失败':'',...overrides},location.origin);
   },{state,id,mark,runDir:runDir||selectedRuns[id],revision:revisions[id]||'initial',overrides});
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  };
  const waitMapShell=async()=>{
   try{await page.frameLocator('#osm-frame').locator('#console-app').waitFor({timeout:5000});}
   catch(error){console.error(JSON.stringify({mapLoads,mapLoadsById,state:await page.evaluate(()=>({reviewFrameState,feedback:document.querySelector('#review-load-status').textContent,frameUrl:document.querySelector('#osm-frame').contentDocument?.URL,frameText:document.querySelector('#osm-frame').contentDocument?.body?.textContent.slice(0,150)}))}));throw error;}
   await page.waitForFunction(()=>!reviewOpening&&document.querySelector('#osm-frame').contentDocument?.readyState==='complete');
  };
  await page.goto('https://review-flow.invalid/_app/');
  await page.waitForFunction(()=>appStatus&&!document.querySelector('#workspace').hidden&&document.querySelectorAll('#agent-events details').length===40);
  assert.equal(await page.locator('#agent-events details[open]').count(),0);
  // Chromium can retain a rect for closed details content; the details row itself must collapse.
  assert.ok(await page.locator('#agent-events details').first().evaluate(e=>e.getBoundingClientRect().height<80));
  const layout=await page.evaluate(()=>{const main=document.querySelector('#main-workspace'),chat=document.querySelector('#chat-messages');return {outerHeight:main.scrollHeight,viewport:innerHeight,outerWidth:main.scrollWidth,width:main.clientWidth,chatHeight:chat.clientHeight,historyHeight:chat.scrollHeight};});
  assert.ok(layout.outerHeight<layout.viewport+100&&layout.outerWidth<=layout.width+1&&layout.historyHeight>layout.chatHeight,JSON.stringify(layout));
  await page.locator('#agent-events summary').first().click();
  await page.evaluate(events=>renderAgentEvents(events),events);
  assert.equal(await page.locator('#agent-events details[open]').count(),1);
  const expanded=await page.locator('#agent-events .event-detail').first().evaluate(e=>({height:e.getBoundingClientRect().height,scrollHeight:e.scrollHeight,clientHeight:e.clientHeight}));
  assert.ok(expanded.height<=240&&expanded.scrollHeight>expanded.clientHeight,JSON.stringify(expanded));
  pass('long_tool_history_collapsed_and_bounded',{layout,expanded});await screenshot('history-details.png');

  await page.locator('.bottom-dock [data-page="osm"]').click();
  await page.locator('#existing-run').fill('');await page.locator('#open-review').click();
  await page.waitForFunction(()=>document.querySelector('#review-load-status').classList.contains('error'));
  assert.match(await page.locator('#review-load-status').innerText(),/请填写结果目录/);
  assert.equal(reviewRequests,0);assert.ok(await page.locator('#existing-run').evaluate(e=>e===document.activeElement));
  pass('empty_run_shows_actionable_error');await screenshot('empty-run.png');

  await page.locator('#existing-run').fill('/fixture/missing');await page.locator('#open-review').click();
  await page.waitForFunction(()=>document.querySelector('#review-load-status').textContent.includes('找不到 run_manifest.json'));
  assert.match(await page.locator('#review-load-status').innerText(),/无法打开结果/);assert.ok(await page.locator('#open-review').isEnabled());
  assert.ok(await page.locator('#confirm-map').isDisabled());pass('backend_error_visible_and_retry_enabled');

  await page.locator('#existing-run').fill('/fixture/retry');await page.locator('#open-review').click();
  await page.waitForFunction(()=>!reviewOpening&&document.querySelector('#review-load-status').classList.contains('error'));
  assert.equal(mapLoadsById['retry-review'],1);assert.ok(await page.locator('#confirm-map').isDisabled());
  await page.locator('#open-review').click();await waitMapShell();
  assert.equal(mapLoadsById['retry-review'],2);assert.ok(await page.locator('#confirm-map').isDisabled());
  pass('failed_iframe_retries_same_run_with_a_new_document');
  // A static console and unrelated ready notifications do not prove map data is usable.
  await notifyMap('ready','different-review',false);assert.ok(await page.locator('#confirm-map').isDisabled());
  await notifyMap('ready','retry-review',false,undefined,{viewId:'previous-document'});assert.ok(await page.locator('#confirm-map').isDisabled());
  await notifyMap('loading','retry-review',false,undefined,{ready:true});assert.ok(await page.locator('#confirm-map').isDisabled());
  await page.evaluate(()=>window.postMessage({type:'cad2gis-review-state',reviewId:'retry-review',state:'ready'},location.origin));
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.ok(await page.locator('#confirm-map').isDisabled());
  await notifyMap('error','retry-review');
  await page.waitForFunction(()=>document.querySelector('#review-load-status').classList.contains('error'));
  assert.ok(await page.locator('#confirm-map').isDisabled());
  pass('map_initialization_failure_and_unrelated_ready_messages_cannot_approve');await screenshot('map-initialization-error.png');
  await page.locator('#open-review').click();await waitMapShell();
  assert.equal(mapLoadsById['retry-review'],3);assert.ok(await page.locator('#confirm-map').isDisabled());
  await notifyMap('ready','retry-review');await page.waitForFunction(()=>!document.querySelector('#confirm-map').disabled);
  pass('successful_retry_enables_confirmation_only_after_map_ready');

  // Confirmation rechecks the backend revision, even when this iframe claims it is ready.
  await notifyMap('ready','retry-review',true,undefined,{revision:'stale-map-revision'});
  await page.locator('#confirm-map').click();await waitMapShell();
  assert.equal(mapLoadsById['retry-review'],4);assert.ok(await page.locator('#confirm-map').isDisabled());
  assert.ok(await page.locator('#tab-osm').evaluate(e=>e.classList.contains('tab-active')));
  pass('revision_mismatch_reloads_map_and_cannot_advance');

  await page.locator('#existing-run').fill('/fixture/result');await page.locator('#open-review').click();await waitMapShell();
  assert.ok(await page.locator('#confirm-map').isDisabled());
  await notifyMap('ready','fixture-review');
  await page.waitForFunction(()=>!document.querySelector('#confirm-map').disabled&&!reviewOpening);
  const map=page.frameLocator('#osm-frame');
  assert.equal(await map.locator('#console-app').count(),1);
  assert.equal(await map.locator('#hero-page,[src*="demo-fixture"],[src*="hero-motion"]').count(),0);
  assert.equal(mapLoadsById['fixture-review'],1);assert.ok(await page.locator('#review-load-status').isVisible());
  pass('map_loads_without_legacy_demo_document');await screenshot('map-review.png');

  await page.locator('#osm-frame').evaluate(frame=>{frame.contentWindow.__reviewFlowIdentity='same-document';});
  await page.locator('#open-review').click();
  await page.waitForFunction(()=>!reviewOpening&&!document.querySelector('#confirm-map').disabled);
  assert.equal(mapLoadsById['fixture-review'],1);
  assert.equal(await page.locator('#osm-frame').evaluate(frame=>frame.contentWindow.__reviewFlowIdentity),'same-document');
  pass('same_run_keeps_iframe_document',{reviewRequests,mapLoads});

  await page.locator('#existing-run').fill('/fixture/not-opened');
  assert.ok(await page.locator('#confirm-map').isDisabled());assert.ok(await page.locator('#view-delivery').isDisabled());
  await page.locator('#existing-run').fill('/fixture/result');
  assert.ok(await page.locator('#confirm-map').isDisabled());
  await notifyMap('ready','fixture-review');assert.ok(await page.locator('#confirm-map').isDisabled());
  await page.locator('#open-review').click();
  await page.waitForFunction(()=>!reviewOpening&&!document.querySelector('#confirm-map').disabled);
  assert.equal(mapLoadsById['fixture-review'],1);assert.equal(await page.locator('#osm-frame').evaluate(frame=>frame.contentWindow.__reviewFlowIdentity),'same-document');
  pass('edited_run_cannot_confirm_previous_map');

  await page.locator('#confirm-map').click();
  await page.waitForFunction(()=>document.querySelector('#tab-svg').classList.contains('tab-active'));
  await page.frameLocator('#svg-frame').locator('#svg-fixture').waitFor();
  assert.match(await page.locator('#map-review-status').innerText(),/已人工确认当前地图匹配/);
  assert.ok(svgLoads>=1);pass('human_confirmation_advances_to_svg');await screenshot('svg-next-step.png');
  await notifyMap('ready','fixture-review',true,undefined,{ready:false,manual:true});
  await page.evaluate(()=>followWorkflow('export'));
  assert.ok(await page.locator('#tab-osm').evaluate(e=>e.classList.contains('tab-active')));
  assert.ok(await page.locator('#confirm-map').isDisabled());assert.ok(await page.locator('#view-delivery').isDisabled());
  await notifyMap('ready','fixture-review');
  assert.equal(await page.evaluate(()=>mapReviewed),false);
  await page.locator('#confirm-map').click();
  await page.waitForFunction(()=>document.querySelector('#tab-svg').classList.contains('tab-active'));
  pass('manual_correction_invalidates_confirmation_without_changing_result_status');
  await page.locator('#view-delivery').click();
  await page.waitForFunction(()=>document.querySelector('#tab-delivery').classList.contains('tab-active')&&document.querySelectorAll('#delivery-files a[download]').length===2);
  assert.match(await page.locator('#delivery-status').innerText(),/CONDITIONAL/);
  assert.equal(await page.locator('#delivery-path').innerText(),'/fixture/result');
  const downloads=await page.locator('#delivery-files a[download]').evaluateAll(links=>links.map(a=>({path:new URL(a.href).pathname,run:new URL(a.href).searchParams.get('run'),name:new URL(a.href).searchParams.get('name')})));
  assert.deepEqual(downloads,[{path:'/_app/api/delivery/artifact',run:'/fixture/result',name:'delivery.gpkg'},{path:'/_app/api/delivery/artifact',run:'/fixture/result',name:'project.qgz'}]);
  pass('delivery_step_lists_current_run_files_and_original_status');await screenshot('delivery-files.png');
  holdDelivery=true;
  const delayedRequest=page.waitForRequest(request=>request.url().includes('/delivery/artifacts?'));
  await page.evaluate(()=>{window.__lateDelivery=loadDelivery();});await delayedRequest;
  await page.locator('.bottom-dock [data-page="osm"]').click();
  await page.locator('#existing-run').fill('/fixture/result-b');await page.locator('#open-review').click();await waitMapShell();
  await notifyMap('ready','fixture-review-b');await page.waitForFunction(()=>!document.querySelector('#confirm-map').disabled);
  await page.locator('#confirm-map').click();await page.locator('#view-delivery').click();
  await page.waitForFunction(()=>document.querySelector('#delivery-files').textContent.includes('second-project.qgz'));
  assert.equal(typeof releaseDelivery,'function');holdDelivery=false;releaseDelivery();await page.evaluate(()=>window.__lateDelivery);
  assert.equal(await page.locator('#delivery-path').innerText(),'/fixture/result-b');
  assert.equal(await page.locator('#delivery-files a[download]').count(),1);
  const finalLink=new URL(await page.locator('#delivery-files a[download]').getAttribute('href'),'https://review-flow.invalid');
  assert.equal(finalLink.searchParams.get('run'),'/fixture/result-b');assert.equal(finalLink.searchParams.get('name'),'second-project.qgz');
  assert.doesNotMatch(await page.locator('#delivery-files').innerText(),/delivery\.gpkg/);
  pass('late_delivery_response_cannot_replace_new_run_files');await screenshot('current-run-after-late-response.png');
  await notifyMap('ready','fixture-review-b',true,'/fixture/result-c');
  assert.equal(await page.locator('#existing-run').inputValue(),'/fixture/result-c');
  assert.match(await page.locator('#map-review-status').innerText(),/新的匹配结果/);
  assert.equal(await page.locator('#delivery-files a[download]').count(),0);
  await page.evaluate(()=>followWorkflow('export'));
  assert.ok(await page.locator('#tab-osm').evaluate(e=>e.classList.contains('tab-active')));
  assert.ok(await page.locator('#confirm-map').isEnabled());
  await page.locator('#confirm-map').click();await page.locator('#view-delivery').click();
  await page.waitForFunction(()=>document.querySelector('#delivery-files').textContent.includes('third-project.qgz'));
  const newRunLink=new URL(await page.locator('#delivery-files a[download]').getAttribute('href'),'https://review-flow.invalid');
  assert.equal(newRunLink.searchParams.get('run'),'/fixture/result-c');assert.equal(newRunLink.searchParams.get('name'),'third-project.qgz');
  pass('new_matching_result_updates_run_and_requires_fresh_confirmation');
  await page.locator('.bottom-dock [data-page="chat"]').click();
  await page.locator('#chat-artifacts').waitFor({state:'visible'});
  assert.deepEqual(await page.locator('#chat-artifacts button').allTextContents(),['打开地图','SVG / PNG 预览','QGZ 与完整交付包']);
  await page.locator('#chat-artifacts').getByRole('button',{name:'SVG / PNG 预览',exact:true}).click();
  await page.frameLocator('#osm-frame').locator('#console-app').waitFor({state:'attached'});
  await page.waitForFunction(()=>!reviewOpening&&document.querySelector('#osm-frame').contentDocument?.readyState==='complete');
  await page.waitForFunction(()=>document.querySelector('#tab-svg').classList.contains('tab-active'));
  await page.frameLocator('#svg-frame').locator('#svg-fixture').waitFor();
  assert.equal(await page.locator('#existing-run').inputValue(),'/fixture/run');
  await notifyMap('ready','chat-artifact-review');
  await page.locator('.bottom-dock [data-page="chat"]').click();
  await page.locator('#chat-artifacts').getByRole('button',{name:'打开地图',exact:true}).click();
  await waitMapShell();
  assert.ok(await page.locator('#tab-osm').evaluate(e=>e.classList.contains('tab-active')));
  assert.equal(await page.locator('#existing-run').inputValue(),'/fixture/run');
  pass('chat_artifact_buttons_open_svg_preview_and_current_map');await screenshot('chat-artifact-map.png');

  await page.locator('.bottom-dock [data-page="chat"]').click();
  await screenshot('chat-qgis-artifact-buttons.png');
  await page.locator('#chat-artifacts').getByRole('button',{name:'QGZ 与完整交付包',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#tab-delivery').classList.contains('tab-active')&&document.querySelectorAll('#delivery-files a[download]').length===3);
  assert.equal(await page.locator('#delivery-path').innerText(),'/fixture/package');
  const packageDownloads=await page.locator('#delivery-files a[download]').evaluateAll(links=>links.map(a=>({path:new URL(a.href).pathname,run:new URL(a.href).searchParams.get('run'),name:new URL(a.href).searchParams.get('name')})));
  assert.deepEqual(packageDownloads,[{path:'/_app/api/delivery/archive',run:'/fixture/package',name:null},{path:'/_app/api/delivery/artifact',run:'/fixture/package',name:'delivery.gpkg'},{path:'/_app/api/delivery/artifact',run:'/fixture/package',name:'delivery.qgz'}]);
  assert.match(await page.locator('#delivery-files').innerText(),/完整 ZIP.*配套 GeoPackage/);
  assert.match(await page.locator('#delivery-status').innerText(),/CONDITIONAL/);
  pass('chat_qgz_button_uses_package_directory_and_complete_zip_download');await screenshot('chat-complete-qgis-package.png');

  await page.locator('#conversations').getByRole('button',{name:'尚无产物的另一对话',exact:true}).click();
  await page.waitForFunction(()=>conversation==='fixture-empty'&&document.querySelector('#chat-messages').textContent.includes('还没有生成交付文件'));
  assert.ok(await page.locator('#chat-artifacts').isHidden());
  assert.equal(await page.locator('#chat-artifacts button').count(),0);
  await page.locator('#conversations').getByRole('button',{name:'长历史与人工复核回归',exact:true}).click();
  await page.waitForFunction(()=>conversation==='fixture-chat'&&document.querySelectorAll('#chat-artifacts button').length===3);
  assert.ok(await page.locator('#chat-artifacts').isVisible());
  pass('chat_artifact_buttons_remain_scoped_to_selected_conversation');
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);pass('no_browser_errors_or_unexpected_requests');
  results.requests={reviewRequests,mapLoads,mapLoadsById,svgLoads};results.errors=errors;
 }finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));}
 assert.equal(results.checks.length,19);
})().catch(error=>{console.error(error);process.exitCode=1;});
