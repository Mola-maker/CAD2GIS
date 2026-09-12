// Browser regression for the bundled Framework7 popup lifecycle and reduced motion.
// All HTTP requests are intercepted locally; no real CAD service is contacted.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { chromium } = require('playwright');
const repo = path.resolve(__dirname, '../..');
const frontend = path.join(repo,'src/cad2gis/desktop');
const snapshot = Object.fromEntries(['app.js','style.css','index.html'].map(f=>[f,fs.readFileSync(path.join(frontend,f))]));
const profile = { base_url:'https://example.invalid/v1', model:'local-review-fixture', capability:'json_object', token_field:'max_tokens', timeout_s:90, max_tokens:4096, max_requests:32, has_key:false, configured:false, issue:'review fixture' };
const api = {
  status:{ remote:false, roots:['/read-only-fixture'], provider_configured:{deepseek:false,'new-api':false}, semantic_extraction:{available:true} },
  'ai/config':{profiles:{deepseek:profile,'new-api':profile},active:'deepseek',editable:true,storage:'review fixture'},
  files:{path:'/read-only-fixture',parent:null,page:1,total:0,has_more:false,entries:[]},
  conversations:{items:[],has_more:false}, jobs:{jobs:[]}
};
const mime = {'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.woff2':'font/woff2','.svg':'image/svg+xml'};
const results = [];
(async () => {
  console.log(JSON.stringify({frontend,servedSnapshotHashes:Object.fromEntries(Object.entries(snapshot).map(([f,data])=>[f,crypto.createHash('sha256').update(data).digest('hex')]))}));
  const browser = await chromium.launch({headless:true});
  try {
    for (const mode of ['no-preference','reduce','switch-opening','switch-closing']) {
      for (const name of ['project','help','tools']) {
        const page = await browser.newPage({reducedMotion:mode==='reduce'?'reduce':'no-preference',viewport:{width:1440,height:1000}});
        const errors = [];
        page.on('pageerror', e=>errors.push(e.message));
        await page.route('**/*', async route=>{
          const url=new URL(route.request().url());
          if(url.hostname!=='cad2gis-review.invalid') return route.abort();
          if(url.pathname.startsWith('/_app/api/')) {
            const body=api[url.pathname.slice('/_app/api/'.length)];
            return route.fulfill({status:body?200:404,contentType:'application/json',body:JSON.stringify(body||{detail:'read-only review fixture'})});
          }
          let filename;
          if(url.pathname==='/_app/') return route.fulfill({contentType:'text/html',body:snapshot['index.html']});
          else if(url.pathname.startsWith('/_app/assets/')) {
            const asset=url.pathname.slice('/_app/assets/'.length);
            if(snapshot[asset]) return route.fulfill({contentType:mime[path.extname(asset)],body:snapshot[asset]});
            filename=path.join(repo,'src/cad2gis/desktop',asset);
          }
          if(!filename||!fs.existsSync(filename)) return route.fulfill({status:404,body:'not provided by review fixture'});
          return route.fulfill({contentType:mime[path.extname(filename)]||'application/octet-stream',body:fs.readFileSync(filename)});
        });
        await page.goto('http://cad2gis-review.invalid/_app/');
        await page.waitForFunction(()=>typeof appStatus!=='undefined'&&appStatus!==null&&!document.querySelector('#workspace').hidden);
        await page.evaluate(name=>{
          window.reviewPopup={project:projectPopup,help:helpPopup,tools:toolsPopup}[name];
          window.reviewEvents=[];
          for(const event of ['open','opened','close','closed']) reviewPopup.on(event,()=>reviewEvents.push(event));
          reviewPopup.open();
        },name);
        if(mode==='switch-opening') {
          await page.waitForTimeout(40);
          await page.emulateMedia({reducedMotion:'reduce'});
        }
        await page.waitForFunction(()=>reviewEvents.includes('opened'),null,{timeout:2500}).catch(()=>{});
        const opened=await page.evaluate(()=>({events:[...reviewEvents],opened:reviewPopup.opened,display:getComputedStyle(reviewPopup.el).display,classes:reviewPopup.el.className}));
        await page.locator('#'+name+'-popup .popup-close').click();
        if(mode==='switch-closing') {
          await page.waitForTimeout(25);
          await page.emulateMedia({reducedMotion:'reduce'});
        }
        await page.waitForFunction(()=>reviewEvents.includes('closed'),null,{timeout:2500}).catch(()=>{});
        const closed=await page.evaluate(()=>({events:[...reviewEvents],opened:reviewPopup.opened,display:getComputedStyle(reviewPopup.el).display,classes:reviewPopup.el.className,transitionListeners:reviewPopup.el.dom7Listeners?.transitionend?.length??null,animationListeners:reviewPopup.el.dom7Listeners?.animationend?.length??null}));
        const record={mode,popup:name,opened,closed,errors};
        results.push(record);console.log(JSON.stringify(record));
        await page.close();
      }
    }
  } finally { await browser.close(); }
  const passed=results.length===12&&results.every(r=>r.closed.display==='none'&&!r.closed.opened&&!r.closed.classes.includes('modal-out')&&r.closed.events.join(',')==='open,opened,close,closed'&&!r.errors.length&&!r.closed.transitionListeners&&!r.closed.animationListeners);
  console.log(JSON.stringify({status:passed?'PASS':'FAIL',scenarios:results.length}));
  if(!passed)process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1;});
