"use strict";
const companyActionNames = {created:"Založení firmy",start:"Spuštění řadiče",pause:"Pozastavení",paused:"Pozastavení",resume:"Pokračování",settings:"Změna nastavení",reserve_runs:"Rezervace běhů",add_task:"Přidání úkolu",add_project:"Připojení projektu",disable_task:"Vypnutí úkolu",enable_task:"Zapnutí úkolu",resolve_external:"Vyřízení externí akce",dispatch:"Přidělení práce",accept:"Převzetí",approve_plan:"Schválení plánu"};
let companySelection = null, companyData = null, companyLoading = false, companyMutating = false;
let companySubmitting = false;
let companyDirty = false, driverEpoch = 0, companySnapshot = "";
const companyStatus = {active:"Řadič běží", paused:"Pozastaveno", queued:"Ve frontě", scheduled:"Čeká na termín", done:"Dokončeno", needs_owner:"Rozhodnutí vlastníka", rejected:"Zamítnuto", running:"Probíhá", waiting:"Čeká na odpověď", blocked:"Blokováno", ready:"K převzetí", awaiting_checks:"Chybí kontroly", expired:"Vypršel čas", cancelled:"Zrušeno", awaiting_plan:"Připravený plán", verifying:"Ověřování"};
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
    if ($$("#driver-dialog form").some(other => other !== form && other.dataset.dirty === "true")) { showError("Rozpracovaný je i jiný formulář. Nejdřív dokonči jeho změny, nebo použij Obnovit přehled a formuláře pro jejich zahození."); return; }
    button.disabled = true;
    try { companySubmitting = true; await fn(); } catch (failure) { showError(failure.message); } finally { companySubmitting = false; button.disabled = false; } };
}
function companyPanel(parent, title) {
  const details = el("details", "product-form-panel"), form = el("form");
  details.append(el("summary", "", title), form); parent.append(details); return form;
}
async function companyAction(c, action, extra = {}) {
  if (companyMutating) return;
  if (companyDirty && !companySubmitting && action !== "pause") throw new Error("Nejdřív ulož formulář nebo obnov přehled.");
  const preserveDraft = companyDirty && action === "pause";
  companyMutating = true; driverEpoch++;
  try {
    const saved = await api("/api/companies/action", {id:c.id, revision:c.revision, action, ...extra});
    if (preserveDraft) {
      Object.assign(c, saved);
      $("#driver-toggle").textContent = "Zapnout Řadič";
      $("#driver-state").textContent = "Pozastaveno";
      $("#driver-message").textContent = saved.message;
      const nav = $("#driver-company-" + c.id); if (nav) nav.textContent = `${c.name} · Pozastaveno`;
      return;
    }
    companyDirty = false; companySnapshot = "";
    $$("#driver-dialog form").forEach(f => { delete f.dataset.dirty; });
  } finally { companyMutating = false; }
  await loadCompanies();
}
async function openCompanyMission(projectId, missionId) {
  if (companyDirty) throw new Error("Nejdřív ulož změny firmy nebo obnov formulář.");
  if (state.dirty) throw new Error("Nejdřív ulož otevřený soubor.");
  $("#driver-dialog").close();
  // Mission view is workspace-scoped. Use the existing project switch path.
  if (state.project !== projectId) await selectProject(projectId);
  if (state.project !== projectId) return;
  missionViewProject = projectId; missionSelection = missionId; missionSnapshot = "";
  await showMissions();
}
function companyLineList(input) { return input.value.split("\n").map(x => x.trim()).filter(Boolean); }
function renderDriverCompany(c, data) {
  const panel = $("#driver-detail"); panel.replaceChildren();
  if (!c) { panel.append(el("h3", "", "Založ svou AI společnost"), el("p", "", "Vyber projekty, stanov cíle a přidej první práci. Řadič navazuje na realizace a jejich ověřené výsledky.")); return; }
  const stateChip=el("span", "mission-state", companyStatus[c.status]);stateChip.id="driver-state";
  panel.append(el("h2", "", c.name), stateChip, el("p", "", c.purpose));
  const metrics = el("div", "driver-metrics");
  for (const [n, label] of [[c.projects.length,"projektů"],[c.usage.used_runs,"použitých běhů"],[c.usage.reserved_runs,"rezervovaných běhů"],[`${c.cycles.length}/${c.policy.cycle_limit}`,"provedení"]]) {
    const box = el("div"); box.append(el("strong", "", String(n)), el("small", "", label)); metrics.append(box);
  }
  const message=el("p", "mission-message", c.message);message.id="driver-message";panel.append(metrics, message);
  panel.append(el("p", "missions-note", c.deadline ? `Horizont do ${new Date(c.deadline*1000).toLocaleString("cs-CZ")}` : "Horizont začne zapnutím řadiče."));
  const actions = el("div", "mission-actions");
  actions.append(companyButton("Živá mapa průběhu", () => showFlow(c.tasks.find(t => t.status === "running")?.last_mission || c.cycles.at(-1)?.mission)));
  const toggle=companyButton(c.status === "active" ? "Pozastavit Řadič" : "Zapnout Řadič", () => {
    if (companyDirty && c.status !== "active") throw new Error("Nejdřív ulož rozepsaný formulář nebo ho obnov.");
    return companyAction(c, c.status === "active" ? "pause" : "start");
  }, true);toggle.id="driver-toggle";
  actions.append(toggle, companyButton("Stáhnout report .md", async () => {
    const r = await api(`/api/companies/report?id=${c.id}`), url = URL.createObjectURL(new Blob([r.text], {type:"text/markdown;charset=utf-8"}));
    const a = el("a"); a.href = url; a.download = `company-${c.id}.md`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  })); panel.append(actions);
  const map = el("div", "driver-map"); map.append(el("strong", "", "Ty · cíle a rozhodnutí"), el("span", "", "↓ Řadič firmy · priority, limity, termíny"), el("span", "", "↓ Oddělení → projekty → realizátor → kontrolor → kontroly → převzetí ↻")); panel.append(map);
  panel.append(el("h3", "", "Portfolio"));
  for (const key of c.projects) {
    const project = state.data.projects.find(p=>p.id===key), row = el("div", "product-work-item");
    row.append(el("strong", "", project?.name || key), el("p", "missions-note", project?.path || ""));
    for (const p of c.products.filter(p=>p.project===key)) row.append(el("p", "", `${p.title} · ${p.releases} převzatých verzí · ${p.status}`));
    panel.append(row);
  }
  const projectForm = companyPanel(panel,"Připojit další otevřený projekt");
  const projects = companySelect(projectForm,"Projekt",state.data.projects.filter(p=>!c.projects.includes(p.id)).map(p=>[p.id,p.name]));
  companySubmit(projectForm,"Připojit",()=>companyAction(c,"add_project",{project:projects.value}));
  panel.append(el("h3", "", "Rozhodnutí a blokace"));
  if (!c.inbox.length && !c.tasks.some(t=>t.status==="needs_owner")) panel.append(el("p", "missions-note", "Žádné otevřené rozhodnutí."));
  for (const item of c.inbox) {
    const row = el("div","product-work-item"); row.append(el("strong","",item.title),el("p","",`${companyStatus[item.status] || item.status}: ${item.message}`));
    for (const q of item.questions) row.append(el("p","mission-feedback",q.question));
    row.append(companyButton("Otevřít realizaci a odpovědět",()=>openCompanyMission(item.project,item.mission))); panel.append(row);
  }
  panel.append(el("h3", "", "Práce oddělení a opakování"));
  for (const t of c.tasks) {
    const row = el("div", "product-work-item");
    row.append(el("strong", "", t.title), el("p", "missions-note", `${data.departments[t.department]} · ${companyStatus[t.status] || t.status} · priorita ${t.priority}${t.enabled ? "" : " · vypnuto"}`));
    const detail = el("details"); detail.append(el("summary", "", "Zadání, kritéria a návaznosti"),el("p","",t.goal));
    const criteria = el("ul"); criteria.append(...t.criteria.map(v=>el("li","",v))); detail.append(criteria);
    detail.append(el("p","missions-note",`Opakování: ${t.interval_hours ? `${t.interval_hours} h, nejvýše ${t.max_cycles}×` : "jednorázově"}. Závislosti: ${t.depends_on.map(id=>c.tasks.find(x=>x.id===id)?.title || id).join(", ") || "žádné"}.`));
    if (t.status === "scheduled") detail.append(el("p","",`Další termín: ${new Date(t.due*1000).toLocaleString("cs-CZ")}`));
    if (t.outcome) detail.append(el("p","",t.outcome)); row.append(detail);
    if (t.last_mission) row.append(companyButton("Průběh a důkazy",()=>openCompanyMission(t.project,t.last_mission)));
    if (t.kind === "external" && t.status === "needs_owner") {
      const f = el("form"), note = companyField(f,"Doklad provedení nebo důvod zamítnutí","","textarea"); note.required=true;
      const outcome = companySelect(f,"Výsledek",[["done","Provedeno vlastníkem"],["rejected","Zamítnuto"]]);
      companySubmit(f,"Zapsat výsledek — nic neodesílá",()=>companyAction(c,"resolve_external",{task:t.id,note:note.value,outcome:outcome.value}));row.append(f);
    } else if (["queued","scheduled","done","cancelled","expired"].includes(t.status)) row.append(companyButton(t.enabled?"Vypnout pravidlo":"Zapnout pravidlo",()=>companyAction(c,t.enabled?"disable_task":"enable_task",{task:t.id})));
    panel.append(row);
  }
  const f = companyPanel(panel, "+ Přidat práci nebo pravidelné zadání");
  const preset = companySelect(f,"Výchozí zadání",[["custom","Vlastní úkol"],["plan","Tvůrce: plán rozvoje projektu"],["health","Řadič: pravidelný audit projektu"],["sales","Obchod: příprava nabídky"],["finance","Finance: přehled z dodaných podkladů"]]);
  const title = companyField(f,"Název úkolu");title.required=true;title.maxLength=160;
  const goal = companyField(f,"Výsledek, který má vzniknout","","textarea");goal.required=true;
  const criteria = companyField(f,"Podmínky převzetí, jedna na řádek","","textarea");criteria.required=true;
  const project = companySelect(f,"Pracovní projekt",c.projects.map(id=>[id,state.data.projects.find(p=>p.id===id)?.name || id]));
  const dept = companySelect(f,"Odpovědné oddělení",Object.entries(data.departments));
  const kind = companySelect(f,"Způsob provedení",[["work","Práce agenta v projektu"],["external","Externí krok — předat vlastníkovi"]]);
  const deps = companySelect(f,"Spustit až po dokončení (Ctrl/Cmd pro více)",c.tasks.map(t=>[t.id,t.title]),true);
  const priority = companySelect(f,"Priorita",[["1","1 · nejvyšší"],["2","2 · běžná"],["3","3 · nižší"]]);priority.value="2";
  const hours = companyField(f,"Opakovat po hodinách (0 = jednorázově)",0,"number");hours.min=0;hours.max=8760;
  const cycles = companyField(f,"Nejvýše realizací tohoto pravidla",1,"number");cycles.min=1;cycles.max=100;
  const checks = companyField(f,"Nezávislé kontrolní příkazy, každý na řádek","","textarea");
  const sources = companyField(f,"Podklady a odkazy (bez hesel)","","textarea");
  preset.onchange = () => {
    companyDirty=true; f.dataset.dirty="true";
    const defaults = {
      plan:["Plán rozvoje projektu","Prozkoumej skutečný stav projektu. Ulož company-plan.md s cílem, současnými možnostmi, prioritami, konkrétními úkoly, závislostmi a otázkami pro vlastníka. Nevymýšlej výsledky ani zákazníky.","Existuje company-plan.md.\nKaždé tvrzení o současném stavu má zdroj nebo je označeno jako neověřené.","operations"],
      health:["Pravidelný audit projektu","Prohlédni projekt a dostupné lokální kontroly. Ulož operations-report.md s výsledky, doloženými chybami, blokacemi a navrženými dalšími kroky. Neprováděj externí změny.","Existuje operations-report.md.\nReport odlišuje spuštěné kontroly a neověřené oblasti.","platform"],
      sales:["Návrh nabídky","Z dodaných podkladů připrav proposal.md: potřeba zákazníka, rozsah dodávky, výstupy, otevřené otázky a cenové předpoklady. Nepoužívej vymyšlené reference ani ceny. Nic neodesílej.","Existuje proposal.md.\nChybějící údaje jsou označené k doplnění.","growth"],
      finance:["Přehled nákladů a závazků","Z dodaných podkladů vytvoř finance-report.md s náklady, závazky, datem a původem každého čísla. Chybějící data označ. Neprováděj platby.","Existuje finance-report.md.\nKaždé číslo má dohledatelný zdroj.","finance"]};
    if (defaults[preset.value]) [title.value,goal.value,criteria.value,dept.value]=defaults[preset.value];
  };
  companySubmit(f,"Přidat do fronty",()=>companyAction(c,"add_task",{title:title.value,goal:goal.value,criteria:companyLineList(criteria),project:project.value,department:dept.value,kind:kind.value,priority:Number(priority.value),depends_on:Array.from(deps.selectedOptions,o=>o.value),interval_hours:Number(hours.value),max_cycles:Number(cycles.value),verification_checks:checks.value,sources:sources.value}));
  const settings = companyPanel(panel,"Limity a oprávnění řadiče");
  const fields = {};
  for (const [key,label,min,max] of [["days","Horizont ve dnech",1,365],["run_budget","Celkový rozpočet běhů (nikoliv peněz)",2,100000],["cycle_limit","Celkem realizací",1,1000],["attempts_per_cycle","Běhů na realizaci",2,1000],["attempt_minutes","Minut na jeden běh",1,360],["max_turns","Kroků na běh",1,200]]) {
    fields[key] = companyField(settings,label,c.policy[key],"number");fields[key].min=min;fields[key].max=max;
  }
  const tools = companyField(settings,"Povolit nástroje bez jednotlivého schválení — i v rozpracovaných realizacích",c.policy.auto_tools,"checkbox");
  const accept = companyField(settings,"Automaticky převzít výsledek po úspěšných nezávislých kontrolách",c.policy.auto_accept,"checkbox");
  const renew = companyField(settings,"Při příštím zapnutí začít nový časový horizont (spotřeba se nenuluje)",false,"checkbox");
  settings.append(el("p","missions-note","Nastavení měň při pozastaveném řadiče. Automatické nástroje běží s oprávněními tohoto uživatele; pracovní kopie není izolované prostředí. Limity neomezují účet poskytovatele ani jiné aplikace. Externí systémy nejsou připojené."));
  companySubmit(settings,"Uložit limity",()=>companyAction(c,"settings",{...Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,Number(v.value)])),auto_tools:tools.checked,auto_accept:accept.checked,renew_horizon:renew.checked}));
  const history=el("details");history.append(el("summary","","Historie řadiče (posledních 100 událostí)"));
  const unfinished=c.cycles.filter(x=>!["accepted","cancelled","expired"].includes(x.status));
  if(c.status==="paused" && unfinished.length){
    const reservation=companyPanel(panel,"Přidělit další běhy rozpracované realizaci");
    const cycle=companySelect(reservation,"Realizace",unfinished.map(x=>[x.mission,c.tasks.find(t=>t.id===x.task)?.title||x.mission]));
    const maximum=companyField(reservation,"Nový celkový limit běhů realizace",c.policy.attempts_per_cycle+10,"number");maximum.min=2;maximum.max=1000;
    reservation.append(el("p","missions-note",`Zbývá ${c.usage.remaining_runs} nepřidělených běhů. Celkový rozpočet firmy se nemění.`));
    companySubmit(reservation,"Zvýšit rezervaci",()=>companyAction(c,"reserve_runs",{mission:cycle.value,max_attempts:Number(maximum.value)}));
  }
  for(const e of c.events) history.append(el("p","missions-note",`${new Date(e.at*1000).toLocaleString("cs-CZ")} · ${companyActionNames[e.action] || e.action} · ${e.message || e.mission || ""}`));panel.append(history);
}
async function loadCompanies(force = false) {
  if (!$("#driver-dialog").open || companyLoading || companyMutating) return;
  const epoch = driverEpoch; companyLoading = true;
  try {
    const data = await api("/api/companies");
    if (epoch !== driverEpoch || !$("#driver-dialog").open) return;
    $("#driver-health").textContent = data.controller_error || "Pozoruj → vyber připravenou práci → realizuj → zkontroluj → převezmi → opakuj. Jeden pracovní slot, trvalý stav v SQLite.";
    if (companyDirty && !force) { $("#driver-health").textContent += " Formulář je rozepsaný; zobrazení se nepřepisuje."; return; }
    const snapshot=JSON.stringify(data);
    if (!force && companySnapshot===snapshot) return;
    companyData=data; companySnapshot=snapshot;
    if (!data.companies.some(c=>c.id===companySelection)) companySelection=data.companies[0]?.id || null;
    const list=$("#driver-list");list.replaceChildren();
    for (const c of data.companies) { const button=companyButton(`${c.name} · ${companyStatus[c.status]}`,()=>{
      if(companyDirty) throw new Error("Nejdřív ulož formulář nebo obnov přehled.");
      companySelection=c.id;companySnapshot="";return loadCompanies(true);
    }, c.id===companySelection);button.id="driver-company-"+c.id;list.append(button); }
    renderDriverCompany(data.companies.find(c=>c.id===companySelection),data);
  } finally { companyLoading=false; }
}
async function showCompanies() {
  const latest=await api("/api/state");state.data.projects=latest.projects;
  const form=$("#driver-create");
  if (!form.childElementCount) {
    const name=companyField(form,"Název firmy","Moje firma");name.required=true;name.maxLength=160;
    const purpose=companyField(form,"Cíle firmy a měřitelné výsledky","Řídit dodávku a údržbu projektů Moje firma. Prioritizovat doloženou práci, odhalovat blokace a připravovat rozhodnutí vlastníka.","textarea");purpose.required=true;
    const projects=companySelect(form,"Projekty (Ctrl/Cmd pro více)",latest.projects.map(p=>[p.id,p.name]),true);projects.required=true;
    for(const opt of projects.options) opt.selected=opt.value===state.project;
    const profiles=latest.profiles.map(p=>[p.id,`${p.model} · ${p.id}`]);
    const profile=companySelect(form,"Realizátor a plánovač",profiles),review=companySelect(form,"Kontrolor",profiles);
    companySubmit(form,"Založit firmu",async()=>{
      if(companyMutating)return;companyMutating=true;driverEpoch++;
      try {
        const c=await api("/api/companies",{name:name.value,purpose:purpose.value,projects:Array.from(projects.selectedOptions,o=>o.value),profile:profile.value,review_profile:review.value});
        companySelection=c.id;companyDirty=false;companySnapshot="";delete form.dataset.dirty;$("#driver-new").open=false;
      } finally { companyMutating=false; }
      await loadCompanies(true);
    });
  }
  $("#driver-dialog").showModal();await loadCompanies();
}
function initCompanies() {
  bind("#driver-button","click",showCompanies);
  bind("#driver-refresh","click",()=>{
    if(companyDirty && !confirm("Zahodit rozepsané formuláře a načíst aktuální stav?"))return;
    companyDirty=false;driverEpoch++;$("#driver-create").replaceChildren();return showCompanies();
  });
  setInterval(()=>loadCompanies().catch(e=>toast(e.message,true)),3000);
}

// Global inbox: independent of the selected project, run and open company dialog.
const approvalInboxCards = new Map(), approvalInboxOffsets = new Map();
let approvalInboxLoading = false, approvalInboxEpoch = 0, approvalInboxStarted = false;
function approvalInboxItems(runs, missions) {
  const items = [];
  for (const run of runs) for (const request of run.approvals || []) {
    if (!request.id) continue;
    items.push({key:`tool/${run.id}/${request.id}`, kind:"tool", run:run.id,
      context:run.title, request});
  }
  for (const m of missions) {
    if (["accepted","cancelled","expired"].includes(m.status)) continue;
    for (const q of m.questions || []) if (q.answer === null) items.push({
      key:`question/${m.id}/${q.id}`, kind:"question", mission:m.id, question:q.id,
      context:m.title, title:q.question, reason:q.reason});
    if (m.status === "awaiting_plan" && !(m.questions || []).some(q=>q.answer===null)) items.push({
      key:`plan/${m.id}`, kind:"plan", mission:m.id, project:m.project, context:m.title,
      title:"Schválit plán a zahájit práci?", tasks:m.tasks});
  }
  return items;
}
function focusApprovalInbox() {
  // Closing a dialog preserves its form DOM and drafts.
  $$("dialog[open]").forEach(dialog=>dialog.close());
  $("#approval-inbox").scrollIntoView({block:"nearest"});
  $("#approval-inbox").focus();
}
function approvalInboxBadges(count) {
  $("#approval-jump").hidden = count === 0;
  $("#approval-count").textContent = String(count);
  $("#approval-total").textContent = String(count);
  $("#approval-inbox").classList.toggle("needs-attention", count > 0);
  $("#approval-heading").textContent = count ? "Čeká na tvoje rozhodnutí" : "Schválení a odpovědi";
  document.title = count ? `(${count}) Čeká na tebe · Switch Studio` : "Switch Studio";
  for (const dialog of $$("dialog[open]")) {
    let shortcut = dialog.querySelector(".approval-dialog-shortcut");
    if (!shortcut) {
      shortcut = el("button", "button approval-dialog-shortcut"); shortcut.type = "button";
      shortcut.onclick = focusApprovalInbox; dialog.prepend(shortcut);
    }
    shortcut.hidden = count === 0;
    shortcut.textContent = `Čeká na tebe ${count} · Otevřít schválení →`;
  }
}
function approvalInboxCard(item) {
  const card = el("div", "approval-card"), controls = [];
  card.dataset.approvalKey = item.key;
  card.append(el("p", "approval-context", item.context),
    el("h3", "", item.kind === "tool" ? `Povolit ${item.request.name}?` : item.title));
  if (item.kind === "tool") {
    card.append(el("p", "", item.request.reason), el("pre", "", item.request.preview || item.request.target));
  } else if (item.reason) card.append(el("p", "", item.reason));
  if (item.kind === "plan") {
    const detail = el("details"), list = el("ol");
    detail.append(el("summary", "", "Zobrazit navržený plán"));
    for (const task of item.tasks || []) list.append(el("li", "", `${task.title}: ${task.instructions}`));
    detail.append(list); card.append(detail, el("p", "", "Ano zahájí realizaci. Ne ji pozastaví."));
  }
  const feedback = el("textarea"); feedback.rows = 2; feedback.maxLength = item.kind === "tool" ? 10000 : 12000;
  feedback.placeholder = "Napiš vlastní odpověď nebo pokyn…";
  feedback.setAttribute("aria-label", `Vlastní odpověď: ${item.context}`);
  if (item.kind !== "plan") card.append(feedback);
  const confirmation = el("input");
  if (item.request?.dangerous) {
    card.append(el("p", "approval-danger", `Zvýšené riziko: ${item.request.dangerous}`));
    confirmation.placeholder = "Pro povolení rizikové akce napiš yes";
    confirmation.setAttribute("aria-label", "Potvrzení rizikové akce"); card.append(confirmation);
  }
  const error = el("p", "mission-feedback"); error.setAttribute("role", "alert"); error.hidden = true;
  const actions = el("div", "actions");
  async function decide(choice) {
    if (card.dataset.busy === "true") return;
    error.hidden = true;
    if (choice === "custom" && !feedback.value.trim()) {
      error.textContent = "Napiš vlastní odpověď."; error.hidden = false; feedback.focus(); return;
    }
    card.dataset.busy = "true"; controls.forEach(b=>b.disabled=true); approvalInboxEpoch++;
    try {
      if (item.kind === "tool") await api("/api/decision", {
        run:item.run, approval:item.request.id, allow:choice==="yes",
        feedback:feedback.value, confirmation:confirmation.value});
      else if (item.kind === "question") await api("/api/missions/action", {
        id:item.mission, action:"answer", question:item.question,
        answer:choice==="custom" ? feedback.value : (choice==="yes" ? "Ano" : "Ne") + (feedback.value.trim() ? "\n"+feedback.value : "")});
      else await api("/api/missions/action", {id:item.mission, action:choice==="yes" ? "approve_plan" : "pause"});
      approvalInboxCards.delete(item.key); card.remove(); approvalInboxEpoch++;
      await pollApprovalInbox();
    } catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally { card.dataset.busy = "false"; controls.forEach(b=>b.disabled=false); }
  }
  for (const [choice,label] of [["yes","Ano"],["no","Ne"], ...(item.kind==="plan" ? [] : [["custom","Odeslat odpověď"]])]) {
    const button = el("button", "button"+(choice==="yes" ? " primary" : ""), label); button.type="button";
    button.onclick=()=>decide(choice); controls.push(button); actions.append(button);
  }
  card.append(actions,error);
  if (item.kind === "tool") card.append(el("p", "approval-help", "Vlastní odpověď tuto akci nepovolí; předá agentovi tvůj pokyn."));
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
  $("#approval-status").textContent = items.length ? "Agent potřebuje tvoje rozhodnutí. Vyber Ano, Ne nebo napiš odpověď." : "Žádný požadavek nečeká na tvoje rozhodnutí.";
}
async function pollApprovalInbox() {
  if (approvalInboxLoading || !state.data) return;
  approvalInboxLoading = true; const epoch = approvalInboxEpoch;
  try {
    const activeRuns = state.data.runs.filter(r=>["running","waiting"].includes(r.status));
    const [missions, ...runs] = await Promise.all([api("/api/missions"), ...activeRuns.map(async r=>{
      const event = await api(`/api/events?run=${r.id}&offset=${approvalInboxOffsets.get(r.id)||0}`);
      approvalInboxOffsets.set(r.id,event.offset);
      return {id:r.id,title:state.data.projects.find(p=>p.id===r.project)?.name || r.project,approvals:event.approvals};
    })]);
    if (epoch !== approvalInboxEpoch) return;
    renderApprovalInbox(approvalInboxItems(runs,missions.missions));
  } catch (error) {
    $("#approval-status").textContent = "Schválení nelze aktualizovat: " + error.message;
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
const flowStateLabels = {active:"Pracuje", done:"Ověřeno", waiting:"Čeká", blocked:"Překážka", pending:"Před námi", paused:"Pozastaveno"};
function flowModel(m, log = {}, now = Date.now()/1000) {
  const events = log.events || [], latest = events.at(-1), questions = (m.questions || []).filter(q=>q.answer === null);
  const attempt = (m.attempts || []).find(a=>a.id === m.active_attempt) || m.attempts?.at(-1);
  const live = m.status === "verifying" || !!m.active_attempt && m.status === "running";
  const age = now - (m.status === "verifying" ? m.updated || now : latest?.time || attempt?.started || m.updated || now);
  const denied = events.filter(e=>e.type === "note" && /blocked:|denied:|odmítnut/i.test(e.text || "")).at(-1);
  const error = events.filter(e=>e.type === "error" || e.type === "tool_result" && (e.is_error || /(?:^|\n\s*Info: )Blocked:/i.test(e.text || ""))).at(-1);
  const issue = [denied,error].filter(Boolean).sort((a,b)=>b.time-a.time)[0];
  const awaiting = log.approvals?.length > 0;
  const terminal = ["accepted","cancelled","expired"].includes(m.status);
  let tone = live ? "active" : "waiting", headline = missionLabels[m.status] || m.status, reason = m.message;
  if (m.status === "accepted") {tone="done"; headline="Výsledek převzat";}
  else if (["cancelled","paused","draft"].includes(m.status)) {tone="paused";}
  else if (m.status === "expired") {tone="blocked";}
  else if (questions.length) {tone="waiting"; headline="Čeká na tvoji odpověď"; reason=questions[0].question;}
  else if (awaiting) {tone="waiting"; headline="Čeká na schválení nástroje"; reason=`${log.approvals[0].name}: ${log.approvals[0].reason || log.approvals[0].target}`;}
  else if (m.status === "blocked") {tone="blocked"; headline="Práce je blokovaná";}
  else if (live && age > 180) {tone="waiting"; headline="Běh nemá čerstvou aktivitu"; reason=`Poslední událost před ${Math.floor(age/60)} min. Proces může čekat na model nebo nástroj; další postup není potvrzený.`;}
  else if (live && issue && m.status !== "verifying") {tone="blocked"; headline="Běh pokračuje, narazil na překážku"; reason=issue.text;}
  else if (live) {headline=m.status === "verifying" ? "Běží nezávislé kontroly" : m.phase === "review" ? "Reviewer kontroluje výsledek" : m.phase === "plan" ? "Vzniká plán práce" : "Agent právě pracuje";}
  const moving = live && tone === "active" && !log.loading;
  const nodes = [{id:"@plan",title:"Plán a zadání",kind:"plan",rank:0, status:m.phase === "plan" && !terminal ? tone : m.tasks.length ? "done" : "pending", subtitle:"Cíl → konkrétní úkoly"}];
  const ranks = new Map(), visiting = new Set(), tasks = m.tasks || [];
  function rank(t) {if(ranks.has(t.id)) return ranks.get(t.id); if(visiting.has(t.id)) return 1; visiting.add(t.id); const r=1+Math.max(0,...(t.depends_on || []).map(id=>{const dep=tasks.find(x=>x.id===id);return dep?rank(dep):0;}));visiting.delete(t.id);ranks.set(t.id,r);return r;}
  const edges=[];
  for(const t of tasks) {
    const current = attempt?.task === t.id && !["final","plan"].includes(m.phase) && !terminal && !["ready","awaiting_checks","verifying"].includes(m.status);
    const status = t.status === "done" ? "done" : current ? tone : "pending";
    nodes.push({id:t.id,title:t.title,kind:"task",rank:rank(t),status,task:t,current,subtitle:current && m.phase === "review" ? "Nezávislá kontrola" : t.status === "done" ? "Kontrola úkolu prošla" : `${t.depends_on.length ? "Návaznosti: " + t.depends_on.length : "První realizační krok"}`});
    for(const from of t.depends_on.length ? t.depends_on : ["@plan"]) edges.push({from,to:t.id});
  }
  const max=Math.max(0,...ranks.values());
  const checking=["verifying","awaiting_checks","ready","accepted"].includes(m.status) || m.phase === "final";
  nodes.push({id:"@checks",title:"Kontrola produktu",kind:"checks",rank:max+1,status:m.acceptance?.kind === "manual" ? "paused" : m.status === "ready" || m.status === "accepted" ? "done" : checking ? tone : "pending",subtitle:m.acceptance?.kind === "manual" ? "Převzato bez automatických kontrol" : "Review a nezávislé kontroly"});
  for(const t of tasks.filter(t=>!tasks.some(other=>other.depends_on.includes(t.id)))) edges.push({from:t.id,to:"@checks"});
  if(!tasks.length)edges.push({from:"@plan",to:"@checks"});
  nodes.push({id:"@accept",title:"Převzetí výsledku",kind:"accept",rank:max+2,label:m.status === "accepted" ? "Převzato" : null,status:m.status === "accepted" ? "done" : ["ready","awaiting_checks"].includes(m.status) ? "waiting" : "pending",subtitle:m.status === "accepted" ? "Výsledek je převzatý" : "Rozhodnutí o hotovém produktu"});
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
  const svg=flowSvg("svg",{viewBox:`0 0 ${width} ${height}`,width,height,role:"group","aria-label":"Průběh realizace a závislosti kroků"});
  const defs=flowSvg("defs"), marker=flowSvg("marker",{id:"flow-arrow",viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:5,markerHeight:5,orient:"auto-start-reverse"});marker.append(flowSvg("path",{d:"M 0 0 L 10 5 L 0 10 z",fill:"#687788"}));defs.append(marker);svg.append(defs);
  for(const edge of model.edges){const a=positions.get(edge.from),b=positions.get(edge.to);if(!a||!b)continue;const x=a.x+200,y=a.y+50,end=b.x;const target=model.nodes.find(n=>n.id===edge.to);svg.append(flowSvg("path",{d:`M${x},${y} C${x+16},${y} ${end-16},${b.y+50} ${end-5},${b.y+50}`,class:"flow-edge"+(model.moving && target.id===model.current.id ? " moving" : ""),"marker-end":"url(#flow-arrow)"}));}
  for(const n of model.nodes){const p=positions.get(n.id), g=flowSvg("g",{transform:`translate(${p.x},${p.y})`,class:`flow-node ${n.status}${flowState.node===n.id ? " selected" : ""}`,role:"button",tabindex:0,"aria-label":`${n.title}: ${(n.label || flowStateLabels[n.status])}. Otevřít detail.`});
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
  const n=model.nodes.find(n=>n.id===flowState.node)||model.current,panel=$("#flow-detail");panel.replaceChildren(el("span","eyebrow","VYBRANÝ KROK"),el("h3","",n.title),el("span",`flow-badge ${n.status}`,(n.label || flowStateLabels[n.status])));
  panel.append(el("p","",n.task?.instructions || (n.kind==="plan" ? m.goal : n.kind==="checks" ? "Reviewer zkontroluje produkt. Řadič potom spustí schválené nezávislé kontroly, pokud jsou nastavené." : "Hotový výsledek se přebírá až po kontrolách nebo výslovném ručním převzetí.")));
  if(n.task?.feedback)panel.append(el("p","flow-warning",n.task.feedback));
  if(n.current && model.issue)panel.append(el("p","flow-warning",model.issue.text));
  panel.append(el("p","missions-note",`Realizátor: ${m.profile} · Kontrolor: ${m.review_profile} · Běhů: ${m.attempts.length}/${m.max_attempts}`));
  panel.append(missionButton(model.questions.length ? "Odpovědět a odblokovat" : "Otevřít realizaci a výsledky",()=>flowOpenMission(m),true));
  if(m.active_attempt)panel.append(missionButton("Otevřít živý log / schválení",async()=>{$("#flow-dialog").close();$("#missions-dialog").close();$("#driver-dialog").close();await refreshState();await selectRun(m.active_attempt);$("#approval-inbox").scrollIntoView({block:"nearest"});}));
}
function renderFlow(m,log){
  const model=flowModel(m,log),summary=$("#flow-summary");
  if(flowState.follow || !model.nodes.some(n=>n.id===flowState.node))flowState.node=model.current.id;
  summary.className=`flow-summary ${model.tone}`;
  summary.replaceChildren(el("div","flow-signal"),el("div","flow-summary-copy"),el("div","flow-counter"));
  const copy=summary.children[1];copy.append(el("strong","",model.headline),el("p","",model.reason || "Čeká na další krok."));
  if(model.issue && model.reason!==model.issue.text && !["accepted","cancelled"].includes(m.status))copy.append(el("p","flow-warning","Poslední překážka: "+model.issue.text));
  summary.children[2].append(el("strong","",`${model.done} / ${model.total}`),el("small","","ověřených úkolů"));
  renderFlowGraph(m,model);renderFlowDetail(m,model);
  const activity=$("#flow-activity");activity.replaceChildren(el("span","eyebrow","SKUTEČNÉ UDÁLOSTI"),el("h3","","Poslední pohyb"));
  const meaningful=(log.events||[]).filter(e=>["tool_call","tool_result","approval","decision","error","final"].includes(e.type)||e.type==="note"&&/blocked:|denied:/i.test(e.text||""));
  if(!meaningful.length)activity.append(el("p","missions-note",log.loading ? "Načítám události běhu…" : "Zatím nejsou zaznamenané akce nástrojů."));
  for(const e of meaningful.slice(-6).reverse()){
    const row=el("div","flow-event"),when=el("time","",new Date(e.time*1000).toLocaleTimeString("cs-CZ"));
    const title=e.type==="tool_call" ? `Spuštění · ${e.name}` : e.type==="tool_result" ? `${e.is_error ? "Chyba" : "Odpověď nástroje"} · ${e.name}` : e.type==="approval" ? `Žádost o schválení · ${e.name}` : e.type==="decision" ? e.allow ? "Akce schválena" : "Akce zamítnuta" : e.type==="note" ? "Nástroj odmítnut" : e.type==="error" ? "Chyba běhu" : "Běh ukončen";
    row.append(when,el("span","",title));if(e.type==="note"||e.type==="error"){row.classList.add("issue");row.append(el("p","",e.text));}activity.append(row);
  }
  activity.append(el("p","missions-note",model.latest ? `Poslední událost: ${new Date(model.latest.time*1000).toLocaleTimeString("cs-CZ")}. Odpověď nástroje sama nepotvrzuje splnění úkolu.` : "Stav vychází z řadiče. Výsledky budou potvrzené až po kontrole."));
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
    const m=missions.find(m=>m.id===flowState.selection);if(!m){$("#flow-summary").textContent="Zatím tu není žádná realizace. Založ ji v Projektech AI nebo ve firmě.";$("#flow-canvas").replaceChildren();$("#flow-detail").replaceChildren();$("#flow-activity").replaceChildren();$("#flow-sync").textContent="Připojeno";return;}
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
    $("#flow-sync").textContent=data.controller_error ? "Chyba řadiče: "+data.controller_error : log.loading ? "Doháním historii…" : `Živě · obnoveno ${new Date().toLocaleTimeString("cs-CZ")}`;
    $("#flow-dialog").classList.remove("flow-offline");
  }catch(error){if(epoch===flowState.epoch){$("#flow-sync").textContent="Spojení přerušeno — zobrazený stav může být zastaralý. "+error.message;$("#flow-dialog").classList.add("flow-offline");}}
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
