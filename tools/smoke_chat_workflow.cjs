/* Deterministic UI responses stress real chat rendering; result opens a real run. */
const {chromium}=require(process.env.CAD2GIS_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const [url,run,output]=process.argv.slice(2);
assert(url&&run&&output,'Usage: node smoke_chat_workflow.cjs URL RUN OUTPUT');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const errors=[],reports=[];
 try{
  const context=await browser.newContext({viewport:{width:1440,height:900},storageState:process.env.CAD2GIS_BROWSER_STATE||undefined});
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  let submitted=false,finished=false,polls=0;
  const long='C:\\项目\\'+('very_long_tool_result_'.repeat(400));
  const result={run_dir:run,project_dir:path.join(path.dirname(run),'project'),drawing:path.join(path.dirname(run),'generated.dwg')};
  const history=()=>({id:'chat-fixture',title:'转换当前图纸',context:{},result:finished?result:{},plan:[],has_more:false,before:0,
   messages:[{role:'user',text:'转换当前图纸'},{role:'assistant',text:long+'\n```json\n'+JSON.stringify({output:long})+'\n```'}]});
  await page.route('**/_app/api/conversations*',route=>route.fulfill({json:submitted?{items:[{id:'chat-fixture',title:'转换当前图纸'}],has_more:false}:{items:[],has_more:false}}));
  await page.route('**/_app/api/conversations/chat-fixture',route=>route.fulfill({json:history()}));
  await page.route('**/_app/api/agent',async route=>{submitted=true;assert.equal(route.request().postDataJSON().message,'转换当前图纸');await route.fulfill({json:{id:'agent-fixture',conversation:'chat-fixture',status:'running'}});});
  await page.route('**/_app/api/agent/agent-fixture',route=>{polls++;return route.fulfill({json:{conversation:'chat-fixture',status:finished?'succeeded':'running',result:finished?result:{},progress:finished?'':long,
   usage:{requests:3,tool_calls:2},events:Array.from({length:40},(_,i)=>({time:i,name:'read_project_text / '+i,kind:i%2?'tool_result':'tool_call',detail:JSON.stringify({path:long})}))}});});
  await page.goto(url);
  await page.locator('#workspace').waitFor({state:'visible'});
  await page.locator('#chat-input').fill('转换当前图纸');
  await page.locator('#send').click();
  await page.locator('#agent-events details').first().waitFor();
  for(const [width,height] of [[1440,900],[1024,768],[768,700],[390,844]]){
   await page.setViewportSize({width,height});
   await page.waitForTimeout(350);
   const bounds=await page.evaluate(()=>{
    const box=selector=>{const r=document.querySelector(selector).getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,height:r.height};};
    return {width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,send:box('#send'),input:box('#chat-input'),dock:box('.bottom-dock'),messages:box('#chat-messages')};
   });
   assert(bounds.documentWidth<=width+1,JSON.stringify(bounds));
   assert(bounds.send.bottom<=bounds.dock.top&&bounds.send.right<=width,JSON.stringify(bounds));
   assert(bounds.input.top>=0&&bounds.messages.height>50,JSON.stringify(bounds));
   if(width>900){
    const last=page.locator('#agent-events details').last();
    assert.equal(await last.getAttribute('open'),null);
    await last.locator('summary').click();
    const before=polls;
    await page.waitForFunction(()=>document.querySelector('#agent-events details:last-child').open);
    await page.waitForTimeout(900);
    assert(polls>before);assert(await last.evaluate(el=>el.open),'Polling must preserve expanded details');
    const pre=await last.locator('pre').evaluate(el=>({height:el.clientHeight,scroll:el.scrollHeight,width:el.scrollWidth,client:el.clientWidth}));
    assert(pre.height<=202&&pre.scroll>pre.height&&pre.width<=pre.client+1,JSON.stringify(pre));
    await last.locator('summary').click();
   }
   await page.screenshot({path:path.join(output,`chat-${width}.png`)});
   reports.push(bounds);
  }
  await page.setViewportSize({width:1440,height:900});
  finished=true;
  await page.locator('#chat-result').waitFor({state:'visible'});
  assert.equal(await page.locator('#existing-run').inputValue(),run);
  await page.locator('#chat-result').click();
  await page.waitForFunction(()=>!document.querySelector('#confirm-map').disabled,{},{timeout:60000});
  assert.equal(await page.frameLocator('#osm-frame').locator('#hero-page').count(),0);
  // Let A finish after B to exercise the actual conversation click handlers.
  await page.locator('.bottom-dock [data-page="chat"]').click();
  await page.route('**/_app/api/conversations?page=*',route=>route.fulfill({json:{items:[{id:'a',title:'图纸 A'},{id:'b',title:'图纸 B'}],has_more:false}}));
  let enteredA,finishedA;
  const aStarted=new Promise(resolve=>{enteredA=resolve;});
  const aFinished=new Promise(resolve=>{finishedA=resolve;});
  await page.route('**/_app/api/conversations/a',async route=>{
   enteredA();await new Promise(resolve=>setTimeout(resolve,900));
   await route.fulfill({json:{...history(),id:'a',result:{},context:{source:'C:\\fixture\\A.dwg'},messages:[{role:'assistant',text:'A 的记录'}]}});finishedA();
  });
  await page.route('**/_app/api/conversations/b',route=>route.fulfill({json:{...history(),id:'b',result:{},context:{source:'C:\\fixture\\B.dwg'},messages:[{role:'assistant',text:'B 的记录'}]}}));
  await page.locator('#refresh-chats').click();
  await page.locator('#conversations button', {hasText:'图纸 A'}).click();
  await aStarted;
  await page.locator('#conversations button', {hasText:'图纸 B'}).click();
  await page.waitForFunction(()=>document.querySelector('#source').value==='C:\\fixture\\B.dwg');
  await aFinished;
  await page.waitForTimeout(200);
  assert.equal(await page.locator('#source').inputValue(),'C:\\fixture\\B.dwg');
  assert((await page.locator('#chat-messages').textContent()).includes('B 的记录'));
  assert(!(await page.locator('#chat-messages').textContent()).includes('A 的记录'));
  assert.deepEqual(errors,[]);
  await context.storageState({path:path.join(output,'session.json')});
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify({status:'passed',reports,errors},null,2));
  console.log(JSON.stringify({status:'passed',viewports:reports.length,resultOpened:true,conversationRace:'passed',errors}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
