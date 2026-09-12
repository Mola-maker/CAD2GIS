// Real desktop, review document and OpenLayers; synthetic local API geometry.
// Covers clipping and view lifecycle, not DWG conversion or geographic accuracy.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const repo=path.resolve(__dirname,'../..'),desktop=path.join(repo,'src/cad2gis/desktop'),review=path.join(repo,'src/cad2gis/webdemo');
const output=path.resolve(process.argv[2]||path.join(repo,'tmp/desktop-map-layout'));fs.mkdirSync(output,{recursive:true});
const results={scope:'Real application assets and OpenLayers; synthetic rectangle and endpoint API fixtures',checks:[],errors:[]};
const profile={base_url:'https://example.invalid/v1',model:'fixture',has_key:false,configured:false};
const local={type:'FeatureCollection',features:[{type:'Feature',properties:{display_label:'LAYOUT FIXTURE'},geometry:{type:'LineString',coordinates:[[0,0],[1000,0],[1000,400],[0,400],[0,0]]}},...[[-30,-20],[1030,420]].map(coordinates=>({type:'Feature',properties:{},geometry:{type:'Point',coordinates}}))]};
const geographic=JSON.parse(JSON.stringify(local));for(const f of geographic.features){const transform=c=>[124+c[0]/100000,1+c[1]/100000];f.geometry.coordinates=f.geometry.type==='Point'?transform(f.geometry.coordinates):f.geometry.coordinates.map(transform);}
const run={run_dir:'/fixture/run',run_status:'CONDITIONAL',source:{path:'/fixture/source.dwg'},delivery_counts:{CABLE:3},svg_candidates:{report_url:null},crs:{source_crs:'CAD:LOCAL',target_crs:'EPSG:4326'}};
const mime={'.html':'text/html','.css':'text/css','.js':'text/javascript','.png':'image/png','.woff2':'font/woff2','.svg':'image/svg+xml'};
const captureMaps=()=>{let library;Object.defineProperty(window,'ol',{configurable:true,get(){return library;},set(value){library=value;value.Map=new Proxy(value.Map,{construct(target,args,newTarget){const map=Reflect.construct(target,args,newTarget);(window.__layoutMaps ||= []).push(map);return map;}});}});};
async function readMaps(frame){return frame.evaluate(()=>window.__layoutMaps.map(map=>{
 const target=map.getTargetElement(),r=target.getBoundingClientRect(),size=map.getSize();
 if(!r.width||!r.height)return {id:target.id,visible:false};
 const view=map.getView(),extent=view.calculateExtent(size);let count=0,inside=0;
 for(const layer of map.getLayers().getArray())for(const f of layer.getSource()?.getFeatures?.()||[]){count++;if(ol.extent.containsExtent(extent,f.getGeometry().getExtent()))inside++;}
 return {id:target.id,visible:true,size,width:r.width,height:r.height,top:r.top,bottom:r.bottom,left:r.left,right:r.right,count,inside,resolution:view.getResolution(),center:view.getCenter(),canvas:[...target.querySelectorAll('canvas')].map(c=>({width:c.width,height:c.height}))};
}));}
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CAD2GIS_BROWSER_EXECUTABLE?{executablePath:process.env.CAD2GIS_BROWSER_EXECUTABLE}:{channel:process.env.CAD2GIS_BROWSER_CHANNEL||'msedge'})});
 const context=await browser.newContext({viewport:{width:711,height:898},reducedMotion:'reduce'});await context.addInitScript(captureMaps);
 let layerGate=null;
 await context.route('**/*',async route=>{
  const u=new URL(route.request().url()),json=body=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
  if(u.hostname!=='map-layout.invalid')return route.abort();
  if(u.pathname==='/_app/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(desktop,'index.html'))});
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(review,'review.html'))});
  let file;if(u.pathname.startsWith('/_app/assets/'))file=path.join(desktop,u.pathname.slice(13));
  if(u.pathname.startsWith('/assets/')){const relative=u.pathname.slice(8);file=[path.join(review,relative),path.join(review,'assets',relative)].find(fs.existsSync);}
  if(file&&fs.existsSync(file))return route.fulfill({contentType:mime[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
  const key=u.pathname.replace('/_app/api/','');
  if(key==='status')return json({remote:false,roots:['/fixture'],provider_configured:{deepseek:false,'new-api':false},semantic_extraction:{available:true}});
  if(key==='ai/config')return json({profiles:{deepseek:profile,'new-api':profile},active:'deepseek',editable:true,storage:'UI fixture'});
  if(key==='files')return json({path:'/fixture',parent:null,page:1,total:0,has_more:false,entries:[]});
  if(key==='jobs')return json({jobs:[]});if(key==='conversations')return json({items:[],has_more:false});
  if(key==='review')return json({url:'/?review=layout-review',review_id:'layout-review',run_dir:'/fixture/run',districts:{scopes:[]}});
  if(u.pathname==='/api/run')return json(run);
  if(u.pathname==='/api/layers'){if(layerGate)await layerGate;return json({layers:[{name:'CABLE',feature_count:3}]});}
  if(u.pathname.endsWith('/local-geojson'))return json(local);if(u.pathname.endsWith('/geojson'))return json(geographic);
  if(u.pathname==='/api/review/features')return json({type:'FeatureCollection',features:[]});
  if(u.pathname==='/api/registration')return json({controls:[]});
  return json({});
 });
 const page=await context.newPage();page.on('pageerror',e=>results.errors.push(e.message));
 const pass=(name,data={})=>{results.checks.push({name,status:'PASS',...data});console.log(JSON.stringify({name,status:'PASS'}));};
 const settle=()=>page.waitForTimeout(350);
 const frame=()=>page.locator('#osm-frame').elementHandle().then(h=>h.contentFrame());
 async function visible(name,{fit=true}={}){
  await settle();const maps=await readMaps(await frame());
  const outer=await page.evaluate(()=>{const f=document.querySelector('#osm-frame').getBoundingClientRect(),d=document.querySelector('.bottom-dock').getBoundingClientRect(),m=document.querySelector('#main-workspace');return {frame:{x:f.x,y:f.y,width:f.width,height:f.height,bottom:f.bottom,innerWidth:document.querySelector('#osm-frame').contentWindow.innerWidth},dockTop:d.top,width:innerWidth,height:innerHeight,scrollHeight:m.scrollHeight,clientHeight:m.clientHeight,inputWidth:document.querySelector('#existing-run').getBoundingClientRect().width};});
  assert.ok(outer.frame.y>=0&&outer.frame.bottom<=outer.dockTop-5,JSON.stringify(outer));assert.ok(outer.scrollHeight<=outer.clientHeight+1,JSON.stringify(outer));
  const visibleMaps=maps.filter(m=>m.visible);assert.equal(visibleMaps.length,outer.frame.innerWidth<=900?1:2);
  for(const m of visibleMaps){
   assert.ok(m.height>=120&&m.top>=0&&m.bottom<=outer.frame.height+1,JSON.stringify(m));assert.ok(m.right<=outer.frame.width+1,JSON.stringify(m));
   assert.ok(Math.abs(m.size[0]-m.width)<=1&&Math.abs(m.size[1]-m.height)<=1,JSON.stringify(m));
   assert.ok(m.canvas.some(c=>Math.abs(c.width-m.size[0])<=1&&Math.abs(c.height-m.size[1])<=1),JSON.stringify(m));
   if(fit)assert.equal(m.inside,m.count,JSON.stringify(m));assert.equal(m.count,3);
  }
  pass(name,{outer,maps});return maps;
 }
 try{
  await page.goto('https://map-layout.invalid/_app/');await page.waitForFunction(()=>appStatus&&!document.querySelector('#workspace').hidden);
  await page.locator('.bottom-dock [data-page="osm"]').click();await page.locator('#existing-run').fill('/fixture/run');await page.locator('#open-review').click();await page.waitForFunction(()=>reviewReady);
  for(const viewport of [{width:711,height:898},{width:720,height:900},{width:956,height:900},{width:957,height:900},{width:1280,height:800},{width:1920,height:1080}]){
   await page.setViewportSize(viewport);await visible(`default_${viewport.width}`);assert.ok(await page.locator('#existing-run').evaluate(e=>e.clientWidth)>120);
   if(viewport.width<=900){await page.frameLocator('#osm-frame').locator('[data-map-view="gis"]').click();await visible(`GIS_${viewport.width}`);await page.frameLocator('#osm-frame').locator('[data-map-view="cad"]').click();}
   await page.locator('#review-focus').click();await visible(`expanded_${viewport.width}`);await page.locator('#review-focus').click();await visible(`collapsed_${viewport.width}`);
  }
  await page.setViewportSize({width:711,height:898});await settle();await page.frameLocator('#osm-frame').locator('[data-map-view="gis"]').click();await settle();
  await page.frameLocator('#osm-frame').locator('#map .ol-zoom-in').click();await page.waitForTimeout(500);
  let before=(await readMaps(await frame())).find(m=>m.id==='map');assert.ok(before.inside<before.count);
  const box=await page.frameLocator('#osm-frame').locator('#map').boundingBox(),previousCenter=before.center;
  await (await frame()).evaluate(()=>{window.__dragEvents=[];const map=window.__layoutMaps.find(m=>m.getTarget()==='map');map.on(['pointerdown','pointerdrag','movestart','moveend'],e=>window.__dragEvents.push({type:e.type,center:map.getView().getCenter(),active:document.activeElement?.outerHTML.slice(0,160)}));});
  await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.down();await page.waitForTimeout(80);
  for(let step=1;step<=8;step++){await page.mouse.move(box.x+box.width/2+step*8,box.y+box.height/2+step*3);await page.waitForTimeout(35);}
  await page.mouse.up();await page.waitForTimeout(600);
  before=(await readMaps(await frame())).find(m=>m.id==='map');results.drag={box,events:await (await frame()).evaluate(()=>window.__dragEvents),previousCenter,after:before.center};assert.notDeepEqual(before.center,previousCenter);pass('user_drag_changes_view');
  await page.setViewportSize({width:1280,height:800});await visible('user_pan_zoom_resize_preserved',{fit:false});
  let after=(await readMaps(await frame())).find(m=>m.id==='map');assert.ok(Math.abs(after.resolution-before.resolution)<1e-8);assert.deepEqual(after.center,before.center);
  await page.locator('.bottom-dock [data-page="chat"]').click();await settle();await page.locator('.bottom-dock [data-page="osm"]').click();await visible('user_pan_zoom_tab_roundtrip_preserved',{fit:false});after=(await readMaps(await frame())).find(m=>m.id==='map');assert.ok(Math.abs(after.resolution-before.resolution)<1e-8);assert.deepEqual(after.center,before.center);
  await page.frameLocator('#osm-frame').locator('#fit-both').click();await visible('show_all_restores_full_extents');await page.setViewportSize({width:711,height:898});await visible('overview_refits_after_resize');
  await page.frameLocator('#osm-frame').locator('#toggle-layers').click();assert.ok(await page.frameLocator('#osm-frame').locator('#review-layers').isVisible());await page.frameLocator('#osm-frame').locator('#close-layers').click();await visible('layers_drawer_returns_to_map');
  assert.equal(await (await frame()).evaluate(()=>document.activeElement.id),'toggle-layers');
  await page.frameLocator('#osm-frame').locator('button[data-map-view="gis"]').click();await page.frameLocator('#osm-frame').locator('#toggle-manual-gcp').click();await page.frameLocator('#osm-frame').locator('#review-guide>summary').click();await page.frameLocator('#osm-frame').locator('#start-guided-correction').click();await settle();
  const guided=await (await frame()).evaluate(()=>{const el=document.querySelector('#local-map'),r=el.getBoundingClientRect();return {map:document.body.dataset.mapView,drawer:document.body.classList.contains('layers-open'),focused:document.activeElement.id,hit:el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))};});
  assert.deepEqual(guided,{map:'cad',drawer:false,focused:'local-map',hit:true});await visible('guided_correction_from_GIS_reveals_CAD');await page.frameLocator('#osm-frame').locator('#toggle-manual-gcp').click();
  let release;layerGate=new Promise(resolve=>release=resolve);await page.reload();await page.waitForFunction(()=>appStatus&&!document.querySelector('#workspace').hidden);await page.locator('.bottom-dock [data-page="osm"]').click();await page.locator('#existing-run').fill('/fixture/run');await page.locator('#open-review').click();await page.frameLocator('#osm-frame').locator('#local-map').waitFor();await page.locator('.bottom-dock [data-page="chat"]').click();release();layerGate=null;await page.waitForFunction(()=>reviewReady);await page.locator('.bottom-dock [data-page="osm"]').click();await visible('initialized_while_hidden_refits_on_return');
  await page.screenshot({path:path.join(output,'narrow-map-complete.png')});assert.deepEqual(results.errors,[]);
 }catch(error){results.failure=error.message;results.dom=await page.locator('body').innerText();results.frame_dom=await (await frame()).locator('body').innerText();throw error;}finally{fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});


