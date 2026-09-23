"use strict";
// Uses Studio's same-origin API, editor and run viewer; no worker runs in the browser.
let missionSelection = null;
let missionViewProject = null;
let missionLoading = false;
let missionSnapshot = "";
const missionAnswers = new Map();
const missionLabels = {
  draft: "Připravené zadání", running: "Pracuje", awaiting_plan: "Potvrď plán",
  waiting: "Potřebuji odpověď", paused: "Pozastaveno", blocked: "Potřebuji rozhodnutí",
  expired: "Vypršel limit", ready: "Produkt k převzetí", accepted: "Převzato", cancelled: "Zastaveno",
  pending: "Čeká", review: "Nezávislá kontrola", done: "Zkontrolováno",
  verifying: "Probíhají nezávislé kontroly", awaiting_checks: "Chybí nezávislé kontroly",
};
const missionPhaseLabels = {plan:"Příprava", build:"Realizace", review:"Kontrola", final:"Kontrola produktu"};
function missionButton(label, fn, primary = false) {
  const button = el("button", "button" + (primary ? " primary" : ""), label);
  button.type = "button";
  button.onclick = async () => {
    button.disabled = true;
    try { await fn(); } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  };
  return button;
}
async function missionAction(id, action, extra = {}) {
  await api("/api/missions/action", {id, action, ...extra});
  await loadMissions(true);
}
function renderMission(m) {
  const panel = $("#mission-detail");
  panel.replaceChildren();
  if (!m) { panel.append(el("p", "", "Založ projekt a popiš požadovaný výsledek.")); return; }
  panel.append(el("h3", "", m.title), el("span", "mission-state", missionLabels[m.status] || m.status));
  panel.append(el("p", "mission-message", m.message), el("p", "", m.goal));
  if (m.work_project) panel.append(missionButton("Otevřít pracovní verzi v editoru", async () => {
    $("#missions-dialog").close(); await refreshState(); await selectProject(m.work_project);
  }));
  const criteria = el("ul");
  criteria.append(...m.criteria.map(c => el("li", "", c)));
  panel.append(criteria);
  panel.append(el("p", "missions-note", `${m.attempts.length}/${m.max_attempts} běhů · limit ${m.days} dní` +
    (m.deadline ? ` · do ${new Date(m.deadline * 1000).toLocaleString("cs-CZ")}` : "") +
    ` · práce: ${m.profile} · kontrola: ${m.review_profile}`));
  const actions = el("div", "mission-actions");
  actions.append(missionButton("Živá mapa průběhu", () => showFlow(m.id)));
  const add = (label, action, primary) => actions.append(missionButton(label, () => missionAction(m.id, action), primary));
  if (m.status === "draft") add("Spustit přípravu", "start", true);
  if (m.status === "awaiting_plan") add("Potvrdit plán a zahájit práci", "approve_plan", true);
  if (["running","waiting","awaiting_plan","verifying"].includes(m.status)) add("Pozastavit", "pause");
  if (["paused","blocked"].includes(m.status)) add("Pokračovat", "resume", true);
  if (m.status === "ready") { add("Převzít produkt", "accept", true); add("Znovu ověřit", "recheck"); }
  if (m.status === "accepted" && !m.product_context) actions.append(missionButton("Dál spravovat jako produkt", () => adoptProduct(m), true));
  if (!["accepted","cancelled","expired"].includes(m.status)) add("Ukončit projekt", "cancel");
  if (m.active_attempt) actions.append(missionButton("Průběh / schválení nástrojů", async () => {
    $("#missions-dialog").close();
    await refreshState();
    await selectRun(m.active_attempt);
  }));
  panel.append(actions);
  if (["draft", "paused", "blocked"].includes(m.status) && !m.active_attempt) {
    const details = el("details", "mission-task");
    details.append(el("summary", "", "Změnit modely a limity pokračování"));
    const form = el("form");
    for (const [name, title] of [["profile", "Model pro práci"], ["review_profile", "Model pro kontrolu"]]) {
      const label = el("label", "", title), select = el("select"); select.name = name;
      select.append(...state.data.profiles.map(p => { const option = el("option", "", `${p.model} · ${p.id}`); option.value = p.id; return option; }));
      select.value = m[name]; label.append(select); form.append(label);
    }
    for (const [name, title, min, max] of [["attempt_minutes", "Minut na jeden běh", 1, 360],
      ["max_turns", "Kroků na jeden běh", 1, 200], ["max_attempts", "Celkový počet běhů", Math.max(2, m.attempts.length + 1), 1000]]) {
      const label = el("label", "", title), input = el("input");
      input.type = "number"; input.name = name; input.min = min; input.max = max; input.value = m[name]; input.required = true;
      label.append(input); form.append(label);
    }
    form.oninput = () => form.dataset.dirty = "true";
    form.append(el("p", "missions-note", "Platí až pro další běhy. Uložené výsledky a celkový termín zůstanou zachované. Model pro kontrolu může být stejný poskytovatel v nové relaci."));
    const save = el("button", "button", "Uložit modely a limity"); save.type = "submit"; form.append(save);
    form.onsubmit = async event => {
      event.preventDefault(); save.disabled = true;
      try {
        const values = Object.fromEntries(new FormData(form));
        for (const name of ["attempt_minutes", "max_turns", "max_attempts"]) values[name] = Number(values[name]);
        await missionAction(m.id, "runtime_settings", values);
      } catch (error) { toast(error.message, true); save.disabled = false; }
    };
    details.append(form); panel.append(details);
  }
  if (["draft","paused","awaiting_plan","awaiting_checks","ready"].includes(m.status)) {
    const details = el("details", "mission-task");
    details.open = m.status === "awaiting_checks";
    details.append(el("summary", "", "Nezávislé kontroly"));
    const form = el("form");
    const commands = el("textarea"); commands.rows = 3;
    commands.placeholder = "npm test\nnpm run build";
    commands.setAttribute("aria-label", "Příkazy nezávislých kontrol");
    commands.value = (m.verification_checks || []).map(c => c.argv.map(a => "'" + a.replaceAll("'", "'\\''") + "'").join(" ")).join("\n");
    commands.oninput = () => commands.dataset.dirty = "true";
    form.append(el("p", "missions-note", "Každý řádek je samostatný příkaz. Běží v pracovní složce, bez shellových operátorů, s limitem 5 minut. Povol pouze příkazy, které chceš skutečně spustit."), commands);
    const save = el("button", "button", "Schválit příkazy kontrol"); save.type = "submit"; form.append(save);
    form.onsubmit = async event => {
      event.preventDefault(); save.disabled = true;
      try { await missionAction(m.id, "set_checks", {verification_checks: commands.value}); }
      catch (error) { toast(error.message, true); save.disabled = false; }
    };
    details.append(form); panel.append(details);
    if (m.status === "awaiting_checks") {
      const manual = el("label", "mission-permission");
      const acknowledged = el("input"); acknowledged.type = "checkbox";
      manual.append(acknowledged, document.createTextNode(" Výsledek jsem zkontroloval ručně a přebírám jej bez automatických kontrol."));
      const accept = missionButton("Převzít ručně", () => missionAction(m.id, "manual_accept", {acknowledge_unverified: true}));
      accept.disabled = true; acknowledged.onchange = () => accept.disabled = !acknowledged.checked;
      panel.append(manual, accept);
    }
  }
  const verificationId = m.verification_id || m.verification_result?.id;
  if (verificationId) {
    const evidence = el("details", "mission-task");
    evidence.append(el("summary", "", "Skutečně spuštěné kontroly a logy"));
    evidence.append(missionButton("Načíst výsledek kontrol", async () => {
      const record = await api(`/api/verification?id=${encodeURIComponent(verificationId)}`);
      const output = el("pre");
      output.textContent = `${record.status}${record.error ? ": " + record.error : ""}\n` + record.checks.map(c => `${c.label} · návratový kód ${c.exit_code}\n${c.log}`).join("\n\n");
      evidence.querySelector("pre")?.remove(); evidence.append(output);
    }));
    panel.append(evidence);
  }
  const timeline = el("details", "mission-task");
  timeline.append(el("summary", "", "Proč se postup změnil · rozhodnutí a důkazy"));
  timeline.append(missionButton("Načíst historii rozhodnutí", async () => {
    const data = await api(`/api/mission-trace?id=${encodeURIComponent(m.id)}`);
    const history = el("div", "mission-timeline");
    for (const event of data.events) {
      const item = el("details");
      item.append(el("summary", "", `${new Date(event.at * 1000).toLocaleString("cs-CZ")} · ${missionLabels[event.to] || event.to}`));
      item.append(el("p", "", event.reason));
      if (event.decision) item.append(el("p", "", `Výběr úkolu (${event.decision.status}): ${event.decision.reason}`));
      if (event.runtime) item.append(el("p", "missions-note", `Práce: ${event.runtime.profile} · kontrola: ${event.runtime.review_profile} · ${event.runtime.attempt_minutes} minut / běh`));
      if (event.phase) item.append(el("p", "missions-note", `Fáze: ${missionPhaseLabels[event.phase] || event.phase} · běh ${event.attempt}`));
      if (event.elapsed_seconds !== null && event.elapsed_seconds !== undefined) item.append(el("p", "", `Doba běhu: ${Math.round(event.elapsed_seconds)} s`));
      item.append(el("p", "missions-note", event.usage
        ? `${event.usage.estimated ? "Odhad" : "Hlášeno poskytovatelem"}: ${event.usage.input} vstupních / ${event.usage.output} výstupních tokenů. Peněžní cena není určena.`
        : "Spotřeba tokenů není dostupná."));
      if (event.inputs.verification) item.append(el("p", "missions-note", `Nezávislá kontrola: ${event.inputs.verification}`));
      if (event.inputs.report_sha256) item.append(el("p", "missions-note", `Otisk reportu: ${event.inputs.report_sha256.slice(0,16)}`));
      for (const change of event.file_changes) item.append(el("p", "", `${change.before ? change.after ? "Změněno" : "Odstraněno" : "Přidáno"}: ${change.path}`));
      history.append(item);
    }
    timeline.querySelector(".mission-timeline")?.remove(); timeline.append(history);
  }));
  panel.append(timeline);
  const open = m.questions.filter(q => q.answer === null);
  if (open.length) panel.append(el("h3", "", "Potřebuji od tebe"));
  for (const q of open) {
    const card = el("form", "mission-question");
    card.append(el("strong", "", q.question), el("p", "", q.reason));
    if (q.task) card.append(el("p", "missions-note", `Blokuje úkol: ${q.task}`));
    const answer = el("textarea");
    answer.required = true;
    answer.maxLength = 12000;
    answer.rows = 3;
    const answerKey = `${m.id}/${q.id}`;
    answer.value = missionAnswers.get(answerKey) || "";
    answer.oninput = () => missionAnswers.set(answerKey, answer.value);
    answer.setAttribute("aria-label", `Odpověď: ${q.question}`);
    card.append(answer);
    const submit = el("button", "button primary", "Uložit odpověď");
    submit.type = "submit";
    card.append(submit);
    card.onsubmit = async event => {
      event.preventDefault();
      submit.disabled = true;
      try {
        await api("/api/missions/action", {id:m.id, action:"answer", question:q.id, answer:answer.value});
        missionAnswers.delete(answerKey);
        await loadMissions(true);
      }
      catch (error) { toast(error.message, true); submit.disabled = false; }
    };
    panel.append(card);
  }
  if (m.questions.some(q => q.answer !== null)) {
    const answered = el("details", "mission-answered");
    answered.append(el("summary", "", "Uložená rozhodnutí a odpovědi"));
    for (const q of m.questions.filter(q => q.answer !== null)) answered.append(el("p", "", `${q.question}\n${q.answer}`));
    panel.append(answered);
  }
  if (m.tasks.length) panel.append(el("h3", "", "Plán a výsledky"));
  if (m.plan_revisions?.length) {
    const revisions=el("details","mission-task");revisions.append(el("summary","","Historie úprav zadání"));
    for(const change of m.plan_revisions){revisions.append(el("p","",`${new Date(change.at*1000).toLocaleString("cs-CZ")} · ${change.task} · ${change.reason}`),el("pre","",`Před: ${change.before.criteria.join("\n")}\n\nPo: ${change.after.criteria.join("\n")}`));}
    panel.append(revisions);
  }
  for (const task of m.tasks) {
    const card = el("details", "mission-task");
    card.append(el("summary", "", `${task.title} · ${missionLabels[task.status] || task.status}`));
    card.append(el("p", "", task.instructions));
    card.append(el("p", "missions-note", `Závislosti: ${task.depends_on.join(", ") || "žádné"}`));
    const list = el("ul"); list.append(...task.criteria.map(c => el("li", "", c))); card.append(list);
    if (task.feedback) card.append(el("p", "mission-feedback", task.feedback));
    if (task.review_summary) card.append(el("p", "", task.review_summary));
    if (["paused", "blocked"].includes(m.status) && !m.active_attempt && ["pending", "waiting"].includes(task.status)) {
      const form = el("form", "mission-revision");
      form.append(el("p", "missions-note", "Upravit zadání bez přepsání historie. U firemní realizace nejdřív pozastav Řadič. Původní cíle produktu zůstanou zachované."));
      const instructions = el("textarea"), criteria = el("textarea"), reason = el("textarea");
      for (const [input, label, value] of [[instructions,"Instrukce úkolu",task.instructions], [criteria,"Kritéria úkolu, jedno na řádek",task.criteria.join("\n")], [reason,"Důvod změny",""]]) {
        input.value=value; input.required=true; input.rows=3; input.setAttribute("aria-label",label);
        input.oninput=()=>form.dataset.dirty="true";
        const wrapper=el("label","",label);wrapper.append(input);form.append(wrapper);
      }
      const save=el("button","button","Uložit opravené zadání");save.type="submit";form.append(save);
      form.onsubmit=async e=>{e.preventDefault();save.disabled=true;try{await missionAction(m.id,"revise_task",{task:task.id,expected_criteria:task.criteria,instructions:instructions.value,criteria:criteria.value.split("\n").map(x=>x.trim()).filter(Boolean),reason:reason.value});}catch(error){toast(error.message,true);save.disabled=false;}};
      card.append(form);
    }
    for (const file of task.artifacts) card.append(missionButton(file.path, async () => {
      $("#missions-dialog").close(); await refreshState();
      await selectProject(m.work_project || m.project); await openFile(file.path);
    }));
    panel.append(card);
  }
  if (m.final_report) {
    panel.append(el("h3", "", "Závěrečné review modelu"), el("p", "", m.final_report.summary));
    for (const check of m.final_report.checks) panel.append(el("p", "", `${check.criterion}: ${check.evidence}`));
  }
  if (m.attempts.length) {
    const attempts = el("details", "mission-attempts");
    attempts.append(el("summary", "", `Historie běhů a důkazů (${m.attempts.length})`));
    for (const a of [...m.attempts].reverse()) {
      const row = el("div", "mission-attempt");
      row.append(el("p", "", `${missionPhaseLabels[a.phase]}${a.task ? " · " + a.task : ""} · ${new Date(a.started*1000).toLocaleString("cs-CZ")}`));
      if (a.error) row.append(el("p", "mission-feedback", a.error));
      if (a.report) row.append(missionButton("Otevřít report", async () => {
        $("#missions-dialog").close(); await refreshState();
        await selectProject(m.work_project || m.project); await openFile(a.report);
      }));
      attempts.append(row);
    }
    panel.append(attempts);
  }
}
async function loadMissions(force = false) {
  if (missionLoading || !$("#missions-dialog").open) return;
  missionLoading = true;
  const selected = state.project;
  try {
    const data = await api(`/api/missions?project=${encodeURIComponent(selected)}`);
    if (selected !== state.project || !$("#missions-dialog").open) return;
    $("#missions-health").textContent = data.controller_error
      ? `Řadič vyžaduje pozornost: ${data.controller_error}`
      : `${data.capabilities.controller} ${data.capabilities.search_status}`;
    // Polling must never replace answers the user is currently writing.
    const hasDraft = $$(".mission-question textarea").some(input => input.value.length > 0) ||
      $$("#mission-detail textarea, #mission-detail form").some(input => input.dataset.dirty === "true");
    const signature = JSON.stringify(data.missions);
    if (!force && (hasDraft || signature === missionSnapshot)) return;
    missionSnapshot = signature;
    if (!data.missions.some(m => m.id === missionSelection)) missionSelection = data.missions[0]?.id || null;
    const list = $("#mission-list"); list.replaceChildren();
    for (const m of data.missions) {
      const button = missionButton(`${m.title} · ${missionLabels[m.status] || m.status}`, async () => {
        if (hasDraft) { toast("Nejdřív ulož rozepsanou odpověď."); return; }
        missionSelection = m.id; missionSnapshot = ""; await loadMissions(true);
      });
      button.classList.toggle("active", m.id === missionSelection);
      list.append(button);
    }
    renderMission(data.missions.find(m => m.id === missionSelection));
  } finally { missionLoading = false; }
}
async function showMissions() {
  fillDecisionModels("#mission-decision-profile");
  if (missionViewProject !== state.project) {
    missionSelection = null; missionSnapshot = "";
    $("#mission-form").reset();
    missionViewProject = state.project;
  }
  for (const id of ["#mission-profile", "#mission-review-profile"]) {
    const previous = $(id).value || $("#model-select").value;
    $(id).replaceChildren(...state.data.profiles.map(p => {
      const option = el("option", "", `${p.model} · ${profileLabel(p)}`); option.value = p.id; return option;
    }));
    $(id).value = previous;
  }
  $("#missions-dialog").showModal();
  await loadMissions(true);
}
function initMissions() {
  bind("#missions-button", "click", showMissions);
  bind("#mission-form", "submit", async event => {
    event.preventDefault();
    if (state.dirty) throw new Error("Nejdřív ulož otevřený soubor v editoru.");
    const selected = state.project;
    const m = await api("/api/missions", {
      project:selected, title:$("#mission-title").value, goal:$("#mission-goal").value,
      criteria:$("#mission-criteria").value.split("\n").map(v => v.trim()).filter(Boolean),
      verification_checks:$("#mission-checks").value,
      sources:$("#mission-sources").value, constraints:$("#mission-constraints").value,
      profile:$("#mission-profile").value, review_profile:$("#mission-review-profile").value,
      decision_profile:$("#mission-decision-profile").value,
      days:Number($("#mission-days").value), max_attempts:Number($("#mission-attempts").value),
      attempt_minutes:Number($("#mission-minutes").value), max_turns:Number($("#mission-turns").value),
      auto_approve:$("#mission-auto").checked,
    });
    if (state.project !== selected) return;
    missionSelection = m.id;
    $("#mission-new").open = false;
    $("#mission-form").reset();
    await loadMissions(true);
    toast("Zadání uloženo. Přípravu spustíš samostatným tlačítkem.");
  });
  setInterval(() => loadMissions().catch(error => toast(error.message, true)), 3000);
}

function fillDecisionModels(id) {
  const select = $(id), previous = select.value;
  const none = el("option", "", "Pořadí plánu · bez dalšího modelu"); none.value = "";
  select.replaceChildren(none, ...state.data.profiles.filter(p => p.protocol === "chat_completions" && !p.oauth_provider).map(p => {
    const option = el("option", "", `${p.model} · pilot rozhodování`); option.value = p.id; return option;
  }));
  select.value = previous;
}
