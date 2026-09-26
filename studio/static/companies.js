"use strict";
let companySelection = null, companyData = null, companyLoading = false, companyMutating = false;
let companySubmitting = false;
let companyDirty = false, driverEpoch = 0, companySnapshot = "";
const companyStatus = {active:"Driver running", paused:"Paused", queued:"In queue", scheduled:"Waiting for deadline", done:"Completed", needs_owner:"Owner's decision", rejected:"Rejected", running:"In progress", waiting:"Waiting for reply", blocked:"Blocked", ready:"Ready for acceptance", awaiting_checks:"Missing checks", expired:"Time expired", cancelled:"Cancelled", awaiting_plan:"Ready plan", verifying:"Verification"};
function companyButton(label, fn, primary = false) { return missionButton(label, fn, primary); }
function companyField(form, label, value = "", type = "text") {
  const wrapper = el("label", "", label), input = el(type === "textarea" ? "textarea" : "input");
  if (type !== "textarea") input.type = type; else { input.rows = 3; input.maxLength = 12000; }
  if (type === "checkbox") { input.checked = Boolean(value); wrapper.className = "mission-permission"; wrapper.prepend(input); }
  else { input.value = value; wrapper.append(input); }
  input.oninput = () => { companyDirty = true; form.dataset.dirty = "true"; };
  form.append(wrapper); return input;
}
function companySelect(form, label, options, multiple = false) {
  const wrapper = el("label", "", label), select = el("select"); select.multiple = multiple;
  if (multiple) select.size = Math.min(6, Math.max(2, options.length));
  for (const [value, name] of options) { const opt = el("option", "", name); opt.value = value; select.append(opt); }
  select.onchange = () => { companyDirty = true; form.dataset.dirty = "true"; }; wrapper.append(select); form.append(wrapper); return select;
}
function companySubmit(form, label, fn) {
  const error = el("p", "mission-feedback"); error.setAttribute("role", "alert"); error.hidden = true;
  const button = el("button", "button primary", label); button.type = "submit"; form.append(error, button);
  const showError = message => { error.textContent = message; error.hidden = false; };
  form.onsubmit = async e => { e.preventDefault(); if (companyMutating || companySubmitting) return;
    error.textContent = ""; error.hidden = true;
    if ($$("#driver-dialog form").some(other => other !== form && other.dataset.dirty === "true")) { showError("Another form is also in progress. First complete its changes, or use Refresh overview to discard the forms."); return; }
    button.disabled = true;
    try { companySubmitting = true; await fn(); } catch (failure) { showError(failure.message); } finally { companySubmitting = false; button.disabled = false; } };
}
function companyPanel(parent, title) {
  const details = el("details", "product-form-panel"), form = el("form"); details.dataset.key = "form:" + title;
  details.append(el("summary", "", title), form); parent.append(details); return form;
}
// Driver recovery events are recorded as "Driver recovery <n>/<max>: <reason>".
function companyRecoveryEvent(e) { return /^Driver recovery\b/i.test(e.message || "") || /recover/i.test(e.action || ""); }
async function companyAction(c, action, extra = {}) {
  if (companyMutating) return;
  if (companyDirty && !companySubmitting && action !== "pause") throw new Error("First save the form or refresh the overview.");
  const preserveDraft = companyDirty && action === "pause";
  companyMutating = true; driverEpoch++;
  try {
    const saved = await api("/api/companies/action", {id:c.id, revision:c.revision, action, ...extra});
    if (preserveDraft) {
      Object.assign(c, saved);
      $("#driver-toggle").textContent = "Start Driver";
      $("#driver-state").textContent = "Paused";
      $("#driver-message").textContent = saved.message;
      const nav = $("#driver-company-" + c.id); if (nav) nav.textContent = `${c.name} · Paused`;
      return;
    }
    companyDirty = false; companySnapshot = "";
    $$("#driver-dialog form").forEach(f => { delete f.dataset.dirty; });
  } finally { companyMutating = false; }
  await loadCompanies();
}
async function openCompanyMission(projectId, missionId) {
  if (companyDirty) throw new Error("First save company changes or restore the form.");
  if (state.dirty) throw new Error("First save the open file.");
  $("#driver-dialog").close();
  // Mission view is workspace-scoped. Use the existing project switch path.
  if (state.project !== projectId) await selectProject(projectId);
  if (state.project !== projectId) return;
  missionViewProject = projectId; missionSelection = missionId; missionSnapshot = "";
  await showMissions();
}
function companyLineList(input) { return input.value.split("\n").map(x => x.trim()).filter(Boolean); }
function renderDriverCompany(c, data) {
  const panel = $("#driver-detail");
  keepPanelState(panel, "company:" + (c?.id || ""), () => { panel.replaceChildren(); buildDriverCompany(panel, c, data); });
}
function buildDriverCompany(panel, c, data) {
  if (!c) { panel.append(el("h3", "", "Create your AI company"), el("p", "", "Select projects, set goals, and add the first task. Driver builds upon executions and their verified results.")); return; }
  const stateChip=el("span", "mission-state", companyStatus[c.status]);stateChip.id="driver-state";
  panel.append(el("h2", "", c.name), stateChip, el("p", "", c.purpose));
  const metrics = el("div", "driver-metrics");
  for (const [n, label] of [[c.projects.length,"projects"],[c.usage.used_runs,"runs used"],[c.usage.reserved_runs,"runs reserved"],[`${c.cycles.length}/${c.policy.cycle_limit}`,"executions"]]) {
    const box = el("div"); box.append(el("strong", "", String(n)), el("small", "", label)); metrics.append(box);
  }
  const message=el("p", "mission-message", c.message);message.id="driver-message";panel.append(metrics, message);
  panel.append(el("p", "missions-note", c.deadline ? `Time horizon until ${new Date(c.deadline*1000).toLocaleString("en-GB")}` : "Horizon starts with enabling Driver."));
  const recovery = c.events.filter(companyRecoveryEvent);
  if (recovery.length) {
    const box = el("div", "product-work-item"); box.append(el("strong", "", "Driver recovery"));
    for (const e of recovery.slice(0, 5)) box.append(el("p", "mission-feedback", `${new Date(e.at*1000).toLocaleString("en-GB")} · ${e.message || e.action}`));
    panel.append(box);
  }
  const actions = el("div", "mission-actions");
  // Only this company's own executions; never fall back to an unrelated mission.
  const flowMission = c.tasks.find(t => t.status === "running")?.last_mission || c.cycles.at(-1)?.mission;
  const flowButton = companyButton(flowMission ? "Live progress map" : "Live progress map · no execution yet", () => showFlow(flowMission));
  flowButton.disabled = !flowMission; actions.append(flowButton);
  const toggle=companyButton(c.status === "active" ? "Pause Driver" : "Start Driver", () => {
    if (companyDirty && c.status !== "active") throw new Error("First save the partially filled form or restore it.");
    return companyAction(c, c.status === "active" ? "pause" : "start");
  }, true);toggle.id="driver-toggle";
  actions.append(toggle, companyButton("Download report .md", async () => {
    const r = await api(`/api/companies/report?id=${c.id}`), url = URL.createObjectURL(new Blob([r.text], {type:"text/markdown;charset=utf-8"}));
    const a = el("a"); a.href = url; a.download = `company-${c.id}.md`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  })); panel.append(actions);
  const map = el("div", "driver-map"); map.append(el("strong", "", "You · goals and decisions"), el("span", "", "↓ Company Driver · priority, limits, deadlines"), el("span", "", "↓ Department → projects → worker → reviewer → checks → acceptance ↻")); panel.append(map);
  panel.append(el("h3", "", "Portfolio"));
  for (const key of c.projects) {
    const project = state.data.projects.find(p=>p.id===key), row = el("div", "product-work-item");
    row.append(el("strong", "", project?.name || key), el("p", "missions-note", project?.path || ""));
    for (const p of c.products.filter(p=>p.project===key)) row.append(el("p", "", `${p.title} · ${p.releases} accepted versions · ${p.status}`));
    panel.append(row);
  }
  const projectForm = companyPanel(panel,"Attach another open project");
  const projects = companySelect(projectForm,"Project",state.data.projects.filter(p=>!c.projects.includes(p.id)).map(p=>[p.id,p.name]));
  companySubmit(projectForm,"Attach",()=>companyAction(c,"add_project",{project:projects.value}));
  panel.append(el("h3", "", "Decisions and blocks"));
  if (!c.inbox.length && !c.tasks.some(t=>t.status==="needs_owner")) panel.append(el("p", "missions-note", "No open decision."));
  for (const item of c.inbox) {
    const row = el("div","product-work-item"); row.append(el("strong","",item.title),el("p","",`${companyStatus[item.status] || item.status}: ${item.message}`));
    for (const q of item.questions) row.append(el("p","mission-feedback",q.question));
    row.append(companyButton("Open execution and reply",()=>openCompanyMission(item.project,item.mission))); panel.append(row);
  }
  panel.append(el("h3", "", "Department work and repetition"));
  for (const t of c.tasks) {
    const row = el("div", "product-work-item"); row.id = `company-task-${t.id}`; row.tabIndex = -1;
    row.append(el("strong", "", t.title), el("p", "missions-note", `${data.departments[t.department]} · ${companyStatus[t.status] || t.status} · priority ${t.priority}${t.enabled ? "" : " · disabled"}`));
    const lastRecovery = recovery.find(e => e.task === t.id);
    if (lastRecovery && !["done","queued","scheduled"].includes(t.status)) row.append(el("p", "mission-feedback", `Last recovery: ${lastRecovery.message || lastRecovery.action}`));
    const detail = el("details"); detail.dataset.key = "task:" + t.id; detail.append(el("summary", "", "Task brief, criteria, and dependencies"),el("p","",t.goal));
    const criteria = el("ul"); criteria.append(...t.criteria.map(v=>el("li","",v))); detail.append(criteria);
    detail.append(el("p","missions-note",`Repetition: ${t.interval_hours ? `${t.interval_hours} h, at most ${t.max_cycles}×` : "one-time"}. Dependencies: ${t.depends_on.map(id=>c.tasks.find(x=>x.id===id)?.title || id).join(", ") || "none"}.`));
    if (t.status === "scheduled") detail.append(el("p","",`Next deadline: ${new Date(t.due*1000).toLocaleString("en-GB")}`));
    if (t.outcome) detail.append(el("p","",t.outcome)); row.append(detail);
    if (t.last_mission) row.append(companyButton("Progress and evidence",()=>openCompanyMission(t.project,t.last_mission)));
    if (t.kind === "external" && t.status === "needs_owner") {
      const f = el("form"), note = companyField(f,"Proof of execution or reason for rejection","","textarea"); note.required=true;
      const outcome = companySelect(f,"Result",[["done","Executed by owner"],["rejected","Rejected"]]);
      companySubmit(f,"Record result — nothing is sent",()=>companyAction(c,"resolve_external",{task:t.id,note:note.value,outcome:outcome.value}));row.append(f);
    } else if (["queued","scheduled","done","cancelled","expired"].includes(t.status)) {
      // The server requeues only agent work whose execution was cancelled or expired.
      if (t.kind === "work" && ["cancelled","expired"].includes(t.status)) row.append(companyButton("Requeue",()=>companyAction(c,"requeue_task",{task:t.id}),true));
      row.append(companyButton(t.enabled?"Disable rule":"Enable rule",()=>companyAction(c,t.enabled?"disable_task":"enable_task",{task:t.id})));
    }
    panel.append(row);
  }
  const f = companyPanel(panel, "+ Add task or recurring assignment");
  const preset = companySelect(f,"Default task",[["custom","Personal task"],["plan","Builder: project development plan"],["health","Driver: regular project audit"],["sales","Sales: offer preparation"],["finance","Finance: overview of provided inputs"]]);
  const title = companyField(f,"Task name");title.required=true;title.maxLength=160;
  const goal = companyField(f,"The expected outcome","","textarea");goal.required=true;
  const criteria = companyField(f,"Acceptance conditions, one per line","","textarea");criteria.required=true;
  const project = companySelect(f,"Working project",c.projects.map(id=>[id,state.data.projects.find(p=>p.id===id)?.name || id]));
  const dept = companySelect(f,"Responsible department",Object.entries(data.departments));
  const kind = companySelect(f,"Method of execution",[["work","Agent's work in the project"],["external","External step — hand over to owner"]]);
  const deps = companySelect(f,"Run only after completion (Ctrl/Cmd for multiple)",c.tasks.map(t=>[t.id,t.title]),true);
  const priority = companySelect(f,"Priority",[["1","1 · highest"],["2","2 · normal"],["3","3 · lower"]]);priority.value="2";
  const hours = companyField(f,"Repeat every hours (0 = one-time)",0,"number");hours.min=0;hours.max=8760;
  const cycles = companyField(f,"Maximum executions of this rule",1,"number");cycles.min=1;cycles.max=100;
  const checks = companyField(f,"Independent verification commands, one per line","","textarea");
  const sources = companyField(f,"Context and links (no passwords)","","textarea");
  preset.onchange = () => {
    companyDirty=true; f.dataset.dirty="true";
    const defaults = {
      plan:["Project development plan","Assess the actual project state. Save company-plan.md with the goal, current capabilities, priorities, specific tasks, dependencies, and questions for the owner. Do not invent outcomes or customers.","company-plan.md exists.\nEach statement about the current state has a source or is marked as unverified.","operations"],
      health:["Regular project audit","Review the project and available local checks. Save operations-report.md with results, documented errors, blockages, and proposed next steps. Do not perform external changes.","operations-report.md exists.\nThe report distinguishes executed checks and unverified areas.","platform"],
      sales:["Proposal draft","From the provided input, prepare proposal.md: customer needs, scope of delivery, outputs, open questions, and price assumptions. Do not use invented references or prices. Do not send anything.","proposal.md exists.\nMissing data is marked for completion.","growth"],
      finance:["Cost and obligation overview","From the provided input, create finance-report.md with costs, obligations, date, and source for each figure. Mark missing data. Do not make payments.","finance-report.md exists.\nEach figure has a traceable source.","finance"]};
    // Preset goals ask for reports; the model must write them in English.
    if (defaults[preset.value]) [title.value,goal.value,criteria.value,dept.value]=defaults[preset.value].map((v,i)=>i===1?v+" Write all reports, questions, summaries, notes and generated documentation in English, regardless of the language of the input.":v);
  };
  companySubmit(f,"Add to queue",()=>companyAction(c,"add_task",{title:title.value,goal:goal.value,criteria:companyLineList(criteria),project:project.value,department:dept.value,kind:kind.value,priority:Number(priority.value),depends_on:Array.from(deps.selectedOptions,o=>o.value),interval_hours:Number(hours.value),max_cycles:Number(cycles.value),verification_checks:checks.value,sources:sources.value}));
  const settings = companyPanel(panel,"Driver limits and permissions");
  const fields = {};
  for (const [key,label,min,max] of [["days","Time horizon in days",1,365],["run_budget","Total budget for runs (not money)",2,100000],["cycle_limit","Total executions",1,1000],["attempts_per_cycle","Runs for execution",2,1000],["attempt_minutes","Minutes per run",1,360],["max_turns","Steps per run",1,200]]) {
    fields[key] = companyField(settings,label,c.policy[key],"number");fields[key].min=min;fields[key].max=max;
  }
  const tools = companyField(settings,"Allow tools without individual approval—even in incomplete executions",c.policy.auto_tools,"checkbox");
  const accept = companyField(settings,"Automatically accept the result after successful independent checks",c.policy.auto_accept,"checkbox");
  const renew = companyField(settings,"On next startup, begin a new time horizon (consumption is not reset)",false,"checkbox");
  settings.append(el("p","missions-note","Settings apply while Driver is paused. Workers run in a Linux bubblewrap sandbox limited to the working copy; hosts without bubblewrap cannot run workers. Automatic tools can still change project files without per-action approval. Limits do not restrict the provider account or other applications. External systems are not connected."));
  companySubmit(settings,"Save limits",()=>companyAction(c,"settings",{...Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,Number(v.value)])),auto_tools:tools.checked,auto_accept:accept.checked,renew_horizon:renew.checked}));
  const history=el("details");history.dataset.key="history";history.append(el("summary","","Driver history (last 100 events)"));
  const unfinished=c.cycles.filter(x=>!["accepted","cancelled","expired"].includes(x.status));
  if(c.status==="paused" && unfinished.length){
    const reservation=companyPanel(panel,"Assign additional runs to incomplete execution");
    const cycle=companySelect(reservation,"Execution",unfinished.map(x=>[x.mission,c.tasks.find(t=>t.id===x.task)?.title||x.mission]));
    const maximum=companyField(reservation,"New total execution run limit",c.policy.attempts_per_cycle+10,"number");maximum.min=2;maximum.max=1000;
    reservation.append(el("p","missions-note",`Remaining ${c.usage.remaining_runs} unassigned runs. The company's total budget remains unchanged.`));
    companySubmit(reservation,"Increase reservation",()=>companyAction(c,"reserve_runs",{mission:cycle.value,max_attempts:Number(maximum.value)}));
  }
  for(const e of c.events) history.append(el("p","missions-note",`${new Date(e.at*1000).toLocaleString("en-GB")} · ${e.action} · ${e.message || e.mission || ""}`));panel.append(history);
}
async function loadCompanies(force = false) {
  if (!$("#driver-dialog").open || companyLoading || companyMutating) return;
  const epoch = driverEpoch; companyLoading = true;
  try {
    const data = await api("/api/companies");
    if (epoch !== driverEpoch || !$("#driver-dialog").open) return;
    const unavailable = executionUnavailable();
    $("#driver-health").textContent = (unavailable ? `Agent execution is unavailable on this host: ${unavailable} ` : "") + (data.controller_error || "Observe → select ready work → execute → verify → accept → repeat. One work slot, persistent state in SQLite.");
    if (companyDirty && !force) { $("#driver-health").textContent += " Unsaved form changes; the display was not refreshed."; return; }
    const snapshot=JSON.stringify(data);
    if (!force && companySnapshot===snapshot) return;
    companyData=data; companySnapshot=snapshot;
    if (!data.companies.some(c=>c.id===companySelection)) companySelection=data.companies[0]?.id || null;
    const list=$("#driver-list");list.replaceChildren();
    for (const c of data.companies) { const button=companyButton(`${c.name} · ${companyStatus[c.status]}`,()=>{
      if(companyDirty) throw new Error("First save the form or refresh the overview.");
      companySelection=c.id;companySnapshot="";return loadCompanies(true);
    }, c.id===companySelection);button.id="driver-company-"+c.id;list.append(button); }
    renderDriverCompany(data.companies.find(c=>c.id===companySelection),data);
  } finally { companyLoading=false; }
}
async function showCompanies(force = false) {
  const latest=await api("/api/state");state.data.projects=latest.projects;
  const form=$("#driver-create");
  if (!form.childElementCount) {
    const name=companyField(form,"Company name","My company");name.required=true;name.maxLength=160;
    const purpose=companyField(form,"Company goals and measurable outcomes","Manage delivery and maintenance of My Company projects. Prioritize verified work, uncover blockages, and prepare owner decisions.","textarea");purpose.required=true;
    const projects=companySelect(form,"Projects (Ctrl/Cmd for multiple)",latest.projects.map(p=>[p.id,p.name]),true);projects.required=true;
    for(const opt of projects.options) opt.selected=opt.value===state.project;
    const profiles=runnableProfiles(latest.profiles).map(p=>[p.id,`${p.model} · ${p.id}`]);
    const profile=companySelect(form,"Worker and scheduler",profiles),review=companySelect(form,"Reviewer",profiles);
    profile.value=defaultModelForRole("work",latest.profiles);
    review.value=defaultModelForRole("review",latest.profiles);
    // A default that cannot run (OAuth/Codex) is not offered; fall back to the first runnable model.
    for(const select of [profile,review])if(!select.value&&select.options.length)select.selectedIndex=0;
    companySubmit(form,"Create company",async()=>{
      if(companyMutating)return;companyMutating=true;driverEpoch++;
      try {
        const c=await api("/api/companies",{name:name.value,purpose:purpose.value,projects:Array.from(projects.selectedOptions,o=>o.value),profile:profile.value,review_profile:review.value});
        companySelection=c.id;companyDirty=false;companySnapshot="";delete form.dataset.dirty;$("#driver-new").open=false;
      } finally { companyMutating=false; }
      await loadCompanies(true);
    });
  }
  if (!$("#driver-dialog").open) $("#driver-dialog").showModal();
  await loadCompanies(force);
}
function initCompanies() {
  bind("#driver-button","click",()=>showCompanies());
  bind("#driver-refresh","click",()=>{
    if(companyDirty && !confirm("Discard unsaved form changes and reload the current state?"))return;
    // Discarding must clear every draft flag, or other forms stay blocked.
    companyDirty=false;driverEpoch++;companySnapshot="";
    $$("#driver-dialog form").forEach(f=>{delete f.dataset.dirty;});
    $("#driver-create").replaceChildren();return showCompanies(true);
  });
  setInterval(()=>loadCompanies().catch(e=>toast(e.message,true)),3000);
}

// Global inbox: independent of the selected project, run and open company dialog.
const approvalInboxCards = new Map(), approvalInboxOffsets = new Map();
let approvalInboxLoading = false, approvalInboxEpoch = 0, approvalInboxStarted = false;
let approvalInboxCompanies = [], approvalInboxCompaniesAt = 0;
const approvalStallTitles = {blocked:"Work is blocked", ready:"Result is ready for your acceptance", awaiting_checks:"Independent checks are missing", expired:"Time limit expired"};
// Mirrors missions.recovery_question: the controller's or Driver's block question, not a model question.
// Answering it resumes the blocked work on the server unless the answer declines.
const approvalLegacyFailureQuestion = "How should I adjust the approach before restarting the task?";
const approvalRecoveryQuestion = q => ["failure","recovery"].includes(q.kind)
  || (!q.kind && !q.task && q.question === approvalLegacyFailureQuestion);
function approvalInboxItems(runs, missions, companies = []) {
  const items = [];
  for (const run of runs) for (const request of run.approvals || []) {
    if (!request.id) continue;
    items.push({key:`tool/${run.id}/${request.id}`, kind:"tool", run:run.id,
      context:run.title, request});
  }
  for (const m of missions) {
    if (["accepted","cancelled","expired"].includes(m.status)) continue;
    const open = (m.questions || []).filter(q=>q.answer === null);
    for (const q of open) items.push({
      key:`question/${m.id}/${q.id}`, kind:q.kind === "readiness" ? "readiness" : "question", mission:m.id, project:m.project, question:q.id,
      context:m.title, title:q.question, reason:q.reason, blocked:m.status === "blocked", recovery:approvalRecoveryQuestion(q)});
    if (m.status === "awaiting_plan" && !open.length) items.push({
      key:`plan/${m.id}`, kind:"plan", mission:m.id, project:m.project, context:m.title,
      title:"Approve plan and start work?", tasks:m.tasks});
    // Stalls without a question still wait for the owner; never leave the inbox empty while work is stuck.
    if (!open.length && ["blocked","ready","awaiting_checks"].includes(m.status)) items.push({
      key:`${m.status}/${m.id}`, kind:m.status, mission:m.id, project:m.project, context:m.title,
      title:approvalStallTitles[m.status], reason:m.message, driver:Boolean(m.company_context)});
  }
  for (const c of companies || []) for (const t of c.tasks || []) {
    if (t.enabled === false || t.status !== "expired" || t.kind === "external") continue;
    items.push({key:`expired/${c.id}/${t.id}/${t.last_mission || ""}`, kind:"expired", company:c.id, task:t.id,
      context:`${c.name} · ${t.title}`, title:approvalStallTitles.expired,
      reason:"The execution ran out of time before acceptance. Requeue the task to try again within the company limits, or disable it."});
  }
  return items;
}
function focusApprovalInbox() {
  $("#dashboard-approval-slot")?.classList.add("show-empty");
  // Closing a dialog preserves its form DOM and drafts.
  $$("dialog[open]").forEach(dialog=>dialog.close());
  $("#approval-inbox").scrollIntoView({block:"nearest"});
  $("#approval-inbox").focus();
}
function approvalInboxBadges(count) {
  $("#dashboard-approval-slot")?.classList.toggle("has-approvals", count > 0);
  $("#approval-jump").hidden = count === 0;
  $("#approval-count").textContent = String(count);
  $("#approval-total").textContent = String(count);
  $("#approval-inbox").classList.toggle("needs-attention", count > 0);
  $("#approval-heading").textContent = count ? "Awaiting your decision" : "Approvals and responses";
  document.title = count ? `(${count}) Waiting for you · Switch Studio` : "Switch Studio";
  for (const dialog of $$("dialog[open]")) {
    let shortcut = dialog.querySelector(".approval-dialog-shortcut");
    if (!shortcut) {
      shortcut = el("button", "button approval-dialog-shortcut"); shortcut.type = "button";
      shortcut.onclick = focusApprovalInbox; dialog.prepend(shortcut);
    }
    shortcut.hidden = count === 0;
    shortcut.textContent = `Waiting for you ${count} · Open approval →`;
  }
}
// Runs one inbox action; the card disappears only after the server accepted it.
async function approvalInboxAct(item, card, controls, error, fn) {
  if (card.dataset.busy === "true") return;
  error.hidden = true;
  card.dataset.busy = "true"; controls.forEach(b=>b.disabled=true); approvalInboxEpoch++;
  try {
    await fn();
    approvalInboxCards.delete(item.key); card.remove(); approvalInboxEpoch++; approvalInboxCompaniesAt = 0;
    await pollApprovalInbox();
  } catch (failure) { error.textContent = failure.message; error.hidden = false; }
  finally { card.dataset.busy = "false"; controls.forEach(b=>b.disabled=false); }
}
async function approvalCompanyTask(item, action) {
  // Company actions need the latest revision; a stale one is rejected by the server.
  const latest = await api("/api/companies"), c = latest.companies.find(x=>x.id === item.company);
  if (!c) throw new Error("The company no longer exists.");
  await api("/api/companies/action", {id:c.id, revision:c.revision, action, task:item.task});
}
// The server already resumes a blocked mission when its recovery question is answered. Resume here
// only when the answered mission is still blocked and not left for the Driver; report, never hide, the outcome.
async function approvalRetryAfterAnswer(item, m) {
  if (m?.status !== "blocked") return;
  if (m.driver_resume) return toast(m.message || "Answer saved. The Driver continues this execution when it runs again.");
  try { await api("/api/missions/action", {id:item.mission, action:"resume"}); }
  catch (failure) { toast(`Answer saved, but the work is still blocked: ${failure.message}`, true); }
}
function approvalStallActions(item) {
  const mission = action => () => api("/api/missions/action", {id:item.mission, action});
  const open = ["Open details", null, () => dashboardOpenMission({id:item.mission, project:item.project})];
  if (item.kind === "blocked") return [["Retry now", mission("resume"), null, true], open];
  if (item.kind === "ready") return [["Accept result", mission("accept"), null, true], ["Review evidence", null, open[2]]];
  if (item.kind === "awaiting_checks") return [["Add checks or accept manually", null, open[2], true]];
  return [["Requeue task", () => approvalCompanyTask(item, "requeue_task"), null, true], ["Disable task", () => approvalCompanyTask(item, "disable_task")]];
}
function approvalStallCard(item, card) {
  const controls = [], error = el("p", "mission-feedback"), actions = el("div", "actions");
  error.setAttribute("role", "alert"); error.hidden = true;
  if (item.kind === "blocked") card.append(el("p", "", item.driver
    ? "The Driver retries blocked company work automatically a limited number of times. Retry now, or open the details to revise the brief or end the work."
    : "No automatic retry is pending. Retry now, or open the details to revise the brief or end the work."));
  if (item.kind === "ready") card.append(el("p", "", "Independent review and checks passed. Accepting applies the verified result; review the evidence first if unsure."));
  for (const [label, act, navigate, primary] of approvalStallActions(item)) {
    const button = el("button", "button"+(primary ? " primary" : ""), label); button.type = "button";
    button.onclick = () => navigate ? Promise.resolve().then(navigate).catch(failure=>{error.textContent = failure.message; error.hidden = false;}) : approvalInboxAct(item, card, controls, error, act);
    controls.push(button); actions.append(button);
  }
  card.append(actions, error);
  return card;
}
function approvalInboxCard(item) {
  const card = el("div", "approval-card"), controls = [];
  card.dataset.approvalKey = item.key;
  card.append(el("p", "approval-context", item.context),
    el("h3", "", item.kind === "tool" ? `Allow ${item.request.name}?` : item.title));
  if (item.kind === "tool") {
    card.append(el("p", "", item.request.reason), el("pre", "", item.request.preview || item.request.target));
  } else if (item.reason) card.append(el("p", "", item.reason));
  if (item.kind === "readiness") {
    card.append(el("p", "", "This needs a corrected brief, not a Yes/No approval. Open Brief readiness and revise the goal or completion criteria."));
    card.append(missionButton("Revise brief", () => dashboardOpenMission({id:item.mission, project:item.project}), true));
    return card;
  }
  if (approvalStallTitles[item.kind]) return approvalStallCard(item, card);
  if (item.kind === "plan") {
    const detail = el("details"), list = el("ol");
    detail.append(el("summary", "", "View proposed plan"));
    for (const task of item.tasks || []) list.append(el("li", "", `${task.title}: ${task.instructions}`));
    detail.append(list); card.append(detail, el("p", "", "Yes will start execution. No will pause it."));
  }
  const feedback = el("textarea"); feedback.rows = 2; feedback.maxLength = item.kind === "tool" ? 10000 : 12000;
  feedback.placeholder = item.kind === "tool" ? "Instruction for the agent if you skip this action…" : "Write your own response or instruction…";
  feedback.setAttribute("aria-label", `Custom response: ${item.context}`);
  if (item.kind !== "plan") card.append(feedback);
  const confirmation = el("input");
  if (item.request?.dangerous) {
    card.append(el("p", "approval-danger", `Elevated risk: ${item.request.dangerous}`));
    confirmation.placeholder = "To allow a risky action, type yes";
    // Mobile keyboards must not turn "yes" into "Yes".
    confirmation.autocomplete = "off"; confirmation.autocapitalize = "off"; confirmation.spellcheck = false;
    confirmation.setAttribute("autocorrect", "off");
    confirmation.setAttribute("aria-label", "Confirm risky action"); card.append(confirmation);
  }
  const error = el("p", "mission-feedback"); error.setAttribute("role", "alert"); error.hidden = true;
  const actions = el("div", "actions");
  const refuse = message => { error.textContent = message; error.hidden = false; feedback.focus(); };
  function decide(choice) {
    if (card.dataset.busy === "true") return;
    error.hidden = true;
    const note = feedback.value.trim();
    // A recovery question asks whether to continue, so an empty retry means "continue".
    if (["custom","redirect","retry"].includes(choice) && !note && !(choice === "retry" && item.recovery))
      return refuse(choice === "redirect" ? "Write the instruction the agent should follow instead." : "Write your own response.");
    if (item.kind === "tool" && choice === "allow" && note)
      return refuse("Allow does not deliver your note to the agent. Clear the note to allow, or use Skip and redirect to send it.");
    if (item.kind === "tool" && choice === "reject" && note)
      return refuse("Reject and stop run does not deliver your note. Clear the note, or use Skip and redirect to send it.");
    const typed = confirmation.value.trim();
    return approvalInboxAct(item, card, controls, error, async () => {
      if (item.kind === "tool") await api("/api/decision", {
        run:item.run, approval:item.request.id, allow:choice==="allow",
        feedback:choice === "redirect" ? feedback.value : "", confirmation:choice === "allow" ? (/^yes$/i.test(typed) ? "yes" : typed) : ""});
      else if (item.kind === "question") {
        const answered = await api("/api/missions/action", {
          id:item.mission, action:"answer", question:item.question,
          answer:["custom","retry"].includes(choice) ? (note ? feedback.value : "Continue with the current limits.")
            : (choice==="yes" ? "Yes" : "No") + (note ? "\n"+feedback.value : "")});
        if (choice === "retry") await approvalRetryAfterAnswer(item, answered);
      }
      else await api("/api/missions/action", {id:item.mission, action:choice==="yes" ? "approve_plan" : "pause"});
    });
  }
  const choices = item.kind === "tool" ? [["allow","Allow"],["redirect","Skip and redirect"],["reject","Reject and stop run"]]
    : item.kind === "plan" ? [["yes","Yes"],["no","No"]]
    // Answering a recovery question resumes the work on the server, so "answer only" is not offered for it.
    : item.blocked ? (item.recovery ? [["retry","Answer and retry"]] : [["retry","Answer and retry"],["custom","Save answer only"]])
    : [["yes","Yes"],["no","No"],["custom","Send response"]];
  for (const [choice,label] of choices) {
    const button = el("button", "button"+(["allow","yes","retry"].includes(choice) ? " primary" : ""), label); button.type="button";
    button.onclick=()=>decide(choice); controls.push(button); actions.append(button);
  }
  if (item.blocked && item.recovery) {
    const details = el("button", "button", "Open details"); details.type = "button";
    details.onclick = () => Promise.resolve().then(() => dashboardOpenMission({id:item.mission, project:item.project}))
      .catch(failure => { error.textContent = failure.message; error.hidden = false; });
    controls.push(details); actions.append(details);
  }
  card.append(actions,error);
  if (item.kind === "tool") card.append(el("p", "approval-help", "Skip and redirect declines only this action and sends your note; the run continues. Reject and stop run ends the current run; the controller may retry the task automatically."));
  if (item.blocked) card.append(el("p", "approval-help", item.recovery
    ? "Answer and retry saves your answer and resumes the blocked work within its remaining limits; an empty answer continues unchanged. To revise the brief, change the limits or end the work, open the details first; the question stays open."
    : "Answer and retry saves your answer and resumes the blocked work within its remaining limits. Save answer only records it; the work stays blocked until you retry."));
  return card;
}
function renderApprovalInbox(items) {
  const keys = new Set(items.map(item=>item.key));
  for (const [key,entry] of approvalInboxCards) if (!keys.has(key)) {entry.node.remove(); approvalInboxCards.delete(key);}
  for (const item of items) {
    if (approvalInboxCards.has(item.key)) continue; // Never erase a reply being typed during polling.
    const node = approvalInboxCard(item); approvalInboxCards.set(item.key,{node}); $("#approvals").append(node);
  }
  approvalInboxBadges(items.length);
  $("#approval-status").textContent = items.length ? "Review the request and choose an action below." : "No request is awaiting your decision.";
}
// Company tasks change slowly; refresh them at most every 10 s for the inbox.
async function approvalInboxCompanyList() {
  if (Date.now() - approvalInboxCompaniesAt < 10000) return approvalInboxCompanies;
  try { approvalInboxCompanies = (await api("/api/companies")).companies || []; approvalInboxCompaniesAt = Date.now(); }
  catch (_) { /* The inbox still shows runs and missions; the next poll retries. */ }
  return approvalInboxCompanies;
}
async function pollApprovalInbox() {
  if (approvalInboxLoading || !state.data) return;
  approvalInboxLoading = true; const epoch = approvalInboxEpoch;
  try {
    const activeRuns = state.data.runs.filter(r=>["running","waiting"].includes(r.status));
    const [missions, companies, ...runs] = await Promise.all([api("/api/missions"), approvalInboxCompanyList(), ...activeRuns.map(async r=>{
      const event = await api(`/api/events?run=${r.id}&offset=${approvalInboxOffsets.get(r.id)||0}`);
      approvalInboxOffsets.set(r.id,event.offset);
      return {id:r.id,title:state.data.projects.find(p=>p.id===r.project)?.name || r.project,approvals:event.approvals};
    })]);
    if (epoch !== approvalInboxEpoch) return;
    renderApprovalInbox(approvalInboxItems(runs,missions.missions,companies));
  } catch (error) {
    $("#approval-status").textContent = "Approval cannot be updated: " + error.message;
  } finally { approvalInboxLoading = false; }
}
function initApprovalInbox() {
  if (approvalInboxStarted) return; approvalInboxStarted = true;
  $("#approval-jump").onclick = focusApprovalInbox;
  pollApprovalInbox(); setInterval(pollApprovalInbox,1500);
}

// Live mission flow map
"use strict";
const flowState = {selection:null, node:null, loading:false, epoch:0, cache:new Map(), graphKey:"", detailKey:"", follow:true, scrolled:null, data:null};
const flowStateLabels = {active:"Working", done:"Verified", waiting:"Pending", blocked:"Obstacle", pending:"Ahead", paused:"Paused"};
// Worker notes for blocked, denied or owner-rejected tool calls ("✗ blocked: …", "✗ bash blocked: …", "✗ rejected bash — stopping task").
function isToolObstacle(text) { return /(?:^|\n)\s*✗\s|\bblocked:|\bdenied:|\brejected\b[^\n]*\bstopping task\b/i.test(text || ""); }
function flowModel(m, log = {}, now = Date.now()/1000) {
  const events = log.events || [], latest = events.at(-1), questions = (m.questions || []).filter(q=>q.answer === null);
  const attempt = (m.attempts || []).find(a=>a.id === m.active_attempt) || m.attempts?.at(-1);
  const live = m.status === "verifying" || !!m.active_attempt && m.status === "running";
  const age = now - (m.status === "verifying" ? m.updated || now : latest?.time || attempt?.started || m.updated || now);
  const denied = events.filter(e=>e.type === "note" && isToolObstacle(e.text)).at(-1);
  const error = events.filter(e=>e.type === "error" || e.type === "tool_result" && (e.is_error || /(?:^|\n\s*Info: )Blocked:/i.test(e.text || ""))).at(-1);
  const issue = [denied,error].filter(Boolean).sort((a,b)=>b.time-a.time)[0];
  const awaiting = log.approvals?.length > 0;
  const terminal = ["accepted","cancelled","expired"].includes(m.status);
  let tone = live ? "active" : "waiting", headline = missionLabels[m.status] || m.status, reason = m.message;
  if (m.status === "accepted") {tone="done"; headline="Result accepted";}
  else if (["cancelled","paused","draft"].includes(m.status)) {tone="paused";}
  else if (m.status === "expired") {tone="blocked";}
  else if (questions.length) {tone="waiting"; headline="Awaiting your response"; reason=questions[0].question;}
  else if (awaiting) {tone="waiting"; headline="Awaiting tool approval"; reason=`${log.approvals[0].name}: ${log.approvals[0].reason || log.approvals[0].target}`;}
  else if (m.status === "blocked") {tone="blocked"; headline="Work is blocked";}
  else if (live && age > 180) {tone="waiting"; headline="The run has no fresh activity"; reason=`Last event ${Math.floor(age/60)} min ago. The process may be waiting for a model or tool; further progress is not confirmed.`;}
  else if (live && issue && m.status !== "verifying") {tone="blocked"; headline="Run continues, encountered an obstacle"; reason=issue.text;}
  else if (live) {headline=m.status === "verifying" ? "Independent checks are running" : m.phase === "review" ? "Reviewer is checking the result" : m.phase === "plan" ? "Work plan is being created" : "Agent is currently working";}
  const moving = live && tone === "active" && !log.loading;
  const nodes = [{id:"@plan",title:"Plan and task brief",kind:"plan",rank:0, status:m.phase === "plan" && !terminal ? tone : m.tasks.length ? "done" : "pending", subtitle:"Goal → specific tasks"}];
  const ranks = new Map(), visiting = new Set(), tasks = m.tasks || [];
  function rank(t) {if(ranks.has(t.id)) return ranks.get(t.id); if(visiting.has(t.id)) return 1; visiting.add(t.id); const r=1+Math.max(0,...(t.depends_on || []).map(id=>{const dep=tasks.find(x=>x.id===id);return dep?rank(dep):0;}));visiting.delete(t.id);ranks.set(t.id,r);return r;}
  const edges=[];
  for(const t of tasks) {
    const current = attempt?.task === t.id && !["final","plan"].includes(m.phase) && !terminal && !["ready","awaiting_checks","verifying"].includes(m.status);
    const status = t.status === "done" ? "done" : current ? tone : "pending";
    nodes.push({id:t.id,title:t.title,kind:"task",rank:rank(t),status,task:t,current,subtitle:current && m.phase === "review" ? "Independent review" : t.status === "done" ? "Task check passed" : `${t.depends_on.length ? "Dependencies: " + t.depends_on.length : "First execution step"}`});
    for(const from of t.depends_on.length ? t.depends_on : ["@plan"]) edges.push({from,to:t.id});
  }
  const max=Math.max(0,...ranks.values());
  const checking=["verifying","awaiting_checks","ready","accepted"].includes(m.status) || m.phase === "final";
  nodes.push({id:"@checks",title:"Product verification",kind:"checks",rank:max+1,status:m.acceptance?.kind === "manual" ? "paused" : m.status === "ready" || m.status === "accepted" ? "done" : checking ? tone : "pending",subtitle:m.acceptance?.kind === "manual" ? "Accepted without automatic checks" : "Review and independent checks"});
  for(const t of tasks.filter(t=>!tasks.some(other=>other.depends_on.includes(t.id)))) edges.push({from:t.id,to:"@checks"});
  if(!tasks.length)edges.push({from:"@plan",to:"@checks"});
  nodes.push({id:"@accept",title:"Result acceptance",kind:"accept",rank:max+2,label:m.status === "accepted" ? "Accepted" : null,status:m.status === "accepted" ? "done" : ["ready","awaiting_checks"].includes(m.status) ? "waiting" : "pending",subtitle:m.status === "accepted" ? "Result is accepted" : "Decision on the finished product"});
  edges.push({from:"@checks",to:"@accept"});
  const current = nodes.find(n=>n.current) || nodes.find(n=>["active","blocked","waiting"].includes(n.status)) || nodes[0];
  return {nodes,edges,current,tone,headline,reason,issue,age,moving,questions,latest,attempt,done:tasks.filter(t=>t.status==="done").length,total:tasks.length};
}
function flowSvg(tag, attributes={}, text) {
  const n=document.createElementNS("http://www.w3.org/2000/svg",tag);
  for(const [key,value] of Object.entries(attributes))n.setAttribute(key,String(value));
  if(text!==undefined)n.textContent=text;
  return n;
}
function flowLines(text, width=27) {
  const words=String(text).split(/\s+/), lines=[""];
  for(const word of words){if((lines.at(-1)+" "+word).trim().length>width && lines.at(-1))lines.push("");lines[lines.length-1]=(lines.at(-1)+" "+word).trim();}
  return lines.slice(0,2).map((line,i)=>i===1 && lines.length>2 ? line.slice(0,width-1)+"…" : line);
}
function renderFlowGraph(m, model) {
  const key=JSON.stringify([model.nodes,model.edges,model.moving,flowState.node]);
  if(key===flowState.graphKey)return;
  flowState.graphKey=key;
  const ranks=new Map();for(const n of model.nodes){if(!ranks.has(n.rank))ranks.set(n.rank,[]);ranks.get(n.rank).push(n);}
  const rows=Math.max(...[...ranks.values()].map(v=>v.length)), height=Math.max(240,rows*128+56), width=(ranks.size)*232+12;
  const positions=new Map();for(const [rank,list] of ranks)list.forEach((n,i)=>positions.set(n.id,{x:24+rank*232,y:(height-list.length*128)/2+i*128}));
  const svg=flowSvg("svg",{viewBox:`0 0 ${width} ${height}`,width,height,role:"group","aria-label":"Execution progress and step dependencies"});
  const defs=flowSvg("defs"), marker=flowSvg("marker",{id:"flow-arrow",viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:5,markerHeight:5,orient:"auto-start-reverse"});marker.append(flowSvg("path",{d:"M 0 0 L 10 5 L 0 10 z",fill:"#687788"}));defs.append(marker);svg.append(defs);
  for(const edge of model.edges){const a=positions.get(edge.from),b=positions.get(edge.to);if(!a||!b)continue;const x=a.x+200,y=a.y+50,end=b.x;const target=model.nodes.find(n=>n.id===edge.to);svg.append(flowSvg("path",{d:`M${x},${y} C${x+16},${y} ${end-16},${b.y+50} ${end-5},${b.y+50}`,class:"flow-edge"+(model.moving && target.id===model.current.id ? " moving" : ""),"marker-end":"url(#flow-arrow)"}));}
  for(const n of model.nodes){const p=positions.get(n.id), g=flowSvg("g",{transform:`translate(${p.x},${p.y})`,class:`flow-node ${n.status}${flowState.node===n.id ? " selected" : ""}`,role:"button",tabindex:0,"aria-label":`${n.title}: ${(n.label || flowStateLabels[n.status])}. Open details.`});
    g.append(flowSvg("title",{},n.title),flowSvg("rect",{width:200,height:100,rx:12}),flowSvg("circle",{cx:17,cy:19,r:4,class:model.moving && n.id===model.current.id ? "flow-pulse" : ""}),flowSvg("text",{x:29,y:23,class:"flow-node-status"},(n.label || flowStateLabels[n.status])));
    flowLines(n.title).forEach((line,i)=>g.append(flowSvg("text",{x:14,y:46+i*16,class:"flow-node-title"},line)));
    g.append(flowSvg("text",{x:14,y:84,class:"flow-node-subtitle"},n.subtitle));
    const select=()=>{flowState.follow=false;flowState.node=n.id;renderFlowGraph(m,model);renderFlowDetail(m,model);};g.onclick=select;g.onkeydown=e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();select();}};svg.append(g);
  }
  $("#flow-canvas").replaceChildren(svg);
  if(flowState.follow && flowState.scrolled!==m.id+model.current.id){
    const canvas=$("#flow-canvas"),p=positions.get(model.current.id);
    canvas.scrollLeft=Math.max(0,p.x-(canvas.clientWidth-200)/2);canvas.scrollTop=Math.max(0,p.y-(canvas.clientHeight-100)/2);
    flowState.scrolled=m.id+model.current.id;
  }
}
function flowOpenMission(m) {
  $("#flow-dialog").close();
  return openCompanyMission(m.project,m.id);
}
function renderFlowDetail(m,model){
  const detailKey=JSON.stringify([flowState.node,m,model.issue,model.questions]);if(flowState.detailKey===detailKey)return;flowState.detailKey=detailKey;
  const n=model.nodes.find(n=>n.id===flowState.node)||model.current,panel=$("#flow-detail");panel.replaceChildren(el("span","eyebrow","SELECTED STEP"),el("h3","",n.title),el("span",`flow-badge ${n.status}`,(n.label || flowStateLabels[n.status])));
  panel.append(el("p","",n.task?.instructions || (n.kind==="plan" ? m.goal : n.kind==="checks" ? "The reviewer checks the product. The controller then launches approved independent checks, if configured." : "The finished result is accepted only after checks or explicit manual acceptance.")));
  if(n.task?.feedback)panel.append(el("p","flow-warning",n.task.feedback));
  if(n.current && model.issue)panel.append(el("p","flow-warning",model.issue.text));
  panel.append(el("p","missions-note",`Worker: ${m.profile} · Reviewer: ${m.review_profile} · Runs: ${m.attempts.length}/${m.max_attempts}`));
  panel.append(missionButton(model.questions.length ? "Reply and unblock" : "Open execution and results",()=>flowOpenMission(m),true));
  if(m.active_attempt)panel.append(missionButton("Open live log / approval",async()=>{$("#flow-dialog").close();$("#missions-dialog").close();$("#driver-dialog").close();await refreshState();await selectRun(m.active_attempt);$("#approval-inbox").scrollIntoView({block:"nearest"});}));
}
function renderFlow(m,log){
  const model=flowModel(m,log),summary=$("#flow-summary");
  if(flowState.follow || !model.nodes.some(n=>n.id===flowState.node))flowState.node=model.current.id;
  summary.className=`flow-summary ${model.tone}`;
  summary.replaceChildren(el("div","flow-signal"),el("div","flow-summary-copy"),el("div","flow-counter"));
  const copy=summary.children[1];copy.append(el("strong","",model.headline),el("p","",model.reason || "Waiting for the next step."));
  if(model.issue && model.reason!==model.issue.text && !["accepted","cancelled"].includes(m.status))copy.append(el("p","flow-warning","Last obstacle: "+model.issue.text));
  summary.children[2].append(el("strong","",`${model.done} / ${model.total}`),el("small","","verified tasks"));
  renderFlowGraph(m,model);renderFlowDetail(m,model);
  const activity=$("#flow-activity");activity.replaceChildren(el("span","eyebrow","REAL EVENTS"),el("h3","","Last movement"));
  const meaningful=(log.events||[]).filter(e=>["tool_call","tool_result","approval","decision","error","final"].includes(e.type)||e.type==="note"&&isToolObstacle(e.text));
  if(!meaningful.length)activity.append(el("p","missions-note",log.loading ? "Loading run events…" : "No tool actions have been recorded yet."));
  for(const e of meaningful.slice(-6).reverse()){
    const row=el("div","flow-event"),when=el("time","",new Date(e.time*1000).toLocaleTimeString("en-GB"));
    const title=e.type==="tool_call" ? `Run · ${e.name}` : e.type==="tool_result" ? `${e.is_error ? "Error" : "Tool response"} · ${e.name}` : e.type==="approval" ? `Approval request · ${e.name}` : e.type==="decision" ? e.allow ? "Action approved" : "Action rejected" : e.type==="note" ? "Tool blocked or rejected" : e.type==="error" ? "Run error" : "Run ended";
    row.append(when,el("span","",title));if(e.type==="note"||e.type==="error"){row.classList.add("issue");row.append(el("p","",e.text));}activity.append(row);
  }
  activity.append(el("p","missions-note",model.latest ? `Last event: ${new Date(model.latest.time*1000).toLocaleTimeString("en-GB")}. The tool response itself does not confirm task completion.` : "Status is derived from the controller. Results will be confirmed only after review."));
}
async function loadFlow(){
  if(!$("#flow-dialog").open||flowState.loading)return;
  flowState.loading=true;const epoch=flowState.epoch;
  try{
    const data=await api("/api/missions");if(epoch!==flowState.epoch||!$("#flow-dialog").open)return;
    const missions=data.missions;
    if(!missions.some(m=>m.id===flowState.selection))flowState.selection=missions.find(m=>!["accepted","cancelled","expired"].includes(m.status))?.id || missions[0]?.id;
    const select=$("#flow-mission"),signature=JSON.stringify(missions.map(m=>[m.id,m.title,m.status]));
    if(select.dataset.signature!==signature){select.replaceChildren(...missions.map(m=>{const option=el("option","",`${m.title} · ${missionLabels[m.status]||m.status}`);option.value=m.id;return option;}));select.dataset.signature=signature;}select.value=flowState.selection||"";
    const m=missions.find(m=>m.id===flowState.selection);if(!m){$("#flow-summary").textContent="There is no execution yet. Create one in AI Projects or in the Company.";$("#flow-canvas").replaceChildren();$("#flow-detail").replaceChildren();$("#flow-activity").replaceChildren();$("#flow-sync").textContent="Connected";return;}
    const runId=m.active_attempt||m.attempts.at(-1)?.id;
    let log={events:[],approvals:[]};
    if(runId){
      if(!flowState.cache.has(runId)){if(flowState.cache.size>=8)flowState.cache.delete(flowState.cache.keys().next().value);flowState.cache.set(runId,{events:[],approvals:[],offset:0});}
      log=flowState.cache.get(runId);
      // Drain event pages incrementally: token streaming can exceed one 600-line page.
      for(let i=0;i<4;i++){
        const page=await api(`/api/events?run=${encodeURIComponent(runId)}&offset=${log.offset}`);
        if(epoch!==flowState.epoch||!$("#flow-dialog").open)return;
        log.offset=page.offset;log.approvals=page.approvals;log.loading=page.events.length>=600;
        const combined=log.events.concat(page.events);
        const meaningful=combined.filter(e=>e.type!=="content"&&e.type!=="usage").slice(-300);
        const last=combined.at(-1);log.events=last && !meaningful.includes(last) ? meaningful.concat(last) : meaningful;
        if(!log.loading)break;
      }
    }
    if(epoch!==flowState.epoch||!$("#flow-dialog").open)return;
    flowState.data={m,log};renderFlow(m,log);
    $("#flow-sync").textContent=data.controller_error ? "Controller error: "+data.controller_error : log.loading ? "Fetching history…" : `Live · refreshed ${new Date().toLocaleTimeString("en-GB")}`;
    $("#flow-dialog").classList.remove("flow-offline");
  }catch(error){if(epoch===flowState.epoch){$("#flow-sync").textContent="Connection interrupted — displayed status may be outdated. "+error.message;$("#flow-dialog").classList.add("flow-offline");}}
  finally{flowState.loading=false;}
}
async function showFlow(id){
  flowState.epoch++;flowState.selection=id||flowState.selection;flowState.node=null;flowState.follow=true;flowState.scrolled=null;flowState.graphKey="";flowState.detailKey="";
  if(!$("#flow-dialog").open)$("#flow-dialog").showModal();
  await loadFlow();
}
function initFlow(){
  bind("#flow-button","click",()=>showFlow());
  bind("#flow-follow","click",()=>{flowState.follow=true;flowState.scrolled=null;flowState.graphKey="";if(flowState.data)renderFlow(flowState.data.m,flowState.data.log);});
  bind("#flow-mission","change",e=>{flowState.epoch++;flowState.selection=e.target.value;flowState.node=null;flowState.follow=true;flowState.scrolled=null;flowState.graphKey="";flowState.detailKey="";return loadFlow();});
  $("#flow-dialog").addEventListener("close",()=>{flowState.epoch++;});
  setInterval(loadFlow,2000);
}
