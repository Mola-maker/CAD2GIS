// Layout regression with the real HTML and bundled Framework7 CSS.
// Long history is a local fixture; no API, CAD engine, or model is contacted.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const repo=path.resolve(__dirname,'../..'),source=path.join(repo,'src/cad2gis/desktop');
const output=path.resolve(process.argv[2]||path.join(repo,'tmp/chat-layout-browser-qa'));
const html=fs.readFileSync(path.join(source,'index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const results=[];
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const options={headless:true};
 if(process.env.CAD2GIS_BROWSER_EXECUTABLE)options.executablePath=process.env.CAD2GIS_BROWSER_EXECUTABLE;
 else if(process.env.CAD2GIS_BROWSER_CHANNEL)options.channel=process.env.CAD2GIS_BROWSER_CHANNEL;
 const browser=await chromium.launch(options);
 try{
  for(const viewport of [{width:1440,height:1000},{width:1140,height:1095},{width:375,height:850},{width:800,height:500}]){
   const page=await browser.newPage({viewport,reducedMotion:'reduce'});
   await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.hostname!=='chat-layout.invalid')return route.abort();
    if(url.pathname==='/_app/')return route.fulfill({contentType:'text/html',body:html});
    if(url.pathname.startsWith('/_app/assets/')){
     const file=path.resolve(source,url.pathname.slice('/_app/assets/'.length));
     if(file.startsWith(source+path.sep)&&fs.existsSync(file))return route.fulfill({body:fs.readFileSync(file),contentType:path.extname(file)==='.css'?'text/css':path.extname(file)==='.png'?'image/png':'application/octet-stream'});
    }
    return route.abort();
   });
   await page.goto('http://chat-layout.invalid/_app/');
   await page.evaluate(()=>{
    document.querySelector('#workspace').hidden=false;
    const messages=document.querySelector('#chat-messages');messages.textContent='';
    for(let i=0;i<100;i++){
     const row=document.createElement('div');row.className='message-row assistant';
     const content=document.createElement('div');content.className='message-content';
     const text=document.createElement(i%10===0?'pre':'p');
     text.textContent=i%10===0?JSON.stringify({geometry:Array.from({length:200},(_,n)=>({id:n,path:'/source/'+'long-name-'.repeat(50)}))},null,2):`历史消息 ${i}：请检查地图与源图的线条和标注。`;
     content.append(text);row.append(content);messages.append(row);
    }
    const events=document.querySelector('#agent-events');events.textContent='';
    for(let i=0;i<40;i++){
     const row=document.createElement('details');row.className='event tool_result';
     const title=document.createElement('summary');title.textContent='转换证据 '+i;
     const detail=document.createElement('pre');detail.className='event-detail';detail.textContent=JSON.stringify({artifacts:Array.from({length:100},(_,n)=>({id:n,sha256:'e'.repeat(64),source:'/fixture/'+'long-name-'.repeat(20)}))},null,2);
     row.append(title,detail);events.append(row);
    }
   });
   const metrics=await page.evaluate(()=>{
    const rect=e=>({width:e.getBoundingClientRect().width,height:e.getBoundingClientRect().height});
    const main=document.querySelector('#main-workspace'),messages=document.querySelector('#chat-messages'),layout=document.querySelector('.chat-layout'),composer=document.querySelector('#chat-form'),activity=document.querySelector('.activity-panel');
    return {viewport:{width:innerWidth,height:innerHeight},main:{...rect(main),scrollHeight:main.scrollHeight,clientHeight:main.clientHeight,scrollWidth:main.scrollWidth,clientWidth:main.clientWidth},messages:{...rect(messages),scrollHeight:messages.scrollHeight,clientHeight:messages.clientHeight},layout:rect(layout),composer:rect(composer),activity:{...rect(activity),scrollHeight:activity.scrollHeight,clientHeight:activity.clientHeight,visible:getComputedStyle(activity).display!=='none'}};
   });
   assert.ok(metrics.main.scrollWidth<=metrics.main.clientWidth+1,JSON.stringify(metrics));
   assert.ok(metrics.main.scrollHeight<viewport.height+650,'History must not set the outer workspace height: '+JSON.stringify(metrics));
   assert.ok(metrics.messages.height>=80&&metrics.messages.height<=viewport.height,'Messages need a bounded visible scroll area: '+JSON.stringify(metrics));
   assert.ok(metrics.messages.scrollHeight>metrics.messages.clientHeight+1000,JSON.stringify(metrics));
   assert.ok(metrics.messages.width>viewport.width*(viewport.width<=650?.75:.4),'Conversation must retain a usable column: '+JSON.stringify(metrics));
   if(metrics.activity.visible){
    assert.ok(metrics.activity.scrollHeight>metrics.activity.clientHeight&&metrics.activity.height<=metrics.layout.height+1,JSON.stringify(metrics));
    await page.locator('#agent-events summary').first().click();
    const expanded=await page.locator('#agent-events .event-detail').first().evaluate(e=>({height:e.getBoundingClientRect().height,scrollHeight:e.scrollHeight,clientHeight:e.clientHeight}));
    assert.ok(expanded.height>0&&expanded.height<=240&&expanded.scrollHeight>expanded.clientHeight,JSON.stringify(expanded));
    assert.ok(Math.abs((await page.locator('.chat-layout').boundingBox()).height-metrics.layout.height)<1);
   }
   // Exercise native scroll, then check that the composer remains reachable above the dock.
   await page.locator('#chat-messages').evaluate(e=>{e.scrollTop=e.scrollHeight;});
   assert.ok(await page.locator('#chat-messages').evaluate(e=>e.scrollTop>1000));
   await page.locator('#chat-input').scrollIntoViewIfNeeded();
   await page.locator('#chat-input').fill('仍可输入后续任务');
   const reach=await page.evaluate(()=>{
    const input=document.querySelector('#chat-input').getBoundingClientRect(),dock=document.querySelector('.bottom-dock').getBoundingClientRect();
    return {top:input.top,bottom:input.bottom,dockTop:dock.top,hit:document.elementFromPoint(input.left+input.width/2,input.top+input.height/2)?.id};
   });
   assert.ok(reach.top>=0&&reach.bottom<=reach.dockTop,JSON.stringify(reach));
   assert.equal(reach.hit,'chat-input');
   assert.equal(await page.locator('#chat-input').inputValue(),'仍可输入后续任务');
   const screenshot=`chat-${viewport.width}x${viewport.height}.png`;await page.screenshot({path:path.join(output,screenshot)});
   results.push({viewport,metrics,reach,screenshot,status:'PASS'});console.log(JSON.stringify({viewport,status:'PASS'}));
   await page.close();
  }
 }finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));}
})().catch(error=>{console.error(error);process.exitCode=1;});
