"use strict";
// Original Studio office view. No code or assets from Agents Office are used.
const officeDepartments = [
  {id:"operations",name:"Provoz",color:"#90e2bc",x:35,y:30},
  {id:"delivery",name:"Vývoj a dodávka",color:"#91bfff",x:325,y:30},
  {id:"growth",name:"Obchod a marketing",color:"#d7a0ed",x:615,y:30},
  {id:"finance",name:"Finance",color:"#edcc83",x:35,y:350},
  {id:"platform",name:"Platforma",color:"#79d8df",x:325,y:350},
  {id:"studio",name:"Projekty Studia",color:"#bdc7d9",x:615,y:350},
];
const officeStatusNames={working:"Pracuje",review:"Probíhá kontrola",verifying:"Probíhá ověřování",waiting:"Vyžaduje pozornost",blocked:"Blokováno",paused:"Pozastaveno",done:"Převzato",finished:"Běh dokončen",queued:"Ve frontě",idle:"Nečinný",stale:"Čeká na aktivitu",cancelled:"Zastaveno"};
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
    const lastIssue=(log?.events||[]).filter(e=>e.type==="error" || e.type==="note"&&/blocked:|denied:/i.test(e.text||"")).at(-1);
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
    items.push({id:m ? "mission:"+m.id : "task:"+company.id+":"+task.id,title:task.title||m?.title||"Úkol bez názvu",department,status,
      mission:m?.id,run:displayRun?.id,project:m?.project||task.project,company:company?.id,companyName:company?.name||"Studio",phase:run ? officeRunPhase(m,run) : lastAttempt?.phase||m?.phase||"queue",lastRun:!run&&Boolean(displayRun),
      model:displayRun?.model||null,profile:displayRun?.profile||m?.profile||company?.profile||null,reviewProfile:m?.review_profile||company?.review_profile||null,
      message:m?.message||task.goal||"Dosud nebylo spuštěno žádné provedení.",updated:m?.updated||task.created||null,
      attempts:m?.attempts?.length||0,completed:m?.tasks?.filter(t=>t.status==="done").length||0,total:m?.tasks?.length||0,
      approvals:log?.approvals?.length||0,summary:company?.purpose||"",source:run ? "Aktivní běh" : displayRun ? "Poslední zaznamenaný běh" : m ? "Uložené provedení" : "Uložený úkol firmy"});
  };
  for(const c of companies.filter(c=>!companyId||c.id===companyId))for(const t of c.tasks||[])append(t,c,byMission.get(t.last_mission));
  if(!companyId) {
    for(const m of missions)if(!covered.has(m.id))append({title:m.title,department:"studio"},null,m);
    for(const r of runs.filter(r=>!r.mission))items.push({id:"run:"+r.id,title:r.task?.split("\n")[0]?.slice(0,120)||"Běh agenta",department:"studio",status:officeItemStatus(null,r,logs[r.id],now),run:r.id,project:r.project,companyName:"Studio",phase:r.mode||"agent",model:r.model,profile:r.profile,message:r.reason||"Otevřete běh pro prozkoumání jeho zaznamenané aktivity.",updated:r.ended||r.created,source:"Zaznamenaný běh",completed:0,total:0,attempts:1,approvals:logs[r.id]?.approvals?.length||0});
  }
  const priority={blocked:0,waiting:1,working:2,review:2,verifying:2,stale:3,queued:4,paused:5,done:6,finished:6,cancelled:7};
  items.sort((a,b)=>(priority[a.status]??8)-(priority[b.status]??8)||(b.updated||0)-(a.updated||0)||a.id.localeCompare(b.id));
  return {items,departments:officeDepartments.map(d=>({...d,items:items.filter(i=>i.department===d.id)})),
    active:items.filter(i=>["working","review","verifying"].includes(i.status)).length,
    attention:items.filter(i=>["blocked","waiting","stale"].includes(i.status)).length,
    done:items.filter(i=>i.status==="done").length};
}
const officeState={company:"",selected:null,department:null,data:null,model:null,logs:{},loading:false,epoch:0,lastSuccess:0,signature:"",angle:-28,tilt:55,zoom:1,layout:"3d",offline:false};
function officeBox(parent,x,y,z,w,d,h,color,cls="") {
  const box=el("div","office-box "+cls);box.setAttribute("aria-hidden","true");
  box.style.cssText=`--x:${x}px;--y:${y}px;--z:${z}px;--w:${w}px;--d:${d}px;--h:${h}px;--box-color:${color}`;
  for(const face of ["top","north","south","east","west"])box.append(el("i","office-face "+face));parent.append(box);return box;
}
function officeSeat(parent,item,x,y,color) {
  const seat=el("div","office-seat "+(item?.status||"empty"));seat.style.cssText=`--sx:${x}px;--sy:${y}px;--accent:${color}`;seat.dataset.noContextHelp="true";
  // Desk, legs, screen and chair are independent CSS 3D solids.
  officeBox(seat,0,0,33,88,48,5,"#586671");
  for(const [a,b] of [[5,5],[77,5],[5,37],[77,37]])officeBox(seat,a,b,0,5,5,33,"#303b48");
  officeBox(seat,27,8,38,34,6,22,item ? color:"#364351","office-monitor");
  officeBox(seat,30,27,39,30,13,1,"#243442");
  officeBox(seat,31,60,15,28,25,4,"#3e4d5b");officeBox(seat,32,82,17,26,5,27,"#465767");
  if(item){
    const person=el("div","office-person");
    officeBox(person,36,59,19,19,18,29,color,"office-torso");
    officeBox(person,37,58,49,17,17,17,"#d4af93","office-head");
    officeBox(person,32,44,33,6,22,6,color,"office-arm");officeBox(person,54,44,33,6,22,6,color,"office-arm");
    seat.append(person);
    const button=el("button","office-agent",item.title);button.type="button";button.dataset.noContextHelp="true";
    button.setAttribute("aria-label",`${item.title} — ${officeStatusNames[item.status]}. Otevřít podrobnosti`);button.dataset.officeItem=item.id;button.onclick=()=>selectOfficeItem(item.id);seat.append(button);
    const badge=el("span","office-seat-signal",item.status==="blocked"?"!":item.status==="waiting"?"?":"");badge.setAttribute("aria-hidden","true");seat.append(badge);
  }
  parent.append(seat);
}
function renderOfficeScene(model) {
  const world=$("#office-world");world.replaceChildren();
  officeBox(world,0,0,-16,900,620,16,"#202c3b","office-foundation");
  officeBox(world,0,0,0,900,8,52,"#344255");officeBox(world,0,0,0,8,620,52,"#344255");
  // A central walkway connects six department areas.
  const walkway=el("div","office-walkway");walkway.textContent="S W I T C H   /   S T U D I O";world.append(walkway);
  for(const d of model.departments){
    const pod=el("div","office-pod");pod.style.cssText=`--px:${d.x}px;--py:${d.y}px;--accent:${d.color}`;pod.dataset.department=d.id;
    const label=el("button","office-department-label",d.name);label.type="button";label.dataset.noContextHelp="true";label.setAttribute("aria-label",`Zobrazit úkoly: ${d.name}`);label.onclick=()=>{officeState.department=officeState.department===d.id?null:d.id;renderOfficeList();};pod.append(label);
    const cap=el("span","office-pod-count",`${d.items.length} ${d.items.length===1?"úkol":d.items.length>=2&&d.items.length<=4?"úkoly":"úkolů"}`);pod.append(cap);
    for(let n=0;n<4;n++)officeSeat(pod,d.items[n],(n%2)*122,40+Math.floor(n/2)*100,d.color);
    // Plant and low department partition, built from original geometry.
    officeBox(pod,232,140,0,12,12,15,"#705e52");officeBox(pod,230,138,15,16,16,20,"#52876d");
    world.append(pod);
  }
  positionOfficeCamera();
}
function positionOfficeCamera(){
  const stage=$("#office-stage");if(!stage)return;
  const fit=Math.min((stage.clientWidth-35)/1050,(stage.clientHeight-45)/650);
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
  $("#office-roster-title").textContent=officeState.department ? officeDepartments.find(d=>d.id===officeState.department).name+" · všechny úkoly" : "Veškerá práce";
  if(!items.length)list.append(el("p","office-empty","Zde není přiřazena žádná práce. Přidejte úkol v oddílech Firma nebo AI projekty."));
  for(const item of items){const row=el("button","office-roster-item "+item.status);row.type="button";row.dataset.noContextHelp="true";row.setAttribute("aria-pressed",String(item.id===officeState.selected));
    row.append(el("span","office-state-dot"),el("strong","",item.title),el("small","",`${officeStatusNames[item.status]} · ${item.companyName}`));row.onclick=()=>selectOfficeItem(item.id);list.append(row);}
  list.scrollTop=scroll;if(hadFocus)list.querySelector('[aria-pressed="true"]')?.focus({preventScroll:true});
  $("#office-clear-filter").hidden=!officeState.department;
  for(const b of $$("[data-office-item]"))b.setAttribute("aria-pressed",String(b.dataset.officeItem===officeState.selected));
}
function renderOfficeDetail(){
  const panel=$("#office-detail"),item=officeState.model?.items.find(i=>i.id===officeState.selected);
  const signature=JSON.stringify([item||null,officeState.offline]);if(panel.dataset.signature===signature)return;panel.dataset.signature=signature;panel.replaceChildren();
  if(!item){panel.append(el("span","eyebrow","VYBERTE STŮL"),el("h3","","Podívejte se, co se skutečně děje"),el("p","","Vyberte obsazený stůl nebo úkol ze seznamu. Prázdné stoly ukazují kapacitu na ilustraci; nejsou to běžící agenti."));return;}
  panel.append(el("span","office-detail-status "+item.status,officeState.offline?"Offline · uložený snímek":officeStatusNames[item.status]),el("h3","",item.title),el("p","office-detail-message",item.message));
  const facts=el("dl","office-facts");for(const [k,v] of [["Firma",item.companyName],[item.lastRun?"Poslední fáze běhu":"Fáze",({plan:"Příprava",build:"Realizace",review:"Kontrola",final:"Kontrola produktu",queue:"Fronta",react:"Samostatný agent",team:"Tým agentů",agent:"Agent"})[item.phase]||item.phase],["Model",item.model||item.profile||"Nepřiřazeno"],["Profil kontrolora",item.reviewProfile||"Nepřiřazeno"],["Důkazy",item.source],["Ověřené kroky",`${item.completed||0} / ${item.total||0}`],["Pokusy",String(item.attempts||0)]])facts.append(el("dt","",k),el("dd","",v));panel.append(facts);
  if(item.status==="stale")panel.append(el("p","office-warning","Proces nemá žádnou nedávnou potvrzenou aktivitu. Zkontrolujte jeho protokol; může čekat na model nebo nástroj."));
  const actions=el("div","office-detail-actions");
  if(item.mission)actions.append(missionButton("Otevřít živou mapu",async()=>{$("#office-dialog").close();await showFlow(item.mission);}));
  if(item.run)actions.append(missionButton("Otevřít běh a schválení",async()=>{$("#office-dialog").close();await refreshState();await selectRun(item.run);focusApprovalInbox();}));
  if(item.company)actions.append(missionButton("Otevřít firmu",async()=>{$("#office-dialog").close();companySelection=item.company;await showCompanies();}));
  panel.append(actions);
}
function renderOffice(data){
  officeState.model=officeModel(data,officeState.company,officeState.logs);const model=officeState.model;
  $("#office-active-count").textContent=officeState.offline?"—":String(model.active);$("#office-attention-count").textContent=String(model.attention);$("#office-done-count").textContent=String(model.done);
  const signature=JSON.stringify(model.items.map(i=>[i.id,i.title,i.status,i.department]));
  if(signature!==officeState.signature){officeState.signature=signature;renderOfficeScene(model);}
  renderOfficeList();renderOfficeDetail();
  $("#office-empty-state").hidden=model.items.length>0;
  $("#office-sync").textContent=officeState.offline?"Offline — poslední snímek může být zastaralý.":`Aktuální · obnoveno ${new Date(officeState.lastSuccess*1000).toLocaleTimeString("cs-CZ")}`;
}
async function loadOffice(){
  if(!$("#office-dialog").open||officeState.loading)return;
  officeState.loading=true;const epoch=officeState.epoch;
  try{
    const [company,mission,stateData]=await Promise.all([api("/api/companies"),api("/api/missions"),api("/api/state")]);
    if(epoch!==officeState.epoch||!$("#office-dialog").open)return;
    if(company.controller_error||mission.controller_error)throw new Error(company.controller_error||mission.controller_error);
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
    if(select.dataset.key!==key){select.replaceChildren();const all=el("option","","Všechny pracovní prostory");all.value="";select.append(all);for(const c of data.companies){const option=el("option","",c.name);option.value=c.id;select.append(option);}select.dataset.key=key;}
    if(officeState.company&&!data.companies.some(c=>c.id===officeState.company))officeState.company="";
    select.value=officeState.company;officeState.data=data;officeState.lastSuccess=Date.now()/1000;officeState.offline=false;
    $("#office-dialog").classList.remove("office-offline");renderOffice(data);
  }catch(error){if(epoch===officeState.epoch){officeState.offline=true;$("#office-active-count").textContent="—";$("#office-dialog").classList.add("office-offline");$("#office-sync").textContent="Offline — poslední snímek může být zastaralý. "+error.message;renderOfficeDetail();}}
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
  bind("#office-layout","click",()=>{officeState.layout=officeState.layout==="3d"?"list":"3d";$("#office-dialog").classList.toggle("office-list-mode",officeState.layout==="list");$("#office-layout").textContent=officeState.layout==="list"?"Zobrazit 3D kancelář":"Zobrazit seznam";positionOfficeCamera();});
  $("#office-dialog").addEventListener("close",()=>{officeState.epoch++;});
  let drag=null;const stage=$("#office-stage");stage.addEventListener("pointerdown",e=>{if(e.target.closest("button,[role=button]"))return;drag={x:e.clientX,y:e.clientY,angle:officeState.angle,tilt:officeState.tilt,id:e.pointerId};stage.setPointerCapture(e.pointerId);});
  stage.addEventListener("pointermove",e=>{if(!drag)return;officeState.angle=drag.angle+(e.clientX-drag.x)*.25;officeState.tilt=Math.max(30,Math.min(75,drag.tilt-(e.clientY-drag.y)*.15));positionOfficeCamera();});
  const endDrag=()=>{drag=null;};stage.addEventListener("pointerup",endDrag);stage.addEventListener("pointercancel",endDrag);
  new ResizeObserver(positionOfficeCamera).observe(stage);
  setInterval(()=>{if(!document.hidden)loadOffice();},2500);
}
if(typeof document!=="undefined")document.addEventListener("DOMContentLoaded",initOffice);
