/* Real, source-bound QGZ feature layers over OpenStreetMap. No image map overlays. */
(() => {
 'use strict';
 const $=id=>document.getElementById(id), stage=$('map-stage'), drill=$('map-drill');
 const reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;
 const state={index:[],project:null,data:null,revision:0,layers:new Map(),labels:[],labelsOn:true,osmOnline:true,fitBounds:null};
 const pending=new Map();let featureTrigger=null,offlineGroup=null,labelFrame=0;
 function status(kind,text){$('map-oc').className='oc-badge s-'+kind;$('map-status-text').textContent=text;}
 function note(text){$('map-note').textContent=text;$('map-note').hidden=!text;}
 function loadData(key){
  if(window.__MAPDATA?.[key])return Promise.resolve(window.__MAPDATA[key]);if(pending.has(key))return pending.get(key);
  const promise=new Promise((resolve,reject)=>{
   const script=document.createElement('script');const timer=setTimeout(()=>finish(new Error('地图数据加载超时')),15000);
   function finish(error){clearTimeout(timer);script.remove();if(error)reject(error);else resolve(window.__MAPDATA[key]);}
   script.src='assets/maps/'+encodeURIComponent(key)+'.js';script.onload=()=>finish(window.__MAPDATA?.[key]?null:new Error('地图数据不完整'));script.onerror=()=>finish(new Error('无法读取 QGZ 图层数据，请重试'));document.head.appendChild(script);
  }).finally(()=>pending.delete(key));pending.set(key,promise);return promise;
 }
 if(!window.L){status('error','地图组件未加载');$('map-error-text').textContent='请检查本地 Leaflet 资源是否完整。';$('map-error').hidden=false;return;}
 const map=L.map('leaflet-map',{zoomControl:false,attributionControl:true,minZoom:2,maxZoom:22,zoomSnap:.25,zoomDelta:.5,zoomAnimation:!reduced,fadeAnimation:!reduced,markerZoomAnimation:!reduced,inertia:!reduced});
 map.createPane('qgz-offline');map.getPane('qgz-offline').style.zIndex='350';
 L.control.scale({imperial:false,position:'bottomright'}).addTo(map);
 const osmTiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
  maxZoom:22,maxNativeZoom:19,keepBuffer:1,updateWhenIdle:true,
  attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
  errorTileUrl:'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
 }).addTo(map);
 window.__CAD2GIS_MAP={map,state,osmTiles};
 function liveTiles(){return Array.from(document.querySelectorAll('#leaflet-map .leaflet-tile')).filter(img=>img.complete&&img.naturalWidth===256&&img.src.startsWith('https://tile.openstreetmap.org/'));}
 function enableOffline(){if(offlineGroup&&!map.hasLayer(offlineGroup)){offlineGroup.addTo(map);const cb=document.querySelector('[data-layer="OSM 离线路网"]');if(cb)cb.checked=true;}}
 osmTiles.on('tileerror',event=>{if(!event.tile.isConnected||!state.osmOnline)return;enableOffline();note('OSM 在线底图暂未连通，已保留 QGZ 真实要素与内置离线路网。可点击「刷新 OSM 底图」重试。');});
 osmTiles.on('load',()=>{if(liveTiles().length)note('');});
 function latLng(c){return L.CRS.EPSG3857.unproject(L.point(c[0],c[1]));}
 function bounds(b){return L.latLngBounds(latLng([b[0],b[1]]),latLng([b[2],b[3]]));}
 function closeFeature(){drill.hidden=true;if(featureTrigger?.isConnected)featureTrigger.focus();else map.getContainer().focus();}
 function featureDetails(f,name,trigger){
  featureTrigger=trigger||null;drill.replaceChildren();
  const close=document.createElement('button');close.className='md-close';close.textContent='×';close.setAttribute('aria-label','关闭要素详情');close.onclick=closeFeature;
  const title=document.createElement('h2');title.id='map-feature-title';title.className='md-title';title.textContent=name+' · '+(f.label||'要素属性');
  const source=document.createElement('p');source.className='md-mode';source.textContent='真实 QGZ 要素 · FID '+f.source_fid;
  const rows=document.createElement('dl');rows.className='md-rows';
  Object.entries(f.attrs||{}).forEach(([k,v])=>{const key=document.createElement('dt'),value=document.createElement('dd');key.textContent=k;value.textContent=typeof v==='object'?JSON.stringify(v):String(v);rows.append(key,value);});
  drill.append(close,title,source,rows);drill.hidden=false;close.focus({preventScroll:true});
 }
 function vectorStyle(f){const s=f.style||{};return{color:s.color||'#2251ff',weight:Math.max(.6,s.width||1.2),opacity:s.strokeOpacity??s.opacity??1,fillColor:s.fill||s.color||'#2251ff',fillOpacity:s.fillOpacity??.12,dashArray:({dash:'6 4',dot:'2 3','dash dot':'6 3 2 3','dash dot dot':'6 3 2 3 2 3'})[s.dash]||null};}
 function pointLayer(f,ll,pane){
  const s=f.style||{},size=Math.max(4,Math.min(64,s.size||8));
  if(f.symbol||s.shape){
   const wrapper=document.createElement('span');wrapper.className='qgis-symbol';wrapper.style.opacity=String(s.opacity??1);wrapper.style.transform=`rotate(${Number(s.rotation)||0}deg)`;
   if(f.symbol){const img=document.createElement('img');img.src=f.symbol;img.alt='';img.draggable=false;wrapper.appendChild(img);}
   else {const shape=document.createElement('span');shape.className='qgis-marker-shape shape-'+String(s.shape).replace(/[^a-z0-9_-]/gi,'');shape.style.backgroundColor=s.fill||s.color||'#2251ff';wrapper.appendChild(shape);}
   return L.marker(ll,{pane,keyboard:true,title:f.label||'QGZ 要素',icon:L.divIcon({html:wrapper,className:'qgis-marker',iconSize:[size,size],iconAnchor:[size/2,size/2]})});
  }
  return L.circleMarker(ll,{...vectorStyle(f),pane,radius:size/2,fillOpacity:s.fillOpacity??1});
 }
 function makeLayer(name,features,pane,interactive){
  return L.geoJSON(features.filter(f=>f.style?.visible!==false).map(f=>({type:'Feature',geometry:f.geom,properties:{source:f}})),{
   pane,coordsToLatLng:latLng,style:feature=>({...vectorStyle(feature.properties.source),pane,interactive}),
   pointToLayer:(feature,ll)=>pointLayer(feature.properties.source,ll,pane),
   onEachFeature:(feature,layer)=>{
    const f=feature.properties.source;
    if(interactive){
     layer.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);featureDetails(f,name,layer.getElement?.());});
     layer.on('add',()=>{const el=layer.getElement?.();if(el){el.classList.add('qgz-feature');el.setAttribute('data-layer-name',name);el.setAttribute('role','button');el.setAttribute('tabindex','0');el.setAttribute('aria-label',name+' · '+(f.label||'查看属性'));if(!el.dataset.keyHandler){el.dataset.keyHandler='true';el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();featureDetails(f,name,el);}});}}});
     if(f.label){const text=document.createElement('span');text.textContent=f.label;layer.bindTooltip(text,{permanent:true,direction:'right',offset:[5,0],className:'qgz-label',opacity:1,interactive:false});state.labels.push(layer);}
    }
   }
  });
 }
 function row(name,count,checked,onChange){
  const label=document.createElement('label');label.className='lyr-row';const cb=document.createElement('input');cb.type='checkbox';cb.checked=checked;cb.dataset.layer=name;cb.setAttribute('aria-label',name+' 图层');
  const sw=document.createElement('span');sw.className='lyr-sw';sw.style.backgroundColor=state.data?.layers?.[name]?.style?.color||'#90a49a';
  const title=document.createElement('span');title.className='lyr-name';title.textContent=name;
  const number=document.createElement('span');number.className='lyr-n';number.textContent=count;
  cb.onchange=()=>{onChange(cb.checked);scheduleLabels();};label.append(cb,sw,title,number);$('map-layers').appendChild(label);
 }
 function scheduleLabels(){cancelAnimationFrame(labelFrame);labelFrame=requestAnimationFrame(()=>{
  const used=[],box=map.getContainer().getBoundingClientRect();
  for(const layer of state.labels){const el=layer.getTooltip()?.getElement();if(!el)continue;el.style.display=state.labelsOn?'':'none';if(!state.labelsOn)continue;
   const r=el.getBoundingClientRect();if(r.right<box.left||r.left>box.right||r.bottom<box.top||r.top>box.bottom){el.style.display='none';continue;}
   const collision=used.some(a=>r.left<a.right+3&&r.right>a.left-3&&r.top<a.bottom+2&&r.bottom>a.top-2);if(collision)el.style.display='none';else used.push(r);
  }
 });}
 map.on('moveend zoomend resize',scheduleLabels);
 function render(){
  const data=state.data;state.layers.forEach(l=>map.removeLayer(l));state.layers.clear();state.labels=[];if(offlineGroup)map.removeLayer(offlineGroup);drill.hidden=true;$('map-layers').replaceChildren();
  const offline=[];Object.entries(data.osm||{}).reverse().forEach(([name,fs])=>offline.push(makeLayer('OSM '+name,fs,'qgz-offline',false)));offlineGroup=L.featureGroup(offline);
  row('OpenStreetMap','在线',state.osmOnline,on=>{state.osmOnline=on;if(on){osmTiles.addTo(map);note('');}else{map.removeLayer(osmTiles);enableOffline();note('当前显示 QGZ 内置离线 OSM 图层。');}});
  const offlineCount=Object.values(data.osm||{}).reduce((sum,fs)=>sum+fs.length,0);
  row('OSM 离线路网',offlineCount,false,on=>on?offlineGroup.addTo(map):map.removeLayer(offlineGroup));
  // QGIS layer_order is top-to-bottom; Leaflet panes are assigned bottom-to-top.
  const ordered=(data.layer_order||Object.keys(data.layers)).filter(name=>data.layers[name]).reverse().map(name=>[name,data.layers[name]]);
  ordered.forEach(([name,l],i)=>{
   const pane='qgz-'+name.replace(/[^a-z0-9]/gi,'_');if(!map.getPane(pane))map.createPane(pane);map.getPane(pane).style.zIndex=String(410+i);
   const group=makeLayer(name,l.features,pane,true);state.layers.set(name,group);if(l.visible!==false)group.addTo(map);
  });
  Object.entries(data.layers).forEach(([name,l])=>{const group=state.layers.get(name);row(name,l.features.length,map.hasLayer(group),on=>on?group.addTo(map):map.removeLayer(group));});
  state.fitBounds=bounds(data.map_extent||data.bounds);map.stop();map.fitBounds(state.fitBounds,{padding:[24,24],animate:false,maxZoom:19});
  if(!state.osmOnline)enableOffline();
  $('map-view-description').textContent='QGZ 真实图层叠加到 OpenStreetMap。点击符号、线或区域查看原始属性；缩放、拖动及图层开关实时作用于要素。';
  $('map-legend-note').textContent='几何、属性、图层颜色及 SVG 符号来自所选 QGZ；网页标注避让随缩放更新。OSM 在线底图可与项目内置离线路网分别开关。';
  status('success','QGZ 图层 · '+data.feature_total+' 个真实要素 · OpenStreetMap');scheduleLabels();
 }
 function link(id,href){const el=$(id);el.hidden=!href;if(href)el.href=href;else el.removeAttribute('href');}
 async function loadProject(id){
  const project=state.index.find(p=>p.id===id);if(!project)return;const revision=++state.revision;state.project=project;state.data=null;state.labels=[];map.stop();
  state.layers.forEach(layer=>map.removeLayer(layer));state.layers.clear();if(offlineGroup)map.removeLayer(offlineGroup);drill.hidden=true;$('map-error').hidden=true;note('');
  $('map-project').value=id;$('map-title').textContent=project.title;link('map-download',project.qgz);link('map-source',project.source_url);link('map-image-link',project.preview);
  $('map-provenance-text').textContent='GitHub commit: '+project.source_commit+'\nQGZ SHA-256: '+project.source_sha256+'\nQGIS / PROJ · EPSG:3857';
  const url=new URL(location.href);url.searchParams.set('p',id);url.searchParams.delete('view');history.replaceState(null,'',url);status('working','正在加载 QGZ 图层 · '+project.title);
  try{const data=await loadData(id);if(revision!==state.revision)return;if(data.srs!==3857)throw new Error('图层坐标系未统一到 EPSG:3857');state.data=data;render();}
  catch(error){if(revision!==state.revision)return;status('error','图层加载失败');$('map-error-text').textContent=error.message;$('map-error').hidden=false;}
 }
 $('map-project').onchange=e=>loadProject(e.target.value);
 $('mz-in').onclick=()=>map.zoomIn();$('mz-out').onclick=()=>map.zoomOut();$('mz-fit').onclick=()=>{if(state.fitBounds){map.stop();map.fitBounds(state.fitBounds,{padding:[24,24],animate:false,maxZoom:19});}};
 $('map-labels-toggle').onclick=()=>{state.labelsOn=!state.labelsOn;$('map-labels-toggle').setAttribute('aria-pressed',String(state.labelsOn));scheduleLabels();};
 $('map-basemap-retry').onclick=()=>{state.osmOnline=true;osmTiles.addTo(map);const cb=document.querySelector('[data-layer="OpenStreetMap"]');if(cb)cb.checked=true;note('正在重新连接 OpenStreetMap…');map.removeLayer(osmTiles);osmTiles.addTo(map); /* Reattach rounds native tile zoom; redraw() uses fractional map zoom in Leaflet 1.9. */};
 $('map-retry').onclick=()=>state.project?loadProject(state.project.id):start();
 document.addEventListener('keydown',e=>{if(e.key==='Escape'){if(!drill.hidden)closeFeature();$('map-toc').classList.remove('open');$('toc-burger').setAttribute('aria-expanded','false');}});
 const burger=document.createElement('button');burger.id='toc-burger';burger.textContent='图层 / 来源';burger.setAttribute('aria-expanded','false');burger.onclick=()=>{const on=$('map-toc').classList.toggle('open');burger.setAttribute('aria-expanded',String(on));};stage.appendChild(burger);
 new ResizeObserver(()=>map.invalidateSize({animate:false})).observe(stage);
 async function start(){try{const index=await loadData('index');state.index=index.projects;$('map-project').replaceChildren(...state.index.map(p=>{const o=document.createElement('option');o.value=p.id;o.textContent=p.title;return o;}));const query=new URLSearchParams(location.search).get('p');await loadProject(state.index.some(p=>p.id===query)?query:state.index[0].id);}catch(error){status('error','地图目录加载失败');$('map-error-text').textContent=error.message;$('map-error').hidden=false;}}
 start();
})();
