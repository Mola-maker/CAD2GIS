"use strict";
const $=s=>document.querySelector(s),$$=s=>Array.from(document.querySelectorAll(s));
const ui=new Framework7({el:"#app",name:"molamaker",theme:"ios",routes:[]});
const popupMotionPreference=window.matchMedia("(prefers-reduced-motion: reduce)");
function createMotionPopup(selector){
  const popup=ui.popup.create({el:selector,closeByBackdropClick:true,closeOnEscape:true,animate:!popupMotionPreference.matches});
  let phase=null,returnFocus=null;
  const endListeners=new Map();
  function clearEnds(){
    for(const [name,handler] of endListeners)popup.$el.off(name,handler);
    endListeners.clear();
  }
  // Framework7 9.1.3 registers both end types but only removes the one that fires.
  // Keep this popup's paired callbacks together without removing unrelated listeners.
  for(const [method,name] of [["animationEnd","animationend"],["transitionEnd","transitionend"]]){
    popup.$el[method]=function(callback){
      if(!callback)return this;
      const handler=event=>{
        if(event.target!==popup.el)return;
        clearEnds();
        callback.call(popup.el,event);
      };
      const previous=endListeners.get(name);
      if(previous)this.off(name,previous);
      endListeners.set(name,handler);
      this.on(name,handler);
      return this;
    };
  }
  function settle(){
    if(!phase)return;
    const ending=phase;phase=null;clearEnds();
    popup.$el.addClass("not-animated");
    popup.$backdropEl?.addClass("not-animated");
    // These are the bundled Modal finalizers, including display, backdrop and events.
    if(ending==="opening")popup.onOpened();else popup.onClosed();
  }
  for(const method of ["open","close"]){
    const original=popup[method].bind(popup);
    popup[method]=function(animate,...args){
      settle();
      if(method==="open"&&!popup.opened)returnFocus=document.activeElement;
      const enabled=!popupMotionPreference.matches&&(animate??popup.params.animate);
      return original(enabled,...args);
    };
  }
  popup.el.setAttribute("role","dialog");
  popup.el.setAttribute("aria-modal","true");
  const heading=popup.el.querySelector(".popup-heading h2");
  if(heading){heading.id ||= popup.el.id+"-title";popup.el.setAttribute("aria-labelledby",heading.id);}
  popup.on("open",()=>{phase="opening";});
  popup.on("close",()=>{phase="closing";});
  popup.on("opened",()=>{
    phase=null;clearEnds();
    if(!popup.el.contains(document.activeElement))popup.el.querySelector(".popup-close")?.focus({preventScroll:true});
  });
  popup.on("closed",()=>{
    phase=null;clearEnds();
    const active=document.activeElement;
    if(returnFocus?.isConnected&&(popup.el.contains(active)||active===document.body))returnFocus.focus({preventScroll:true});
    returnFocus=null;
  });
  function preferenceChanged(){
    popup.params.animate=!popupMotionPreference.matches;
    if(popupMotionPreference.matches)settle();
  }
  popupMotionPreference.addEventListener("change",preferenceChanged);
  popup.on("beforeDestroy",()=>{clearEnds();popupMotionPreference.removeEventListener("change",preferenceChanged);});
  popup.el.addEventListener("keydown",event=>{
    if(event.key!=="Tab"||!popup.opened)return;
    const focusable=Array.from(popup.el.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])')).filter(el=>el.getClientRects().length&&getComputedStyle(el).visibility!=="hidden");
    const first=focusable[0],last=focusable.at(-1);
    if(first&&event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(last&&!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  });
  return popup;
}
const projectPopup=createMotionPopup("#project-popup"),helpPopup=createMotionPopup("#help-popup"),toolsPopup=createMotionPopup("#tools-popup");
const statusNames={queued:"等待启动",running:"正在执行",cancelling:"正在停止",succeeded:"已完成",failed:"未完成",cancelled:"已停止"},actionNames={doctor:"运行环境检查",inspect:"图纸检查",bootstrap:"建立草稿",convert:"标准转换","auto-convert":"AI 建项与转换","semantic-extract":"语义提取"};
let messageRequest=0;
let appStatus=null,aiConfig=null,directory="",parentDirectory=null,filePage=1,chatPage=1,jobPage=1,records=[],selectedJob="",activeJob=null,timer=null,agentTimer=null,agentRun="",conversation="",messageBefore=0,reviewUrl="",reviewId="",databasePage=1,databaseTable="",databaseSchema=[];
const iconPaths={plus:"M12 5v14M5 12h14",close:"m6 6 12 12M6 18 18 6",arrow:"M5 12h14m-5-5 5 5-5 5","arrow-up":"M12 19V5m-5 5 5-5 5 5",file:"M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h6",folder:"M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z",sliders:"M4 5h16M4 12h16M4 19h16M8 3v4M16 10v4M10 17v4",chat:"M21 11a8 8 0 0 1-8 8H8l-5 3v-6a8 8 0 1 1 18-5zM8 9h8M8 13h5",map:"m3 5 6-3 6 3 6-3v17l-6 3-6-3-6 3zM9 2v17M15 5v17",database:"M20 6c0 2-3.6 4-8 4S4 8 4 6s3.6-4 8-4 8 2 8 4ZM4 6v12c0 2 3.6 4 8 4s8-2 8-4V6M4 12c0 2 3.6 4 8 4s8-2 8-4",shapes:"m6 2 5 8H1zM14 3h7v7h-7zM8 18a4 4 0 1 1-8 0 4 4 0 0 1 8 0ZM13 14h8v8h-8z",book:"M12 5C9 2 4 3 2 4v16c3-2 7-2 10 0 3-2 7-2 10 0V4c-2-1-7-2-10 1zM12 5v15",help:"M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3M12 17h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",power:"M12 2v10M6 5a9 9 0 1 0 12 0",check:"m5 12 4 4L19 6",play:"m8 4 12 8-12 8z",expand:"M8 3H3v5M16 3h5v5M21 16v5h-5M3 16v5h5",upload:"M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4",sparkles:"m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3zM20 2v4M18 4h4",connection:"M8 3v5M16 3v5M6 8h12v3a6 6 0 0 1-12 0zM12 17v5"};
function node(tag,text="",cls=""){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;}
// Motion is presentation only: requests, disabled state and task results stay authoritative.
const motion = (() => {
  const root = document.documentElement;
  const pending = new WeakMap(), tasks = new WeakMap(), pulses = new WeakMap(), busyTimers = new WeakMap(), observed = new WeakSet();
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const visible = new IntersectionObserver(entries => {
    for (const entry of entries) entry.target.dataset.motionVisible = String(entry.isIntersecting);
  });
  function attach(scope) {
    const buttons = scope.matches?.("button") ? [scope] : [];
    buttons.push(...(scope.querySelectorAll?.("button") || []));
    for (const button of buttons) {
      if (observed.has(button)) continue;
      observed.add(button);
      button.classList.add("motion-button");
      button.dataset.motionState = "idle";
      button.dataset.motionVisible = "false";
      button.dataset.motionGesture = button.matches("[data-page],[data-workflow],.rail-link") ? "navigate"
        : button.matches("[data-doc],[data-prompt],#inspect,#refresh-svg,#execute-sql") ? "inspect"
        : button.matches(".popup-close,#cancel,#agent-stop,#shutdown,#logout") ? "dismiss" : "greet";
      visible.observe(button);
    }
  }
  function action(event) {
    return event?.submitter || (event?.target instanceof Element ? event.target.closest("button") : null);
  }
  function disabled(button) { return !button || button.disabled || button.getAttribute("aria-disabled") === "true"; }
  function restore(button) {
    if (!button?.isConnected) return;
    button.dataset.motionState = pending.get(button) ? "working" : (tasks.get(button) || "idle");
  }
  function pulse(button, state = "press") {
    if (disabled(button)) return;
    clearTimeout(pulses.get(button));
    button.dataset.motionState = state;
    pulses.set(button, setTimeout(() => { pulses.delete(button); restore(button); }, state === "error" ? 2200 : 180));
  }
  function begin(button) {
    if (!button) return;
    // A fresh operation cannot inherit a completed result from an older job.
    if (tasks.get(button) !== "working") {
      tasks.delete(button);
      if (["success", "error", "cancelled"].includes(button.dataset.motionState)) button.dataset.motionState = "idle";
    }
    pending.set(button, (pending.get(button) || 0) + 1);
    if (!busyTimers.has(button)) busyTimers.set(button, setTimeout(() => {
      busyTimers.delete(button);
      if (pending.get(button)) { clearTimeout(pulses.get(button)); pulses.delete(button); button.dataset.motionState = "working"; }
    }, 120));
  }
  function finish(button, failed = false) {
    if (!button) return;
    pending.set(button, Math.max(0, (pending.get(button) || 0) - 1));
    if (!pending.get(button)) { clearTimeout(busyTimers.get(button)); busyTimers.delete(button); }
    if (failed) {
      tasks.delete(button);
      button.dataset.motionState = "error";
      clearTimeout(pulses.get(button));
      pulses.set(button, setTimeout(() => { pulses.delete(button); restore(button); }, 2200));
    } else if (!pulses.has(button)) restore(button); // Completing a click handler is not proof that a task succeeded.
  }
  function task(job) {
    const state = { queued:"working", running:"working", cancelling:"working", succeeded:"success", failed:"error", cancelled:"cancelled" }[job.status];
    const id = { doctor:"doctor", inspect:"inspect", bootstrap:"bootstrap", convert:"convert", "auto-convert":"convert", "semantic-extract":"semantic-extract", "AI 审阅":"send" }[job.action];
    const button = id && document.getElementById(id);
    if (!state || !button) return;
    clearTimeout(pulses.get(button));
    pulses.delete(button);
    tasks.set(button, state);
    button.dataset.motionState = state;
  }
  function enabled() {
    root.dataset.motionPaused = String(document.hidden || reduced.matches);
  }
  document.addEventListener("pointerdown", event => pulse(action(event)), true);
  document.addEventListener("click", event => { if (event.detail === 0) pulse(action(event)); }, true);
  document.addEventListener("visibilitychange", enabled);
  reduced.addEventListener("change", enabled);
  attach(document); enabled();
  new MutationObserver(records => {
    for (const record of records) {
      for (const removed of record.removedNodes) {
        if (!(removed instanceof Element)) continue;
        for (const button of [removed, ...removed.querySelectorAll("button")]) {
          if (button.matches("button") && !button.isConnected) { visible.unobserve(button); observed.delete(button); clearTimeout(pulses.get(button)); clearTimeout(busyTimers.get(button)); pulses.delete(button); busyTimers.delete(button); }
        }
      }
      for (const added of record.addedNodes) if (added instanceof Element) attach(added);
    }
  }).observe(document.body, { childList:true, subtree:true });
  return { action, begin, finish, task, reduced };
})();

// Companion feedback uses task state; a press is never a successful result.
class CompanionFeedback {
  constructor(render, schedule=(fn,ms)=>setTimeout(fn,ms), cancel=id=>clearTimeout(id), taskFeedback=()=>{}) {
    this.render=render;this.schedule=schedule;this.cancel=cancel;this.taskFeedback=taskFeedback;
    this.state="idle";this.label="一起看清每一个要素";this.timer=null;
  }
  set(state,label) {
    if(!["idle","working","success","error","cancelled"].includes(state))return;
    this.cancel(this.timer);this.timer=null;this.state=state;this.label=label;
    this.render(state,label);
  }
  press(disabled=false) {
    if(disabled)return;
    this.cancel(this.timer);this.render("press",this.label);
    this.timer=this.schedule(()=>this.render(this.state,this.label),180);
  }
  task(job) {
    const state={queued:"working",running:"working",cancelling:"working",succeeded:"success",failed:"error",cancelled:"cancelled"}[job.status];
    this.taskFeedback(job);
    if(state)this.set(state,(actionNames[job.action]||"任务")+" · "+(statusNames[job.status]||job.status));
  }
}
// End companion feedback model.
const companion=new CompanionFeedback((state,label)=>{
  for(const el of $$("[data-companion]"))el.dataset.state=state;
  for(const el of $$("[data-companion-label]"))if(el.textContent!==label)el.textContent=label;
},undefined,undefined,job=>motion.task(job));
const companionSprite=new Image();
companionSprite.onload=()=>{
  for(const portrait of $$(".companion-portrait")) {
    const sheet=companionSprite.cloneNode();sheet.className="companion-sheet";sheet.alt="";portrait.append(sheet);
  }
  document.documentElement.classList.add("companion-sprite-ready");
};
companionSprite.src="/_app/assets/assets/molamaker-actions-v1.png";
document.addEventListener("pointerdown",event=>{
  const button=event.target.closest("button");
  if(button)companion.press(button.disabled||button.getAttribute("aria-disabled")==="true");
},true);
document.addEventListener("click",event=>{
  const button=event.target.closest("button");
  if(button&&event.detail===0)companion.press(button.disabled||button.getAttribute("aria-disabled")==="true");
},true);
let jobSubmitting=false,jobFeedbackKey="",semanticArtifactKey="";
const liveJobStatuses=["queued","running","cancelling"];
function icon(name){const s=document.createElementNS("http://www.w3.org/2000/svg","svg");for(const [k,v] of Object.entries({viewBox:"0 0 24 24",fill:"none",stroke:"currentColor","stroke-width":"1.65","stroke-linecap":"round","stroke-linejoin":"round","aria-hidden":"true"}))s.setAttribute(k,v);s.classList.add("icon");const p=document.createElementNS(s.namespaceURI,"path");p.setAttribute("d",iconPaths[name]||iconPaths.file);s.append(p);return s;}
for(const placeholder of $$("[data-icon]"))placeholder.replaceWith(icon(placeholder.dataset.icon));
const dock=document.querySelector('.bottom-dock'),dockLens=document.createElement('span');
dockLens.className='dock-lens';dockLens.setAttribute('aria-hidden','true');dock.prepend(dockLens);
function positionDockLens(){const selected=dock.querySelector('button.active');if(!selected)return;dockLens.style.transform='translateX('+selected.offsetLeft+'px)';dockLens.style.width=selected.offsetWidth+'px';dockLens.style.top=selected.offsetTop+'px';dockLens.style.height=selected.offsetHeight+'px';}
new ResizeObserver(positionDockLens).observe(dock);requestAnimationFrame(positionDockLens);
const welcomeTemplate=$("#chat-welcome").cloneNode(true);
function message(text,error=false){const el=$("#message");el.textContent=text;el.className=error?"message error":"message";el.hidden=false;if(error)companion.set("error",text);clearTimeout(message.timer);message.timer=setTimeout(()=>el.hidden=true,8000);}
async function request(path,data){const r=await fetch("/_app/api/"+path,data===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});const body=await r.json().catch(()=>({}));if(!r.ok){if(r.status===401&&path!=="login")showLogin();throw new Error(body.detail||"服务请求失败 ("+r.status+")");}return body;}
function guarded(fn){return async event=>{
  const button=motion.action(event),wasDisabled=button?.disabled||button?.getAttribute("aria-disabled")==="true";let tracked=false;
  try{const result=fn(event);if(!wasDisabled&&result&&typeof result.then==="function"){tracked=true;motion.begin(button);}await result;}
  catch(error){motion.finish(button,true);tracked=false;message(error.message,true);}
  finally{if(tracked)motion.finish(button);}
};}
function on(id,event,fn){let pending=false;$(id).addEventListener(event,guarded(async e=>{if(event==="submit")e.preventDefault();if(pending||e.currentTarget.disabled)return;pending=true;try{return await fn(e);}finally{pending=false;}}));}
function showLogin(){clearTimeout(timer);clearTimeout(agentTimer);++agentPollRequest;agentRun="";$("#workspace").hidden=true;$("#login").hidden=false;$("#connection").textContent="尚未登录";projectPopup.close();helpPopup.close();toolsPopup.close();}
function showPage(page){
  if(!["config","chat","osm","svg","sqlite","delivery"].includes(page))return;
  toolsPopup.close();
  ui.tab.show("#tab-"+page,false);
  $("#main-workspace").scrollTop=0;
  for(const b of $$(".bottom-dock [data-page]")){
    const active=b.dataset.page===page;
    b.classList.toggle("active",active);
    if(active)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");
  }
  $("#open-tools").classList.toggle("active",!["chat","osm"].includes(page));
  $("#app").classList.toggle("review-mode",page==="osm");
  if(page!=="osm")setReviewFocus(false);
  positionDockLens();
  if(page==="osm"&&$("#osm-frame").contentWindow)$("#osm-frame").contentWindow.dispatchEvent(new Event("resize"));
  if(page==="svg"&&reviewId)loadSvg().catch(e=>message(e.message,true));
}
function setReviewFocus(expanded){
  $("#app").classList.toggle("review-expanded",expanded);
  $("#review-focus").setAttribute("aria-pressed",String(expanded));
  $("#review-focus").textContent=expanded?"收起工作区":"展开工作区";
  requestAnimationFrame(()=>{
    positionDockLens();
    $("#osm-frame").contentWindow?.dispatchEvent(new Event("resize"));
  });
}
function updateWorkflow(){
  const hasSource=Boolean($("#source").value.trim());
  $("#workflow-source").textContent=hasSource?"已选择 DWG":"选择 DWG";
  $("#workflow-state").textContent=activeJob?"正在执行："+(actionNames[activeJob.action]||activeJob.action):reviewUrl?"已打开结果 · 可进入地图核对":hasSource?"下一步：检查源图与转换设置":"下一步：导入 DWG";
  const current=reviewUrl?(mapReviewed?"preview":"gcp"):hasSource?"inspect":"import";
  for(const button of $$("[data-workflow]")){
    if(button.dataset.workflow===current)button.setAttribute("aria-current","step");
    else button.removeAttribute("aria-current");
  }
}
async function followWorkflow(step){
  if(step==="import"||step==="inspect"){
    projectPopup.open();
    if(step==="inspect")$("#inspect").focus();
    return;
  }
  if((!reviewUrl||reviewRun!==$("#existing-run").value.trim()) && !await openReview())return;
  if(["preview","export"].includes(step)&&(!mapReviewed||!reviewReady||reviewSelectionDirty)){
    showPage("osm");$("#confirm-map").focus();return;
  }
  if(step==="preview"){showPage("svg");return;}
  if(step==="export"){
    const selected=reviewId,view=reviewViewId,revision=reviewRevision;
    const run=await currentReviewRun();
    if(selected!==reviewId||view!==reviewViewId||revision!==reviewRevision||!mapReviewed||!reviewReady)return;
    showPage("delivery");await loadDelivery(run.current_run_dir||run.run_dir||reviewRun);return;
  }
  showPage("osm");
}

function join(base,name){return base.replace(/[\\/]$/,"")+(base.includes("\\")?"\\":"/")+name;}
function currentResultName(){return "run-"+new Date().toISOString().replace(/[:.]/g,"-");}
function syncSource(){const s=$("#source").value.trim();$("#selected-source").textContent=s?s.split(/[\\/]/).pop():"尚未选择图纸";$("#selected-source").title=s;updateWorkflow();}
async function browse(path,page=1){const r=await request("files?"+new URLSearchParams({path,page,page_size:30,search:$("#file-search").value}));directory=r.path;parentDirectory=r.parent;filePage=r.page;$("#directory").textContent=directory;$("#parent").disabled=!parentDirectory;$("#files").textContent="";
for(const entry of r.entries){const b=node("button","","file");b.type="button";b.append(icon(entry.directory?"folder":/\.(db|gpkg|sqlite3?)$/i.test(entry.name)?"database":"file"),node("span",entry.name));b.addEventListener("click",guarded(async()=>{if(entry.directory)return browse(entry.path);if(/\.(db|gpkg|sqlite3?)$/i.test(entry.name)){$("#database-path").value=entry.path;projectPopup.close();showPage("sqlite");return openDatabase();}if(!/\.dwg$/i.test(entry.name))return message("请选择 DWG 图纸，或进入项目 / 结果目录。");$("#source").value=entry.path;$("#project").value=join(directory,entry.name.replace(/\.[^.]+$/,"")+"-project");$("#run").value=join(directory,currentResultName());syncSource();for(const old of $$(".file.selected"))old.classList.remove("selected");b.classList.add("selected");message("已选择 "+entry.name);}));$("#files").append(b);}if(!r.entries.length)$("#files").append(node("p","目录为空，或没有匹配的文件。","empty-copy"));$("#file-count").textContent=r.total+" 项";$("#file-page").textContent=filePage;$("#file-prev").disabled=filePage===1;$("#file-next").disabled=!r.has_more;}
function updateProvider(){const auto=$("#mode").value==="auto-convert";$("#provider-field").hidden=!auto;const ready=appStatus&&appStatus.provider_configured[$("#provider").value];$("#provider-status").textContent=ready?"已使用软件保存的 AI 配置":"请先在底部「AI 配置」中保存服务并测试连接";}
function renderSemanticArtifacts(job) {
  const panel=$("#semantic-artifacts"),key=job?.id+":"+job?.status;
  panel.hidden=!(job?.action==="semantic-extract"&&job.status==="succeeded");
  if(panel.hidden||semanticArtifactKey===key)return;
  semanticArtifactKey=key;
  const summary=job.summary||{};
  $("#semantic-summary").textContent=`${summary.entity_count??"—"} 个源实体 · ${summary.candidate_count??"—"} 个候选关系。候选等待审阅，尚未提交分类。`;
  $("#semantic-output-path").textContent=job.run_dir;
  const links=$("#semantic-artifact-links");links.textContent="";
  for(const artifact of job.artifacts||[]) {
    const row=node("div","","artifact-row"),label=node("span",artifact.label);
    const download=node("a","下载 ↗");download.href="/_app/api/semantic/artifact?"+new URLSearchParams({run:job.run_dir,name:artifact.name});download.download="";
    row.append(label,download);
    if(artifact.kind==="database") {
      const browseButton=node("button","查看数据","text-button");browseButton.type="button";
      browseButton.addEventListener("click",guarded(async()=>{$("#database-path").value=artifact.path;projectPopup.close();showPage("sqlite");await openDatabase();}));
      row.append(browseButton);
    }
    links.append(row);
  }
}
function renderJobs(){
  const selected=records.find(j=>j.id===selectedJob)||records[0];
  activeJob=records.find(j=>liveJobStatuses.includes(j.status))||null;
  for(const id of ["convert","inspect","bootstrap","doctor","semantic-extract"])$("#"+id).disabled=Boolean(jobSubmitting||activeJob||agentRun);
  if(!appStatus?.semantic_extraction?.available)$("#semantic-extract").disabled=true;
  $("#cancel").hidden=!activeJob;$("#cancel").disabled=activeJob?.status==="cancelling";
  $("#cancel").textContent=activeJob?.status==="cancelling"?"正在停止…":"取消任务";
  $("#review-result").hidden=!selected||selected.status!=="succeeded"||!["convert","auto-convert"].includes(selected.action);
  if(selected){
    selectedJob=selected.id;$("#job-status").textContent=(actionNames[selected.action]||selected.action)+" · "+(statusNames[selected.status]||selected.status);
    const log=$("#log"),atEnd=log.scrollHeight-log.scrollTop-log.clientHeight<80;
    log.textContent=selected.log||"任务已排队，正在等待引擎。";if(atEnd)log.scrollTop=log.scrollHeight;
    const stageNames={queued:"等待启动",source:"1 / 3 · 读取 CAD 源事实",candidates:"2 / 3 · 整理语义候选",verify:"3 / 3 · 验证来源与产物",complete:"产物已验证",execute:"执行当前任务"};
    $("#job-stage").textContent=selected.status==="cancelled"?"任务已停止。已有阶段产物保留在结果目录，可重新选择新目录再运行。":selected.status==="failed"?"本次未完成，查看下方输出定位原因。":stageNames[selected.stage]||"";
    const progress=$("#job-progress");progress.hidden=selected.action!=="semantic-extract";progress.max=selected.total_stages||3;progress.value=selected.completed_stages||0;
    progress.setAttribute("aria-label","语义提取已完成阶段");
  }
  renderSemanticArtifacts(selected);
  const feedbackJob=activeJob||selected,key=feedbackJob?.id+":"+feedbackJob?.status;
  if(feedbackJob&&key!==jobFeedbackKey){jobFeedbackKey=key;companion.task(feedbackJob);}
  $("#jobs").textContent="";jobPage=Math.max(1,Math.min(jobPage,Math.ceil(records.length/4)||1));
  for(const j of records.slice((jobPage-1)*4,jobPage*4)){const b=node("button",(actionNames[j.action]||j.action)+" · "+statusNames[j.status],"secondary");b.setAttribute("aria-pressed",String(j.id===selectedJob));b.addEventListener("click",()=>{selectedJob=j.id;renderJobs();});$("#jobs").append(b);}
  $("#job-page").textContent=jobPage;$("#job-prev").disabled=jobPage===1;$("#job-next").disabled=jobPage*4>=records.length;updateWorkflow();
}
async function poll(){clearTimeout(timer);try{records=(await request("jobs")).jobs;renderJobs();$("#connection").textContent=appStatus.remote?"已连接远程服务":"本地服务已连接";}catch(error){$("#connection").textContent="连接中断";if($("#workspace").hidden)return;}timer=setTimeout(poll,activeJob?1000:4000);}
async function connected(){appStatus=await request("status");$("#login").hidden=true;$("#workspace").hidden=false;$("#shutdown").hidden=appStatus.remote;$("#logout").hidden=!appStatus.remote;$("#root").textContent="";for(const path of appStatus.roots){const opt=node("option",path);opt.value=path;$("#root").append(opt);}$("#existing-run").value=appStatus.run_dir||"";await Promise.all([loadConfig(),browse(appStatus.roots[0]),loadConversations()]);await poll();if(new URLSearchParams(location.search).get("tool")==="semantic"){projectPopup.open();$("#semantic-title").scrollIntoView({block:"center"});}if(appStatus.active_agent){agentRun=appStatus.active_agent;const r=await request("agent/"+agentRun);conversation=r.conversation;await loadMessages();pollAgent();}}
async function start(action){
  if(jobSubmitting||activeJob||agentRun)return;
  if(action==="semantic-extract"&&!appStatus?.semantic_extraction?.available)throw new Error("当前服务器尚未提供语义提取，请升级服务器后重新连接。");
  const payload={action,source:$("#source").value.trim(),project:$("#project").value.trim(),run:$("#run").value.trim(),provider:$("#provider").value,osm_place:$("#osm-place").value.trim()};
  if(action!=="doctor"&&!payload.source){$("#source").focus();throw new Error("请先选择一张 DWG 图纸。");}
  if(action==="semantic-extract"&&!payload.run){$("#run").focus();throw new Error("请填写一个新的结果目录。");}
  jobSubmitting=true;renderJobs();companion.set("working","正在提交"+(actionNames[action]||"任务"));
  try {
    const job=await request("jobs",payload);selectedJob=job.id;jobPage=1;records=[job,...records.filter(item=>item.id!==job.id)];
    jobFeedbackKey="";renderJobs();await poll();$("#job-status").scrollIntoView({block:"center"});
  } catch(error){companion.set("error",error.message);throw error;}
  finally{jobSubmitting=false;renderJobs();}
}
function renderDistricts(catalog){
  const choices=catalog?.scopes||[], select=$("#review-district");
  select.textContent="";
  for(const district of choices){
    const count=Object.values(district.delivery_counts||{}).reduce((sum,value)=>sum+value,0);
    const option=node("option",`${district.label} · ${count} 个要素${district.available?"":" · 不可用"}`);
    option.value=district.run_dir;
    option.disabled=!district.available;
    option.selected=district.region_id===catalog.selected_scope;
    if(district.error)option.title=district.error;
    select.append(option);
  }
  $("#review-districts").hidden=choices.length<2;
  const unavailable=choices.filter(item=>!item.available).length;
  $("#review-district-count").textContent=`${choices.length} 个 GIS 分区${unavailable?`，${unavailable} 个需要检查`:"，可分别打开核对"}`;
}
let mapReviewed=false,reviewReady=false,reviewOpening=false,reviewViewId="",reviewRevision="",reviewRun="",reviewSelectionDirty=false,svgLoadRequest=0,reviewFrameState="empty",reviewFrameTimer;
async function currentReviewRun(){
  const r=await fetch("/api/run?review="+encodeURIComponent(reviewId));
  const body=await r.json();if(!r.ok)throw new Error(body.detail||"读取审查结果失败");return body;
}
async function openReview(run){
  if(reviewOpening)return false;
  const ready=records.find(job=>job.status==="succeeded"&&job.run_dir&&["convert","auto-convert"].includes(job.action));
  run=(run||$("#existing-run").value||ready?.run_dir||$("#run").value||"").trim();
  showPage("osm");
  if(!run){
    setReviewFrameState("error","请填写结果目录，或在「项目与图纸」中进入结果目录后选择打开。");
    $("#existing-run").focus();return false;
  }
  reviewOpening=true;$("#open-review").disabled=true;
  $("#confirm-map").disabled=true;$("#manual-gcp").disabled=true;
  $("#view-delivery").disabled=true;
  $("#review-load-status").className="inline-result";
  $("#review-load-status").textContent="正在检查结果并加载地图…";
  try{
    const r=await request("review",{run});
    const changed=reviewUrl!==r.url||reviewId!==r.review_id||reviewRun!==r.run_dir||reviewFrameState!=="ready";
    reviewUrl=r.url;reviewId=r.review_id;reviewRun=r.run_dir;reviewSelectionDirty=false;
    renderDistricts(r.districts);$("#existing-run").value=r.run_dir;
    if(changed){
      reviewViewId=crypto.randomUUID();reviewRevision="";++svgLoadRequest;++deliveryRequest;
      $("#delivery-files").textContent="";
      setReviewFrameState("loading");
      const frameUrl=new URL(reviewUrl,location.origin);frameUrl.searchParams.set("view",reviewViewId);
      $("#osm-frame").src=frameUrl.href;
      $("#manual-gcp").textContent="人工 GCP 校正（可选）";
      $("#map-review-status").textContent="人工确认仅用于继续审阅，保留当前转换结果。";
      $("#svg-frame").hidden=true;$("#svg-frame").removeAttribute("src");
      clearTimeout(reviewFrameTimer);
      reviewFrameTimer=setTimeout(()=>setReviewFrameState("error","地图数据加载超时，请点击“打开结果”重试。"),30000);
    }else{
      $("#confirm-map").disabled=!reviewReady;$("#manual-gcp").disabled=false;
      $("#view-delivery").disabled=!reviewReady;
      $("#review-load-status").textContent="结果已打开。核对地图后可直接继续，人工 GCP 为可选操作。";
    }
    $("#osm-frame").hidden=false;$("#osm-empty").hidden=true;
    $("#map-review-actions").hidden=false;$("#review-fullscreen").disabled=false;$("#review-focus").disabled=false;
    updateWorkflow();projectPopup.close();
    // A missing SVG report must not turn a successfully opened map into a failed action.
    loadSvg();
    return true;
  }catch(error){setReviewFrameState("error","无法打开结果："+error.message);return false;}
  finally{reviewOpening=false;$("#open-review").disabled=false;}
}
function setReviewFrameState(state,detail=""){
  reviewFrameState=state;
  if(state!=="ready"){
    reviewReady=false;mapReviewed=false;++deliveryRequest;$("#delivery-files").textContent="";
    $("#confirm-map").disabled=true;$("#view-delivery").disabled=true;$("#manual-gcp").disabled=true;
  }
  if(state!=="loading")clearTimeout(reviewFrameTimer);
  const status=$("#review-load-status");status.className=state==="error"?"inline-result error":"inline-result";
  status.textContent=detail||(state==="loading"?"正在加载地图与图层，请稍候…":"结果已打开。核对地图后可直接继续，人工 GCP 为可选操作。");
  updateWorkflow();
}
function reviewFrameReady(){
  // A loaded shell alone is not evidence that GIS data and the applied revision are ready.
  if(!reviewUrl)return;
  const doc=$("#osm-frame").contentDocument;
  if(!doc?.querySelector("#console-app"))setReviewFrameState("error","地图页面未能加载，请点击“打开结果”重试。");
}
async function loadSvg(refresh=false){
  if(!reviewId)return;
  const selected=reviewId,epoch=++svgLoadRequest,frame=$("#svg-frame"),status=$("#svg-status");
  let matchingRevision;
  const current=()=>selected===reviewId&&epoch===svgLoadRequest;
  const failed=detail=>{if(current()){status.textContent="SVG 报告加载失败："+detail+"；请刷新报告重试。";frame.hidden=true;$("#svg-empty").hidden=false;}};
  try{
    const body=await currentReviewRun();if(!current())return;
    matchingRevision=(body.osm_matching?.record||body.osm_matching)?.match_sha256||"initial";
    const record=body.svg_candidates||{};
    $("#svg-empty").hidden=Boolean(record.report_url);frame.hidden=!record.report_url;
    if(!record.report_url){status.textContent=record.report_blocker||"此结果尚未生成 SVG 候选报告";frame.removeAttribute("src");return matchingRevision;}
    const url=new URL(record.report_url,location.origin);url.searchParams.set("review",selected);url.searchParams.set("revision",reviewRevision);
    if(refresh)url.searchParams.set("refresh",String(epoch));
    const loaded=()=>{
      if(!current())return;
      const doc=frame.contentDocument;
      if(!doc||doc.URL!==frame.src)return;
      if(doc.readyState!=="complete")return;
      if(doc.contentType!=="text/html"||!doc.querySelector("h1"))return failed("服务未返回可显示的报告");
      const images=[...doc.images];
      if(images.some(img=>!img.complete||img.naturalWidth===0))return failed("部分 SVG / PNG 图像未能加载");
      status.textContent="来源已绑定 · SVG 对应审阅报告"+(images.length?` · ${images.length} 张图像已加载`:"");
    };
    frame.onload=loaded;frame.onerror=()=>failed("无法读取报告资源");
    if(frame.src!==url.href){
      status.textContent="正在加载来源已绑定的 SVG 报告与图像…";
      // An iframe's load event also fires for HTTP error documents. Check the
      // authenticated report response before treating its document as evidence.
      const response=await fetch(url.href,{cache:"no-store"});
      if(!current())return;
      if(!response.ok)throw new Error("报告请求失败 ("+response.status+")");
      if(!response.headers.get("content-type")?.toLowerCase().startsWith("text/html"))throw new Error("服务未返回 HTML 审阅报告");
      await response.text();if(!current())return;
      frame.src=url.href;
    }else loaded();
    return matchingRevision;
  }catch(error){failed(error.message);if(current())return matchingRevision;}
}
window.addEventListener("message",event=>{
  if(event.origin!==location.origin||event.source!==$("#osm-frame").contentWindow||event.data?.reviewId!==reviewId||event.data?.viewId!==reviewViewId)return;
  if(event.data.type==="cad2gis-review-state"){
    if(!["loading","ready","error"].includes(event.data.state))return;
    if(event.data.state==="error")return setReviewFrameState("error",event.data.message||"地图数据加载失败，请点击“打开结果”重试。");
    reviewReady=event.data.state==="ready"&&event.data.ready===true&&typeof event.data.revision==="string"&&Boolean(event.data.revision);
    reviewFrameState=event.data.state;
    if(reviewFrameState==="ready")clearTimeout(reviewFrameTimer);
    if(!reviewReady||reviewSelectionDirty)mapReviewed=false;
    const canConfirm=reviewReady&&!reviewSelectionDirty&&reviewRun===$("#existing-run").value.trim();
    $("#confirm-map").disabled=!canConfirm;$("#view-delivery").disabled=!canConfirm;
    $("#manual-gcp").disabled=reviewFrameState!=="ready";
    $("#manual-gcp").textContent=event.data.manual?"收起人工 GCP 校正":"人工 GCP 校正（可选）";
    if(!reviewSelectionDirty&&event.data.state==="ready"&&event.data.runDir&&event.data.runDir!==reviewRun){
      reviewRun=event.data.runDir;$("#existing-run").value=reviewRun;mapReviewed=false;
      $("#map-review-status").textContent="已切换到新的匹配结果，请检查后继续。";
      ++deliveryRequest;$("#delivery-files").textContent="";
    }
    if(reviewRevision!==event.data.revision){
      mapReviewed=false;reviewRevision=event.data.revision;++svgLoadRequest;++deliveryRequest;
      $("#delivery-files").textContent="";
      $("#svg-frame").hidden=true;$("#svg-frame").removeAttribute("src");
      $("#svg-status").textContent="地图结果已更新，正在读取对应的 SVG 报告…";
      loadSvg();updateWorkflow();
    }
    $("#review-load-status").className="inline-result";
    $("#review-load-status").textContent=reviewReady?"结果已打开。核对地图后可直接继续，人工 GCP 为可选操作。":event.data.manual?"人工 GCP 校正已展开。完成后收起校正，核对已应用结果即可继续。":"正在准备当前地图结果…";
    updateWorkflow();
  }else if(event.data.type==="cad2gis-review-error"){
    setReviewFrameState("error","地图加载失败："+event.data.message);
  }
});
let deliveryRequest=0;
async function loadDelivery(directory=reviewRun){
  const requestId=++deliveryRequest,requestedRun=directory,status=$("#delivery-status"),files=$("#delivery-files");files.textContent="";
  status.className="inline-result";status.textContent="正在读取交付文件…";$("#delivery-path").textContent=requestedRun;
  try{
    const result=await request("delivery/artifacts?"+new URLSearchParams({run:requestedRun}));
    if(requestId!==deliveryRequest)return;
    status.textContent=result.artifacts.length?`已找到 ${result.artifacts.length} 个文件 · 原转换状态：${result.run_status||"未记录"}`:"当前结果目录尚无可交付文件。";
    if(result.archive){
      const box=node("div","","delivery-file"),text=node("p","下载完整 ZIP 并解压，再打开其中的 .qgz；请保留配套 GeoPackage 和样式文件。","muted"),download=node("a","下载完整 QGIS 交付包","primary");
      download.href="/_app/api/delivery/archive?"+new URLSearchParams({run:requestedRun});download.download="";box.append(text,download);files.append(box);
    }
    for(const item of result.artifacts){
      const row=node("div","","delivery-file"),copy=node("div"),actions=node("div","","review-actions");
      copy.append(node("strong",item.name),node("p",`${item.size>=1048576?(item.size/1048576).toFixed(1)+" MB":Math.max(1,Math.ceil(item.size/1024))+" KB"}`,"muted"));
      const download=node("a","下载","secondary");download.href="/_app/api/delivery/artifact?"+new URLSearchParams({run:requestedRun,name:item.name});download.download="";actions.append(download);
      if(/\.gpkg$/i.test(item.name)){
        const inspect=node("button","查看数据表","secondary");inspect.addEventListener("click",guarded(async()=>{$("#database-path").value=join(requestedRun,item.name);showPage("sqlite");await openDatabase();}));actions.prepend(inspect);
      }
      row.append(copy,actions);files.append(row);
    }
  }catch(error){if(requestId!==deliveryRequest)return;status.className="inline-result error";status.textContent="无法读取交付文件："+error.message;}
}
function displayProfile(provider){const p=aiConfig.profiles[provider];$("#ai-provider").value=provider;$("#ai-url").value=p.base_url;$("#ai-model").value=p.model;$("#ai-key").value="";$("#ai-capability").value=p.capability;$("#ai-token-field").value=p.token_field;$("#ai-timeout").value=p.timeout_s;$("#ai-max-tokens").value=p.max_tokens;$("#ai-max-requests").value=p.max_requests;$("#key-state").textContent=p.has_key?"已保存 · 留空保留":"尚未保存密钥";$("#config-badge").textContent=p.configured?"已配置":"待配置";$("#config-badge").classList.toggle("ready",p.configured);for(const b of $$("[data-provider]"))b.classList.toggle("button-active",b.dataset.provider===provider);$("#ai-test-result").textContent=p.configured?"配置已保存。点击测试连接，确认模型和工具均可使用。":p.issue;$("#ai-test-result").className="inline-result";}
async function loadConfig(){aiConfig=await request("ai/config");displayProfile(aiConfig.active);$("#config-storage").textContent=aiConfig.storage+" · 密钥不写入浏览器存储";for(const el of $$("#ai-config-form input, #ai-config-form select, #save-ai"))el.disabled=!aiConfig.editable;$("#provider").value=aiConfig.active;if(appStatus){appStatus.ai_provider=aiConfig.active;for(const [key,p] of Object.entries(aiConfig.profiles))appStatus.provider_configured[key]=p.configured;}const a=aiConfig.profiles[aiConfig.active];$("#composer-model").textContent=a.configured?a.model+" · CAD2GIS tools":"先连接你的 AI 模型";$("#ai-dot").classList.toggle("ready",a.configured);updateProvider();}
async function saveConfig(){const data={provider:$("#ai-provider").value,base_url:$("#ai-url").value.trim(),model:$("#ai-model").value.trim(),api_key:$("#ai-key").value.trim(),capability:$("#ai-capability").value,token_field:$("#ai-token-field").value,timeout_s:Number($("#ai-timeout").value),max_tokens:Number($("#ai-max-tokens").value),max_requests:Number($("#ai-max-requests").value)};await request("ai/config",data);$("#ai-key").value="";await loadConfig();}
async function loadConversations(){const d=await request("conversations?page="+chatPage);$("#conversations").textContent="";for(const item of d.items){const b=node("button",item.title,item.id===conversation?"selected":"");b.title=item.title;b.addEventListener("click",guarded(async()=>{conversation=item.id;messageBefore=0;showPage("chat");await loadMessages(false,true);await loadConversations();}));$("#conversations").append(b);}if(!d.items.length)$("#conversations").append(node("p","还没有对话记录","empty-copy"));$("#chat-page").textContent=chatPage;$("#chat-prev").disabled=chatPage===1;$("#chat-next").disabled=!d.has_more;}
function inline(parent,text){const pattern=/(\*\*[^*]+\*\*|\x60[^\x60]+\x60|\[[^\]]+\]\(https?:\/\/[^)]+\))/g;let end=0;for(const m of text.matchAll(pattern)){parent.append(document.createTextNode(text.slice(end,m.index)));const v=m[0];if(v.startsWith("**"))parent.append(node("strong",v.slice(2,-2)));else if(v.startsWith("\x60"))parent.append(node("code",v.slice(1,-1)));else{const a=v.match(/^\[([^\]]+)\]\(([^)]+)\)$/),link=node("a",a[1]);link.href=a[2];link.target="_blank";link.rel="noopener noreferrer";parent.append(link);}end=m.index+v.length;}parent.append(document.createTextNode(text.slice(end)));}
function markdown(parent,text){parent.textContent="";let code=null,list=null;for(const line of text.split("\n")){if(line.startsWith("\x60\x60\x60")){if(code){parent.append(code);code=null;}else code=node("pre");list=null;continue;}if(code){code.textContent+=line+"\n";continue;}if(!line.trim()){list=null;continue;}let n;const h=line.match(/^(#{1,3})\s+(.*)/);if(h){n=node("h"+h[1].length);inline(n,h[2]);list=null;}else if(/^[-*]\s|^\d+\.\s/.test(line)){if(!list){list=node("ul");parent.append(list);}n=node("li");inline(n,line.replace(/^([-*]|\d+\.)\s/,""));list.append(n);continue;}else{n=node("p");inline(n,line);list=null;}parent.append(n);}if(code)parent.append(code);}
function drawMessage(item){const row=node("div","","message-row "+item.role);if(item.role==="assistant"){const img=node("img","","message-avatar");img.src="/_app/assets/assets/molamaker-icon.png";img.alt="molamaker";row.append(img);}const body=node("div","","message-content");body.append(node("div",item.role==="user"?"你":item.role==="assistant"?"molamaker":"任务状态","role-label"));const content=node("div");markdown(content,item.text);body.append(content);row.append(body);return row;}
async function loadMessages(older=false,restore=false){
  if(!conversation)return;
  const selected=conversation,epoch=++messageRequest;
  const d=await request("conversations/"+selected+(older?"?before="+messageBefore:""));
  if(conversation!==selected||epoch!==messageRequest)return;
  const container=$("#chat-messages"),previousHeight=container.scrollHeight,previousTop=container.scrollTop;
  if(restore){
    for(const key of ["source","project","run"])$("#"+key).value=d.context?.[key]||"";
    $("#osm-place").value=d.context?.osm_place||"";syncSource();
  }
  if(!older){
    if(agentEventConversation!==conversation)clearActivity();
    showChatResult(d.result);renderChatArtifacts(d.artifacts||[]);container.textContent="";
  }
  const frag=document.createDocumentFragment();for(const item of d.messages)frag.append(drawMessage(item));
  if(older)container.prepend(frag);else container.append(frag);
  messageBefore=d.before;$("#older-messages").hidden=!d.has_more;$("#agent-plan").textContent="";
  for(const step of d.plan)$("#agent-plan").append(node("li",(step.status==="completed"?"✓ ":step.status==="running"?"◉ ":"○ ")+step.text,step.status));
  container.scrollTop=older?previousTop+container.scrollHeight-previousHeight:container.scrollHeight;
}
function showChatResult(result){
  const button=$("#chat-result");
  button.hidden=!result?.run_dir;button.dataset.run=result?.run_dir||"";
  if(!result?.run_dir)return;
  button.textContent="打开转换结果 · "+(result.drawing||result.run_dir).split(/[\\/]/).pop();
  button.title=result.run_dir;
  if(!$("#source").value||$("#source").value===result.drawing){
    $("#existing-run").value=result.run_dir;$("#run").value=result.run_dir;
    $("#project").value=result.project_dir;$("#source").value=result.drawing;
    syncSource();
  }
}
function drawAgentEvents(events){
  const container=$("#agent-events"),signature=JSON.stringify(events);
  if(container.dataset.signature===signature)return;
  const expanded=new Set([...container.querySelectorAll("details[open]")].map(el=>el.dataset.key));
  const previousTop=container.scrollTop,atEnd=container.scrollHeight-container.clientHeight-previousTop<24;
  container.textContent="";container.dataset.signature=signature;
  for(const item of events){
    const row=node("details","","event "+item.kind);
    row.dataset.key=JSON.stringify([item.time,item.kind,item.name]);
    row.open=expanded.has(row.dataset.key);
    const summary=node("summary");summary.append(node("strong",item.name));row.append(summary);
    if(item.detail)row.append(node("pre",typeof item.detail==="string"?item.detail:JSON.stringify(item.detail,null,2),"event-detail"));
    container.append(row);
  }
  container.scrollTop=atEnd?container.scrollHeight:previousTop;
}
let agentEventConversation="",agentPollRequest=0;
function renderChatArtifacts(items){
  const container=$("#chat-artifacts");container.textContent="";
  for(const item of items){
    if(!item.run_dir&&!item.output_dir)continue;
    const group=node("div","","chat-artifact");
    const scoped=item.kind==="qgis_scope";
    group.append(node("span",scoped?`${item.scope} · ${item.delivery_kind==="standard_matched"?"OSM 匹配交付":"未校正参考视图"}`:item.kind==="qgis_package"?"QGIS 交付已生成":"转换结果已生成","muted"));
    if(item.run_dir){
      const map=node("button","打开地图","secondary");map.addEventListener("click",guarded(()=>openReview(item.run_dir)));group.append(map);
      const svg=node("button","SVG / PNG 预览","secondary");svg.addEventListener("click",guarded(async()=>{if(await openReview(item.run_dir))showPage("svg");}));group.append(svg);
    }
    if(scoped&&item.output_dir){
      const delivery=node("button","六文件交付","primary");delivery.addEventListener("click",guarded(async()=>{showPage("delivery");await loadDelivery(item.output_dir);}));group.append(delivery);
    }
    if(item.kind==="qgis_package"&&item.output_dir){
      const delivery=node("button","QGZ 与完整交付包","primary");delivery.addEventListener("click",guarded(async()=>{showPage("delivery");await loadDelivery(item.output_dir);}));group.append(delivery);
    }
    container.append(group);
  }
  container.hidden=!container.children.length;
}
function clearActivity(){
  agentEventConversation="";$("#agent-events").textContent="";delete $("#agent-events").dataset.signature;
  $("#agent-plan").textContent="";$("#agent-usage").textContent="";$("#agent-status").textContent="等待任务";$("#agent-stream").hidden=true;
}
function renderAgentEvents(events){
  drawAgentEvents(events.slice(-40));
}
async function pollAgent(){
  clearTimeout(agentTimer);if(!agentRun)return;
  const selected=agentRun,epoch=++agentPollRequest,current=()=>selected===agentRun&&epoch===agentPollRequest;
  try{
    const d=await request("agent/"+selected),live=d.status==="running";
    if(!current())return;
    companion.task({action:"AI 审阅",status:d.status});$("#agent-stop").hidden=!live;$("#send").disabled=live;
    if(d.conversation===conversation){
      agentEventConversation=d.conversation;renderAgentEvents(d.events);if(d.artifacts?.length)renderChatArtifacts(d.artifacts);
      $("#agent-status").textContent=statusNames[d.status]||d.status;
      $("#agent-stream").hidden=!d.progress;$("#agent-stream").textContent=d.progress;
      $("#agent-usage").textContent=d.usage.requests?"模型 "+d.usage.requests+" 次 · 工具 "+d.usage.tool_calls+" 次":"";
      if(d.result?.run_dir)showChatResult(d.result);
    }
    if(!live){
      agentRun="";$("#agent-stop").hidden=true;$("#send").disabled=false;
      if(d.conversation===conversation)await loadMessages();
      await loadConversations();renderJobs();return;
    }
    if(d.conversation===conversation){
      const plan=(await request("conversations/"+d.conversation)).plan;
      if(!current())return;
      if(d.conversation===conversation){
        $("#agent-plan").textContent="";
        for(const step of plan)$("#agent-plan").append(node("li",(step.status==="completed"?"✓ ":step.status==="running"?"◉ ":"○ ")+step.text,step.status));
      }
    }
    agentTimer=setTimeout(pollAgent,700);
  }catch(error){if(!current())return;message(error.message,true);agentTimer=setTimeout(pollAgent,2500);}
}
async function sendChat(e){e.preventDefault();if(agentRun)return;const text=$("#chat-input").value.trim();if(!text)return;const r=await request("agent",{conversation:conversation||null,message:text,source:$("#source").value.trim(),project:$("#project").value.trim(),run:$("#run").value.trim(),osm_place:$("#osm-place").value.trim()});conversation=r.conversation;agentRun=r.id;$("#chat-input").value="";$("#send").disabled=true;await loadMessages();await loadConversations();pollAgent();renderJobs();}
async function openDatabase(){databaseSchema=(await request("database/schema?path="+encodeURIComponent($("#database-path").value.trim()))).tables;$("#tables").textContent="";$("#table-count").textContent=databaseSchema.length;for(const table of databaseSchema){const b=node("button",table.name);b.title=table.columns.join(", ");b.addEventListener("click",guarded(async()=>{databaseTable=table.name;databasePage=1;$("#sql-query").value='SELECT * FROM "'+table.name.replace(/"/g,'""')+'"';$("#schema-text").textContent=table.sql||table.columns.join(", ");for(const other of $$("#tables button"))other.classList.remove("selected");b.classList.add("selected");await databaseQuery();}));$("#tables").append(b);}if(databaseSchema.length)$("#tables button").click();else{$("#data-empty").hidden=false;$("#data-empty").textContent="数据库中没有可浏览的数据表。";}}
async function databaseQuery(){const r=await request("database/query",{path:$("#database-path").value.trim(),table:databaseTable,sql:$("#sql-query").value,page:databasePage,page_size:50}),head=$("#data-table thead"),body=$("#data-table tbody");head.textContent="";body.textContent="";const tr=node("tr");for(const name of r.columns)tr.append(node("th",name));head.append(tr);for(const row of r.rows){const n=node("tr");for(const v of row){const cell=node("td",v===null?"NULL":String(v));cell.title=cell.textContent;n.append(cell);}body.append(n);}$("#data-empty").hidden=r.rows.length>0;$("#data-empty").textContent="本页没有记录";$("#data-page").textContent=databasePage;$("#data-status").textContent=r.rows.length+" 行 · "+r.columns.length+" 列 · 只读";$("#data-prev").disabled=databasePage===1;$("#data-next").disabled=!r.has_more;}
async function showDoc(name){const labels={guide:"使用说明",ai:"AI 配置与排查",architecture:"集成架构文档",sources:"框架与设计来源"},d=await request("docs/"+name);$("#help-title").textContent=labels[name]||"使用说明";markdown($("#help-content"),d.content);for(const b of $$(".help-nav button"))b.classList.toggle("active",b.dataset.doc===name);helpPopup.open();}
document.addEventListener("click",guarded(async e=>{if(e.target.closest("button:disabled,[aria-disabled=true]"))return;const semantic=e.target.closest("[data-open-semantic]");if(semantic){toolsPopup.close();projectPopup.open();$("#semantic-title").scrollIntoView({block:"center"});$("#semantic-extract").focus();}const page=e.target.closest("[data-page]");if(page)showPage(page.dataset.page);const doc=e.target.closest("[data-doc]");if(doc){toolsPopup.close();await showDoc(doc.dataset.doc);}const project=e.target.closest("[data-project]");if(project)projectPopup.open();const workflow=e.target.closest("[data-workflow]");if(workflow)await followWorkflow(workflow.dataset.workflow);const preset=e.target.closest("[data-provider]");if(preset)displayProfile(preset.dataset.provider);const prompt=e.target.closest("[data-prompt]");if(prompt){$("#chat-input").value=prompt.dataset.prompt;$("#chat-input").focus();}}));
on("#open-tools","click",()=>toolsPopup.open());
on("#semantic-extract","click",()=>start("semantic-extract"));
on("#new-output","click",()=>{const current=$("#run").value.trim(),parent=current.replace(/[\\/][^\\/]+$/,"");$("#run").value=join(parent||directory||appStatus.roots[0],currentResultName());message("已选择新的结果目录，之前的产物会保留。");$("#run").focus();});
on("#semantic-agent-draft","click",()=>{
  const job=records.find(item=>item.id===selectedJob&&item.action==="semantic-extract"&&item.status==="succeeded");
  if(!job)return;
  const sourceRun=join(job.run_dir,"source"),prepareManifest=join(join(job.run_dir,"candidates"),"manifest.json");
  $("#chat-input").value=`请审阅已完成的语义提取。source_run=${JSON.stringify(sourceRun)}；prepare_manifest=${JSON.stringify(prepareManifest)}。先检查源快照与候选来源绑定，按页查看分类、标签、尺寸候选，列出有证据的建议和仍有歧义的对象。保持源几何、原坐标和 baseline 不变；本轮只审阅，不初始化、提交或编译语义修订。`;
  projectPopup.close();showPage("chat");$("#chat-input").focus();message("审阅提示已填入对话，检查后再发送。");
});
on("#osm-frame","load",reviewFrameReady);
on("#view-delivery","click",()=>followWorkflow("export"));
on("#existing-run","input",()=>{reviewSelectionDirty=true;mapReviewed=false;$("#confirm-map").disabled=true;$("#view-delivery").disabled=true;updateWorkflow();});
on("#review-focus","click",()=>setReviewFocus(!$("#app").classList.contains("review-expanded")));
on("#confirm-map","click",async()=>{
  if(!reviewReady||reviewSelectionDirty||reviewRun!==$("#existing-run").value.trim())return;
  const selected=reviewId,revision=reviewRevision;
  const loadedRevision=await loadSvg();
  if(!reviewReady||reviewSelectionDirty||selected!==reviewId||revision!==reviewRevision||reviewRun!==$("#existing-run").value.trim())return;
  if(loadedRevision!==revision){setReviewFrameState("loading");await openReview($("#existing-run").value);message("检测到新的匹配结果，请核对更新后的地图再继续。");return;}
  mapReviewed=true;$("#map-review-status").textContent="已人工确认当前地图匹配，可继续审阅符号和交付结果。";
  updateWorkflow();showPage("svg");
});
on("#manual-gcp","click",()=>{
  const doc=$("#osm-frame").contentDocument;
  const open=Boolean(doc?.querySelector("#stage-registration")?.hidden);
  doc?.querySelector(open?'[data-stage="registration"]':'[data-stage="source"]')?.click();
  $("#manual-gcp").textContent=open?"收起人工 GCP 校正":"人工 GCP 校正（可选）";
  if(open)doc?.querySelector("#stage-registration")?.scrollIntoView({block:"nearest"});
  $("#osm-frame").scrollIntoView({block:"start"});
});
on("#login-form","submit",async e=>{e.preventDefault();try{await request("login",{password:$("#password").value});$("#password").value="";$("#login-error").textContent="";await connected();}catch(error){$("#login-error").textContent=error.message;}});
for(const id of ["show-project","choose-drawing","conversion-settings","browse-database"])on("#"+id,"click",()=>projectPopup.open());
on("#new-chat","click",async()=>{const item=await request("conversations",{title:"新的转换对话"});conversation=item.id;showChatResult(null);clearActivity();renderChatArtifacts([]);$("#chat-messages").textContent="";$("#chat-messages").append(welcomeTemplate.cloneNode(true));$("#older-messages").hidden=true;showPage("chat");await loadConversations();});
on("#chat-result","click",()=>openReview($("#chat-result").dataset.run));
on("#refresh-chats","click",loadConversations);on("#chat-prev","click",async()=>{chatPage--;await loadConversations();});on("#chat-next","click",async()=>{chatPage++;await loadConversations();});on("#chat-form","submit",sendChat);on("#agent-stop","click",async()=>{if(agentRun)await request("agent/"+agentRun+"/cancel",{});await pollAgent();});on("#older-messages","click",()=>loadMessages(true));on("#chat-input","keydown",e=>{if(e.key==="Enter"&&(e.ctrlKey||e.metaKey)){e.preventDefault();$("#chat-form").requestSubmit();}});
on("#ai-config-form","submit",async e=>{e.preventDefault();await saveConfig();message("AI 配置已保存，可测试连接。");});on("#test-ai","click",async()=>{$("#test-ai").disabled=true;$("#ai-test-result").className="inline-result";$("#ai-test-result").textContent="正在请求模型并测试一次真实工具调用…";try{if(aiConfig.editable)await saveConfig();const r=await request("ai/test",{provider:$("#ai-provider").value});$("#ai-test-result").textContent=r.message+(r.latency_ms?" · "+(r.latency_ms/1000).toFixed(1)+" 秒":"");$("#ai-test-result").className="inline-result "+(r.tool_calling?"ok":"error");}finally{$("#test-ai").disabled=false;}});
on("#convert-form","submit",e=>{e.preventDefault();return start($("#mode").value);});for(const a of ["inspect","bootstrap","doctor"])on("#"+a,"click",()=>start(a));on("#root","change",()=>browse($("#root").value));on("#parent","click",()=>browse(parentDirectory));on("#file-prev","click",()=>browse(directory,filePage-1));on("#file-next","click",()=>browse(directory,filePage+1));let searchTimer;on("#file-search","input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>browse(directory).catch(e=>message(e.message,true)),250);});on("#source","input",syncSource);
on("#select-project","click",()=>{$("#project").value=directory;if(!$("#run").value)$("#run").value=join(directory,currentResultName());message("已选择当前项目目录");});on("#select-run","click",()=>openReview(directory));on("#mode","change",updateProvider);on("#provider","change",updateProvider);on("#open-review","click",()=>openReview($("#existing-run").value.trim()));on("#review-result","click",()=>{const j=records.find(j=>j.id===selectedJob);if(j&&j.run_dir)return openReview(j.run_dir);});on("#review-fullscreen","click",()=>{if(reviewUrl)window.open(reviewUrl,"_blank","noopener");});on("#refresh-svg","click",()=>loadSvg(true));on("#cancel","click",async()=>{if(activeJob)await request("jobs/"+activeJob.id+"/cancel",{});await poll();});on("#job-prev","click",()=>{jobPage--;renderJobs();});on("#job-next","click",()=>{jobPage++;renderJobs();});
on("#open-database","click",openDatabase);on("#execute-sql","click",()=>{databaseTable="";databasePage=1;return databaseQuery();});on("#data-prev","click",()=>{databasePage--;return databaseQuery();});on("#data-next","click",()=>{databasePage++;return databaseQuery();});on("#logout","click",async()=>{await request("logout",{});showLogin();});on("#shutdown","click",async()=>{await request("shutdown",{});clearTimeout(timer);clearTimeout(agentTimer);$("#workspace").hidden=true;$("#connection").textContent="程序已关闭";message("服务已关闭，下次请从开始菜单打开 CAD2GIS。");});
on("#upload","change",()=>{const f=$("#upload").files[0];if(!f)return;if(!/\.dwg$/i.test(f.name)||f.size>512*1024*1024||!f.size){$("#upload").value="";throw new Error("请选择有效 DWG，大小不得超过 512 MB；DXF 请先另存为 DWG。");}const x=new XMLHttpRequest();x.open("POST","/_app/api/upload?name="+encodeURIComponent(f.name)+"&root="+$("#root").selectedIndex);x.setRequestHeader("Content-Type","application/octet-stream");$("#upload").disabled=true;x.upload.onprogress=e=>$("#upload-status").textContent="正在导入 "+f.name+(e.lengthComputable?" · "+Math.round(e.loaded/e.total*100)+"%":"");x.onload=async()=>{try{const r=JSON.parse(x.responseText);if(x.status<200||x.status>=300)throw new Error(r.detail||"导入失败");$("#source").value=r.source;$("#project").value=r.project;$("#run").value=r.run;$("#mode").value="auto-convert";updateProvider();syncSource();await browse(r.directory);$("#upload-status").textContent="已导入 "+f.name+"，可以检查图纸或交给智能体。";message("图纸已导入项目目录");}catch(e){message(e.message,true);}finally{$("#upload").disabled=false;$("#upload").value="";}};x.onerror=()=>{$("#upload").disabled=false;$("#upload").value="";message("图纸导入中断，请检查连接。",true);};x.send(f);});
(async()=>{const token=new URLSearchParams(location.hash.slice(1)).get("token");if(token)history.replaceState(null,"",location.pathname+location.search);try{if(token)await request("login",{token});await connected();}catch(e){showLogin();if(token)$("#login-error").textContent=e.message;}})();

on("#review-district","change",async()=>{const select=$("#review-district");select.disabled=true;try{await openReview(select.value);}finally{select.value=$("#existing-run").value;select.disabled=false;}});
