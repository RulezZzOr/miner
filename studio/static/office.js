"use strict";
// Original Studio office view. No code or assets from Agents Office are used.
const officeDepartments = [
  {id:"operations",name:"Operations",color:"#569681",x:25,y:25},
  {id:"delivery",name:"Delivery",color:"#6689b9",x:335,y:25},
  {id:"growth",name:"Growth",color:"#ab7fba",x:645,y:25},
  {id:"finance",name:"Finance",color:"#b4934e",x:25,y:410},
  {id:"platform",name:"Platform",color:"#579ca2",x:335,y:410},
  {id:"studio",name:"Studio projects",color:"#7d8a9d",x:645,y:410},
];
const officeStatusNames={working:"Working",review:"Reviewing",verifying:"Checking",waiting:"Needs attention",blocked:"Blocked",paused:"Paused",done:"Accepted",finished:"Run finished",queued:"Queued",idle:"Idle",stale:"Awaiting activity",cancelled:"Stopped"};
function officeRunPhase(m,run) {
  return run?.mission?.phase || m?.attempts?.find(a=>a.id===run?.id)?.phase || m?.phase || "queue";
}
function officeItemStatus(m,run,log,now) {
  if(m && ["accepted","cancelled","expired","paused","blocked"].includes(m.status))return {accepted:"done",cancelled:"cancelled",expired:"blocked",paused:"paused",blocked:"blocked"}[m.status];
  if(m && ["waiting","awaiting_plan","ready","awaiting_checks"].includes(m.status))return "waiting";
  if(log?.approvals?.length || run?.status==="waiting")return "waiting";
  if(m?.status==="verifying")return "verifying";
  if(run && ["failed","interrupted"].includes(run.status))return "blocked";
  if(run?.status==="cancelled")return "cancelled";
  if(!m && run?.status==="completed")return "finished";
  if(run && ["running","stopping"].includes(run.status)) {
    const recent=(log?.events||[]).reduce((latest,e)=>Math.max(latest,Number(e.time)||0),0);
    if(!recent || now-recent>180 || log?.loading)return "stale";
    const lastIssue=(log?.events||[]).filter(e=>e.type==="error" || e.type==="note"&&isToolObstacle(e.text)).at(-1);
    if(lastIssue && recent-Number(lastIssue.time)<30)return "blocked";
    return ["review","final"].includes(officeRunPhase(m,run)) ? "review":"working";
  }
  if(m?.status==="running")return "queued";
  return "queued";
}
function officeModel(data,companyId="",logs={},now=Date.now()/1000) {
  const companies=data.companies||[],missions=data.missions||[],runs=data.runs||[];
  const byMission=new Map(missions.map(m=>[m.id,m])),byRun=new Map(runs.map(r=>[r.id,r]));
  const covered=new Set(),items=[];
  const append=(task,company,m)=>{
    if(m)covered.add(m.id);
    const run=m?.active_attempt ? byRun.get(m.active_attempt) : null;
    const lastAttempt=m?.attempts?.at(-1),displayRun=run || byRun.get(lastAttempt?.id);
    const log=run ? logs[run.id] : null;
    const department=officeDepartments.some(d=>d.id===task.department)?task.department:"studio";
    const status=m ? officeItemStatus(m,run,log,now) : ({blocked:"blocked",done:"done",cancelled:"cancelled",needs_owner:"waiting",rejected:"cancelled"}[task.status] || (company?.status==="paused"?"paused":"queued"));
    items.push({id:m ? "mission:"+m.id : "task:"+company.id+":"+task.id,title:task.title||m?.title||"Untitled task",department,status,
      mission:m?.id,run:displayRun?.id,project:m?.project||task.project,company:company?.id,companyName:company?.name||"Studio",phase:run ? officeRunPhase(m,run) : lastAttempt?.phase||m?.phase||"queue",lastRun:!run&&Boolean(displayRun),
      model:displayRun?.model||null,profile:displayRun?.profile||m?.profile||company?.profile||null,reviewProfile:m?.review_profile||company?.review_profile||null,
      decision:m?.decision||null,
      reviewEvidence:lastAttempt?.review_packet ? {id:lastAttempt.review_packet.id,bytes:lastAttempt.review_packet.bytes,
        snapshot:lastAttempt.review_packet.snapshot,reads:lastAttempt.review_reads??null,limits:lastAttempt.review_packet.limits,started:lastAttempt.started} : null,
      lastActivity:(log?.events||[]).reduce((latest,e)=>Math.max(latest,Number(e.time)||0),0)||null,
      message:m?.message||task.goal||"No execution has started.",updated:m?.updated||task.created||null,
      attempts:m?.attempts?.length||0,completed:m?.tasks?.filter(t=>t.status==="done").length||0,total:m?.tasks?.length||0,
      approvals:log?.approvals?.length||0,summary:company?.purpose||"",source:run ? "Active run" : displayRun ? "Last recorded run" : m ? "Saved execution" : "Saved company task"});
  };
  for(const c of companies.filter(c=>!companyId||c.id===companyId))for(const t of c.tasks||[])append(t,c,byMission.get(t.last_mission));
  if(!companyId) {
    for(const m of missions)if(!covered.has(m.id))append({title:m.title,department:"studio"},null,m);
    for(const r of runs.filter(r=>!r.mission))items.push({id:"run:"+r.id,title:r.task?.split("\n")[0]?.slice(0,120)||"Agent run",department:"studio",status:officeItemStatus(null,r,logs[r.id],now),run:r.id,project:r.project,companyName:"Studio",phase:r.mode||"agent",model:r.model,profile:r.profile,message:r.reason||"Open the run to inspect its recorded activity.",updated:r.ended||r.created,source:"Recorded run",completed:0,total:0,attempts:1,approvals:logs[r.id]?.approvals?.length||0});
  }
  const priority={blocked:0,waiting:1,working:2,review:2,verifying:2,stale:3,queued:4,paused:5,done:6,finished:6,cancelled:7};
  items.sort((a,b)=>(priority[a.status]??8)-(priority[b.status]??8)||(b.updated||0)-(a.updated||0)||a.id.localeCompare(b.id));
  return {items,departments:officeDepartments.map(d=>({...d,items:items.filter(i=>i.department===d.id)})),
    active:items.filter(i=>["working","review","verifying"].includes(i.status)).length,
    attention:items.filter(i=>["blocked","waiting","stale"].includes(i.status)).length,
    done:items.filter(i=>i.status==="done").length};
}
const officeState={company:"",selected:null,department:null,data:null,model:null,logs:{},loading:false,epoch:0,lastSuccess:0,signature:"",angle:-28,tilt:55,zoom:1,layout:"3d",offline:false,warning:""};
function officeBox(parent,x,y,z,w,d,h,color,cls="") {
  const box=el("div","office-box "+cls);box.setAttribute("aria-hidden","true");
  box.style.cssText=`--x:${x}px;--y:${y}px;--z:${z}px;--w:${w}px;--d:${d}px;--h:${h}px;--box-color:${color}`;
  for(const face of ["top","north","south","east","west"])box.append(el("i","office-face "+face));parent.append(box);return box;
}
function officeSeat(parent,item,x,y,color) {
  const seat=el("div","office-seat "+(item?.status||"empty"));seat.style.cssText=`--sx:${x}px;--sy:${y}px;--accent:${color}`;seat.dataset.noContextHelp="true";
  // Desk, legs, screen and chair are independent CSS 3D solids.
  officeBox(seat,0,0,33,88,48,5,"#c6b99f","office-desktop");
  for(const [a,b] of [[5,5],[77,5],[5,37],[77,37]])officeBox(seat,a,b,0,5,5,33,"#74776c");
  officeBox(seat,26,8,38,36,6,23,"#354344","office-monitor");
  officeBox(seat,29,14,42,30,1,15,item ? "#c5e5df":"#bec9c6","office-display");
  officeBox(seat,30,27,39,30,13,1,"#e8e6dd");
  officeBox(seat,73,29,39,6,6,8,"#f5f1e6","office-mug");
  officeBox(seat,31,60,15,28,25,4,"#71857c");officeBox(seat,32,82,17,26,5,27,"#7e9187");
  if(item){
    const person=el("div","office-person");
    officeBox(person,36,59,19,19,18,29,color,"office-torso");
    officeBox(person,37,58,49,17,17,17,"#d4af93","office-head");
    officeBox(person,37,58,63,17,17,5,"#43413c","office-hair");
    officeBox(person,37,64,5,7,12,18,"#53646c");officeBox(person,48,64,5,7,12,18,"#53646c");
    officeBox(person,32,44,33,6,22,6,color,"office-arm");officeBox(person,54,44,33,6,22,6,color,"office-arm");
    seat.append(person);
    const button=el("button","office-agent",item.title);button.type="button";button.dataset.noContextHelp="true";
    button.setAttribute("aria-label",`${item.title} — ${officeStatusNames[item.status]}. Open details`);button.dataset.officeItem=item.id;button.onclick=()=>selectOfficeItem(item.id);seat.append(button);
    const badge=el("span","office-seat-signal",item.status==="blocked"?"!":item.status==="waiting"?"?":"");badge.setAttribute("aria-hidden","true");seat.append(badge);
  }
  parent.append(seat);
}
function renderOfficeScene(model) {
  const world=$("#office-world");world.replaceChildren();
  world.classList.toggle("office-has-activity",model.active>0);
  // Independent raised islands and bridges; geometry is generated locally.
  officeBox(world,140,317,-13,635,30,10,"#cbcdbf","office-bridge");
  for(const x of [140,450,760])officeBox(world,x,240,-13,30,190,10,"#cbcdbf","office-bridge");
  officeBox(world,383,292,-15,164,82,16,"#c4d3c8","office-hub-base");
  const hub=el("button","office-hub");hub.type="button";hub.dataset.noContextHelp="true";
  hub.append(el("span","office-hub-icon","◈"),el("strong","","Company Driver"),el("small","",`${model.active} active · ${model.attention} need attention`));
  hub.setAttribute("aria-label","Select the highest priority recorded task");
  hub.onclick=()=>{if(model.items[0])selectOfficeItem(model.items[0].id);};hub.disabled=!model.items.length;world.append(hub);
  for(const d of model.departments){
    const pod=el("div","office-pod");pod.style.cssText=`--px:${d.x}px;--py:${d.y}px;--accent:${d.color}`;pod.dataset.department=d.id;
    officeBox(pod,-10,-6,-18,274,252,18,"#c9cebd","office-island");
    const label=el("button","office-department-label",d.name);label.type="button";label.dataset.noContextHelp="true";label.setAttribute("aria-label",`Show ${d.name} tasks`);label.onclick=()=>{officeState.department=officeState.department===d.id?null:d.id;renderOfficeList();};pod.append(label);
    const cap=el("span","office-pod-count",`${d.items.length} ${d.items.length===1?"task":"tasks"} · ${d.items.filter(i=>["blocked","waiting","stale"].includes(i.status)).length} alerts`);pod.append(cap);
    for(let n=0;n<4;n++)officeSeat(pod,d.items[n],(n%2)*122,40+Math.floor(n/2)*100,d.color);
    // Plant and low department partition, built from original geometry.
    officeBox(pod,237,192,0,13,13,13,"#b99780","office-planter");
    officeBox(pod,235,190,13,17,17,15,"#74967a","office-leaf");
    officeBox(pod,238,193,27,11,11,12,"#91ac81","office-leaf");
    world.append(pod);
  }
  positionOfficeCamera();
}
function positionOfficeCamera(){
  const stage=$("#office-stage");if(!stage)return;
  const fit=Math.min((stage.clientWidth-35)/1120,(stage.clientHeight-35)/760);
  $("#office-world").style.setProperty("--camera-angle",officeState.angle+"deg");$("#office-world").style.setProperty("--camera-tilt",officeState.tilt+"deg");
  $("#office-world").style.transform=`translate(-50%,-50%) scale(${Math.max(.14,fit)*officeState.zoom}) rotateX(${officeState.tilt}deg) rotateZ(${officeState.angle}deg)`;
  $("#office-camera-value").textContent=`${Math.round(officeState.zoom*100)}%`;
}
function selectOfficeItem(id){officeState.selected=id;renderOfficeList();renderOfficeDetail();}
function renderOfficeList(){
  const model=officeState.model;if(!model)return;
  const list=$("#office-roster");
  const items=model.items.filter(i=>!officeState.department||i.department===officeState.department);
  const signature=JSON.stringify([officeState.department,officeState.selected,items.map(i=>[i.id,i.title,i.companyName,i.status])]);
  if(list.dataset.signature===signature)return;
  const scroll=list.scrollTop,hadFocus=list.contains(document.activeElement);list.dataset.signature=signature;list.replaceChildren();
  $("#office-roster-title").textContent=officeState.department ? officeDepartments.find(d=>d.id===officeState.department).name+" · all tasks" : "All work";
  if(!items.length)list.append(el("p","office-empty","No work assigned here. Add a task in Company or AI Projects."));
  for(const item of items){const row=el("button","office-roster-item "+item.status);row.type="button";row.dataset.noContextHelp="true";row.setAttribute("aria-pressed",String(item.id===officeState.selected));
    row.append(el("span","office-state-dot"),el("strong","",item.title),el("small","",`${officeStatusNames[item.status]} · ${item.companyName}`));row.onclick=()=>selectOfficeItem(item.id);list.append(row);}
  list.scrollTop=scroll;if(hadFocus)list.querySelector('[aria-pressed="true"]')?.focus({preventScroll:true});
  $("#office-clear-filter").hidden=!officeState.department;
  for(const b of $$("[data-office-item]"))b.setAttribute("aria-pressed",String(b.dataset.officeItem===officeState.selected));
}
function renderOfficeDetail(){
  const panel=$("#office-detail"),item=officeState.model?.items.find(i=>i.id===officeState.selected);
  const signature=JSON.stringify([item||null,officeState.offline]);if(panel.dataset.signature===signature)return;panel.dataset.signature=signature;panel.replaceChildren();
  if(!item){panel.append(el("span","eyebrow","SELECT A DESK"),el("h3","","See what is actually happening"),el("p","","Choose an occupied desk or a task in the list. Empty desks show capacity in the illustration; they are not running agents."));return;}
  panel.append(el("span","office-detail-status "+item.status,officeState.offline?"Offline · saved snapshot":officeStatusNames[item.status]),el("h3","",item.title),el("p","office-detail-message",item.message));
  const facts=el("dl","office-facts");for(const [k,v] of [["Company",item.companyName],[item.lastRun?"Last run phase":"Phase",item.phase],["Model",item.model||item.profile||"Not assigned"],["Review profile",item.reviewProfile||"Not assigned"],["Evidence",item.source],["Verified steps",`${item.completed||0} / ${item.total||0}`],["Attempts",String(item.attempts||0)]])facts.append(el("dt","",k),el("dd","",v));panel.append(facts);
  if(item.status==="stale")panel.append(el("p","office-warning","The process has no recent confirmed activity. Check its log; it may be waiting for a model or tool."));
  if(item.lastActivity)panel.append(el("p","",`Last observed activity: ${new Date(item.lastActivity*1000).toLocaleString("en-GB")}`));
  if(item.reviewEvidence){const e=item.reviewEvidence;panel.append(el("p","",`Review packet: ${(e.bytes/1024).toFixed(1)} KB · ${e.reads===null?"up to "+e.limits.extra_reads:e.reads+"/"+e.limits.extra_reads} extra reads`),el("p","missions-note",`Immutable snapshot ${e.snapshot}. Freshness is checked before acceptance.`));}
  if(item.decision){const d=item.decision;panel.append(el("p",d.status==="fallback"?"office-warning":"",`Selection: ${d.mode||"select"} / ${d.status} · ${d.choice||"plan order"}`),el("p","",d.reason||"Waiting for proposal."));}
  const actions=el("div","office-detail-actions");
  if(item.mission)actions.append(missionButton("Open live map",async()=>{$("#office-dialog").close();await showFlow(item.mission);}));
  if(item.run)actions.append(missionButton("Open run and approvals",async()=>{$("#office-dialog").close();await refreshState();await selectRun(item.run);focusApprovalInbox();}));
  // Same path as the dashboard: resets the company snapshot and refuses while a company form has unsaved changes.
  if(item.company)actions.append(missionButton("Open company",async()=>{await dashboardOpenCompany(item.company);$("#office-dialog").close();}));
  panel.append(actions);
}
function renderOffice(data){
  officeState.model=officeModel(data,officeState.company,officeState.logs);const model=officeState.model;
  $("#office-active-count").textContent=officeState.offline?"—":String(model.active);$("#office-attention-count").textContent=String(model.attention);$("#office-done-count").textContent=String(model.done);
  const signature=JSON.stringify(model.items.map(i=>[i.id,i.title,i.status,i.department]));
  if(signature!==officeState.signature){officeState.signature=signature;renderOfficeScene(model);}
  renderOfficeList();renderOfficeDetail();
  $("#office-empty-state").hidden=model.items.length>0;
  $("#office-sync").textContent=officeState.offline?"Offline — last snapshot may be outdated.":`Live · updated ${new Date(officeState.lastSuccess*1000).toLocaleTimeString("en-GB")}${officeState.warning?" · Controller warning: "+officeState.warning:""}`;
}
async function loadOffice(){
  if(!$("#office-dialog").open||officeState.loading)return;
  officeState.loading=true;const epoch=officeState.epoch;
  try{
    const [company,mission,stateData]=await Promise.all([api("/api/companies"),api("/api/missions"),api("/api/state")]);
    if(epoch!==officeState.epoch||!$("#office-dialog").open)return;
    // Controller errors are warnings: the data is still current and must stay visible.
    officeState.warning=[company.controller_error,mission.controller_error].filter(Boolean).join(" · ");
    const data={companies:company.companies,missions:mission.missions,runs:stateData.runs};
    const active=stateData.runs.filter(r=>["running","waiting","stopping"].includes(r.status));
    const ids=new Set(active.map(r=>r.id));for(const key of Object.keys(officeState.logs))if(!ids.has(key))delete officeState.logs[key];
    await Promise.all(active.slice(0,8).map(async run=>{
      const log=officeState.logs[run.id]||(officeState.logs[run.id]={offset:0,events:[],approvals:[],loading:true});
      for(let page=0;page<4;page++){
        const chunk=await api(`/api/events?run=${encodeURIComponent(run.id)}&offset=${log.offset}`);
        if(epoch!==officeState.epoch)return;
        log.offset=chunk.offset;log.approvals=chunk.approvals;log.loading=chunk.events.length>=600;
        log.events=log.events.concat(chunk.events).slice(-300);if(!log.loading)break;
      }
    }));
    if(epoch!==officeState.epoch||!$("#office-dialog").open)return;
    const select=$("#office-company"),key=JSON.stringify(data.companies.map(c=>[c.id,c.name]));
    if(select.dataset.key!==key){select.replaceChildren();const all=el("option","","All workspaces");all.value="";select.append(all);for(const c of data.companies){const option=el("option","",c.name);option.value=c.id;select.append(option);}select.dataset.key=key;}
    if(officeState.company&&!data.companies.some(c=>c.id===officeState.company))officeState.company="";
    select.value=officeState.company;officeState.data=data;officeState.lastSuccess=Date.now()/1000;officeState.offline=false;
    $("#office-dialog").classList.remove("office-offline");renderOffice(data);
  }catch(error){if(epoch===officeState.epoch){officeState.offline=true;$("#office-active-count").textContent="—";$("#office-dialog").classList.add("office-offline");$("#office-sync").textContent="Offline — last snapshot may be outdated. "+error.message;renderOfficeDetail();}}
  finally{officeState.loading=false;}
}
async function showOffice(){if(!$("#office-dialog").open)$("#office-dialog").showModal();officeState.epoch++;positionOfficeCamera();await loadOffice();}
function initOffice(){
  bind("#office-button","click",showOffice);bind("#office-refresh","click",loadOffice);
  bind("#office-company","change",e=>{officeState.epoch++;officeState.company=e.target.value;officeState.selected=null;officeState.department=null;officeState.signature="";if(officeState.data)renderOffice(officeState.data);return loadOffice();});
  bind("#office-clear-filter","click",()=>{officeState.department=null;renderOfficeList();});
  bind("#office-reset","click",()=>{officeState.angle=-28;officeState.tilt=55;officeState.zoom=1;positionOfficeCamera();});
  bind("#office-turn-left","click",()=>{officeState.angle-=15;positionOfficeCamera();});bind("#office-turn-right","click",()=>{officeState.angle+=15;positionOfficeCamera();});
  bind("#office-zoom-in","click",()=>{officeState.zoom=Math.min(1.6,officeState.zoom+.15);positionOfficeCamera();});bind("#office-zoom-out","click",()=>{officeState.zoom=Math.max(.6,officeState.zoom-.15);positionOfficeCamera();});
  bind("#office-layout","click",()=>{officeState.layout=officeState.layout==="3d"?"list":"3d";$("#office-dialog").classList.toggle("office-list-mode",officeState.layout==="list");$("#office-layout").textContent=officeState.layout==="list"?"Show 3D office":"List view";positionOfficeCamera();});
  $("#office-dialog").addEventListener("close",()=>{officeState.epoch++;});
  let drag=null;const stage=$("#office-stage");stage.addEventListener("pointerdown",e=>{if(e.target.closest("button,[role=button]"))return;drag={x:e.clientX,y:e.clientY,angle:officeState.angle,tilt:officeState.tilt,id:e.pointerId};stage.setPointerCapture(e.pointerId);});
  stage.addEventListener("pointermove",e=>{if(!drag)return;officeState.angle=drag.angle+(e.clientX-drag.x)*.25;officeState.tilt=Math.max(30,Math.min(75,drag.tilt-(e.clientY-drag.y)*.15));positionOfficeCamera();});
  const endDrag=()=>{drag=null;};stage.addEventListener("pointerup",endDrag);stage.addEventListener("pointercancel",endDrag);
  new ResizeObserver(positionOfficeCamera).observe(stage);
  setInterval(()=>{if(!document.hidden)loadOffice();},2500);
}
if(typeof document!=="undefined")document.addEventListener("DOMContentLoaded",initOffice);
