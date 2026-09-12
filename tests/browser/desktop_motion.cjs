// Real bundled UI and Framework7 in headless Edge; API responses are local fixtures.
// This test does not execute CAD conversion, installation, or a model request.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const repo=path.resolve(__dirname,'../..'),source=path.join(repo,'src/cad2gis/desktop');
const output=path.resolve(process.argv[2]||path.join(repo,'tmp/motion-browser-qa'));
fs.mkdirSync(output,{recursive:true});
const snapshot=Object.fromEntries(['app.js','style.css','index.html'].map(f=>[f,fs.readFileSync(path.join(source,f))]));
const hashes=Object.fromEntries(Object.entries(snapshot).map(([f,b])=>[f,crypto.createHash('sha256').update(b).digest('hex')]));
const results={scope:'Real frontend + bundled Framework7; local API fixtures only, no CAD engine/backend acceptance',hashes,scenarios:[],screenshots:[]};
const profile={base_url:'https://example.invalid/v1',model:'motion-fixture',capability:'json_object',token_field:'max_tokens',timeout_s:90,max_tokens:4096,max_requests:32,has_key:false,configured:false,issue:'UI fixture'};
const mime={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.woff2':'font/woff2','.svg':'image/svg+xml'};
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.CAD2GIS_BROWSER_CHANNEL||'msedge'});
 try {
  for(const width of [1440,375])for(const reducedMotion of ['no-preference','reduce']){
   const context=await browser.newContext({viewport:{width,height:width===375?850:1000},hasTouch:width===375,isMobile:width===375,reducedMotion});
   const page=await context.newPage(),errors=[],presses=[];let jobs=[],posts=0;
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url()),key=url.pathname.slice('/_app/api/'.length);
    if(url.hostname!=='motion-fixture.invalid')return route.abort();
    if(url.pathname.startsWith('/_app/api/')){
     let body;
     if(key==='status')body={remote:false,roots:['/motion-fixture'],provider_configured:{deepseek:false,'new-api':false},semantic_extraction:{available:true}};
     else if(key==='ai/config')body={profiles:{deepseek:profile,'new-api':profile},active:'deepseek',editable:true,storage:'UI fixture'};
     else if(key==='files')body={path:'/motion-fixture',parent:null,page:1,total:2,has_more:false,entries:[{name:'motion-fixture.dwg',path:'/motion-fixture/motion-fixture.dwg',directory:false},{name:'candidates.sqlite3',path:'/motion-fixture/candidates.sqlite3',directory:false}]};
     else if(key==='conversations')body={items:[{id:'fixture-chat',title:'动态对话按钮 · UI夹具'}],has_more:false};
     else if(key.startsWith('conversations/'))body={messages:[],plan:[],before:0,has_more:false};
     else if(key.startsWith('docs/'))body={content:'# 动效验收夹具\n\n这里不执行 CAD 引擎。'};
     else if(key==='jobs'){
      if(route.request().method()==='POST'){
       posts++;await new Promise(resolve=>setTimeout(resolve,240));
       const payload=route.request().postDataJSON();
       body={id:'fixture-job-'+posts,action:payload.action,status:'running',run_dir:'/motion-fixture/result-'+posts,log:'UI fixture only',artifacts:[]};jobs=[body,...jobs];
      }else body={jobs};
     }else if(key.endsWith('/cancel')){jobs[0].status='cancelled';body={};}
     else if(key==='database/schema')body={tables:[{name:'source_entities',columns:['handle','layer'],sql:'CREATE TABLE source_entities(handle TEXT,layer TEXT)'}]};
     else if(key==='database/query')body={columns:['handle','layer'],rows:[['1A','roads']],has_more:false};
     else return route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({detail:'Unavailable in UI fixture'})});
     return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    }
    if(url.pathname==='/_app/'){
     const html=snapshot['index.html'].toString('utf8').replace('</body>','<div style="position:fixed;left:4px;bottom:0;z-index:99999;font-size:10px;pointer-events:none">UI 动效夹具 · 不执行 CAD 引擎</div></body>');
     return route.fulfill({contentType:'text/html',body:html});
    }
    if(url.pathname.startsWith('/_app/assets/')){
     const name=url.pathname.slice('/_app/assets/'.length),file=path.resolve(source,name);
     if(!file.startsWith(source+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:'Missing fixture asset'});
     return route.fulfill({contentType:mime[path.extname(file)]||'application/octet-stream',body:snapshot[name]||fs.readFileSync(file)});
    }
    return route.abort();
   });
   await page.goto('http://motion-fixture.invalid/_app/');
   await page.waitForFunction(()=>appStatus&&!document.querySelector('#workspace').hidden&&document.documentElement.classList.contains('companion-sprite-ready'));
   await page.evaluate(()=>{
    window.motionQA={events:{project:[],help:[],tools:[]},presses:[]};
    for(const [name,popup] of Object.entries({project:projectPopup,help:helpPopup,tools:toolsPopup}))
     for(const event of ['open','opened','close','closed'])popup.on(event,()=>motionQA.events[name].push(event));
    document.addEventListener('pointerdown',event=>queueMicrotask(()=>{const b=event.target.closest('button');if(b)motionQA.presses.push({id:b.id,state:b.dataset.motionState,kind:event.pointerType});}));
   });
   async function waitEvent(name,event){await page.waitForFunction(({name,event})=>motionQA.events[name].at(-1)===event,{name,event});}
   const all=await page.locator('button').count();
   assert.equal(await page.locator('button.motion-button').count(),all);
   assert.ok(all>=75);
   const opener=page.locator('.titlebar [data-project]');
   if(width===375)await opener.tap();else{await opener.focus();await page.keyboard.press('Enter');}
   await waitEvent('project','opened');
   assert.equal(await page.locator('#project-popup').getAttribute('role'),'dialog');
   assert.ok(await page.evaluate(()=>projectPopup.el.contains(document.activeElement)));
   if(width!==375){
    await page.locator('#project-popup .popup-close').focus();await page.keyboard.press('Shift+Tab');
    assert.notEqual(await page.evaluate(()=>document.activeElement.className),'icon-button popup-close motion-button');
    await page.keyboard.press('Tab');assert.ok(await page.locator('#project-popup .popup-close').evaluate(e=>e===document.activeElement));
    await page.keyboard.press('Escape');
   }else await page.locator('#project-popup .popup-close').tap();
   await waitEvent('project','closed');
   assert.equal(await page.locator('#project-popup').evaluate(e=>getComputedStyle(e).display),'none');
   if(width!==375)assert.ok(await opener.evaluate(e=>e===document.activeElement));

   await page.locator('#open-tools')[width===375?'tap':'click']();await waitEvent('tools','opened');
   const toolsShot='tools-'+width+'-'+reducedMotion+'.png';await page.screenshot({path:path.join(output,toolsShot)});results.screenshots.push(toolsShot);
   await page.locator('#tools-popup [data-open-semantic]')[width===375?'tap':'click']();
   await waitEvent('project','opened');await waitEvent('tools','closed');
   await page.locator('#files .file').first()[width===375?'tap':'click']();
   assert.ok((await page.locator('#source').inputValue()).endsWith('.dwg'));
   assert.notEqual(await page.locator('#files .file').first().getAttribute('data-motion-state'),'success');
   await page.locator('#doctor').scrollIntoViewIfNeeded();
   await page.locator('#doctor')[width===375?'tap':'click']();
   await page.waitForFunction(()=>document.querySelector('#doctor').dataset.motionState==='working');
   assert.equal(posts,1);assert.notEqual(await page.locator('#doctor').getAttribute('data-motion-state'),'success');
   await page.waitForFunction(()=>records[0]?.status==='running');
   jobs[0].status='failed';await page.evaluate(()=>poll());
   assert.equal(await page.locator('#doctor').getAttribute('data-motion-state'),'error');
   await page.locator('#doctor')[width===375?'tap':'click']();
   await page.waitForFunction(()=>document.querySelector('#doctor').dataset.motionState==='working');
   await page.waitForFunction(()=>records[0]?.id==='fixture-job-2');
   jobs[0].status='succeeded';await page.evaluate(()=>poll());
   assert.equal(await page.locator('#doctor').getAttribute('data-motion-state'),'success');
   await page.locator('#doctor')[width===375?'tap':'click']();
   await page.waitForFunction(()=>document.querySelector('#doctor').dataset.motionState==='working');
   await page.waitForFunction(()=>records[0]?.id==='fixture-job-3');
   await page.locator('#cancel')[width===375?'tap':'click']();
   await page.waitForFunction(()=>document.querySelector('#doctor').dataset.motionState==='cancelled');
   assert.equal(await page.locator('#doctor').getAttribute('data-motion-state'),'cancelled');
   assert.equal(await page.locator('#jobs button.motion-button').count(),3);
   assert.equal(await page.locator('#files button.motion-button').count(),2);
   assert.ok(await page.locator('#conversations button.motion-button').count()>0);

   await page.locator('#project-popup .popup-close')[width===375?'tap':'click']();await waitEvent('project','closed');
   await page.locator('.titlebar [data-doc]')[width===375?'tap':'click']();await waitEvent('help','opened');
   await page.locator('#help-popup .popup-close')[width===375?'tap':'click']();await waitEvent('help','closed');
   // Real dynamic database table creation and query event wiring.
   await opener[width===375?'tap':'click']();await waitEvent('project','opened');
   await page.locator('#files .file').nth(1)[width===375?'tap':'click']();await waitEvent('project','closed');
   await page.waitForFunction(()=>document.querySelector('#tables button.motion-button'));
   const surface=await page.evaluate(()=>{
    const visible=[...document.querySelectorAll('button')].filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
    return {all:document.querySelectorAll('button').length,enhanced:document.querySelectorAll('button.motion-button').length,viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,
     smallTargets:visible.filter(e=>e.getBoundingClientRect().width<43.9||e.getBoundingClientRect().height<43.9).map(e=>({id:e.id,label:e.textContent.trim(),width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height})),
     backgroundAnimation:getComputedStyle(document.querySelector('.ambient')).animationName,paused:document.documentElement.dataset.motionPaused,
     popupListeners:[projectPopup,helpPopup,toolsPopup].map(p=>({id:p.el.id,animation:p.el.dom7Listeners?.animationend?.length||0,transition:p.el.dom7Listeners?.transitionend?.length||0})),presses:motionQA.presses};
   });
   assert.equal(surface.all,surface.enhanced);assert.equal(surface.smallTargets.length,0,JSON.stringify(surface.smallTargets));
   assert.ok(Math.max(surface.documentWidth,surface.bodyWidth)<=surface.viewport+1,JSON.stringify(surface));
   assert.ok(surface.popupListeners.every(p=>p.animation===0&&p.transition===0));
   assert.equal(surface.backgroundAnimation,reducedMotion==='reduce'?'none':'atelier-drift');
   assert.ok(surface.presses.some(p=>p.state==='press'&&p.kind===(width===375?'touch':'mouse')));
   assert.deepEqual(errors,[]);
   const shot='workspace-'+width+'-'+reducedMotion+'.png';await page.screenshot({path:path.join(output,shot)});results.screenshots.push(shot);
   results.scenarios.push({width,reducedMotion,jobPosts:posts,errors,surface,status:'PASS'});
   console.log(JSON.stringify({width,reducedMotion,status:'PASS',buttons:surface.all,jobPosts:posts}));
   await context.close();
  }
 }finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));}
 assert.equal(results.scenarios.length,4);
})().catch(error=>{console.error(error);process.exitCode=1;});
