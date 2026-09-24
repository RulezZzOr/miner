"use strict";
// Uses Studio's same-origin API, editor and run viewer; no worker runs in the browser.
let missionSelection = null;
let missionViewProject = null;
let missionLoading = false;
let missionSnapshot = "";
const missionAnswers = new Map();
const missionLabels = {
  draft: "Ready task brief", running: "Working", awaiting_plan: "Confirm plan",
  waiting: "I need an answer", paused: "Paused", blocked: "I need a decision",
  expired: "Time limit expired", ready: "Product ready for acceptance", accepted: "Accepted", cancelled: "Stopped",
  pending: "Pending", review: "Independent review", done: "Verified",
  verifying: "Independent verifications in progress", awaiting_checks: "Missing independent verifications",
};
const missionPhaseLabels = {plan:"Preparation", build:"Execution", review:"Review", final:"Product verification"};
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
  if (!m) { panel.append(el("p", "", "Create a project and describe the desired outcome.")); return; }
  panel.append(el("h3", "", m.title), el("span", "mission-state", missionLabels[m.status] || m.status));
  panel.append(el("p", "mission-message", m.message), el("p", "", m.goal));
  if (m.work_project) panel.append(missionButton("Open working version in editor", async () => {
    $("#missions-dialog").close(); await refreshState(); await selectProject(m.work_project);
  }));
  const criteria = el("ul");
  criteria.append(...m.criteria.map(c => el("li", "", c)));
  panel.append(criteria);
  panel.append(el("p", "missions-note", `${m.attempts.length}/${m.max_attempts} runs · limit ${m.days} days` +
    (m.deadline ? ` · until ${new Date(m.deadline * 1000).toLocaleString("en-GB")}` : "") +
    ` · work: ${m.profile} · review: ${m.review_profile}`));
  const actions = el("div", "mission-actions");
  actions.append(missionButton("Live progress map", () => showFlow(m.id)));
  const add = (label, action, primary) => actions.append(missionButton(label, () => missionAction(m.id, action), primary));
  if (m.status === "draft") add("Start preparation", "start", true);
  if (m.status === "awaiting_plan") add("Confirm plan and start work", "approve_plan", true);
  if (["running","waiting","awaiting_plan","verifying"].includes(m.status)) add("Pause", "pause");
  if (["paused","blocked"].includes(m.status)) add("Continue", "resume", true);
  if (m.status === "ready") { add("Accept product", "accept", true); add("Re-verify", "recheck"); }
  if (m.status === "accepted" && !m.product_context) actions.append(missionButton("Continue managing as product", () => adoptProduct(m), true));
  if (!["accepted","cancelled","expired"].includes(m.status)) add("End project", "cancel");
  if (m.active_attempt) actions.append(missionButton("Tool execution / approval", async () => {
    $("#missions-dialog").close();
    await refreshState();
    await selectRun(m.active_attempt);
  }));
  panel.append(actions);
  const latestReview = [...m.attempts].reverse().find(a => a.review_packet);
  if (latestReview) {
    const packet = latestReview.review_packet, box = el("details", "mission-task");
    box.append(el("summary", "", `Review evidence · ${(packet.bytes / 1024).toFixed(1)} KB · immutable snapshot`));
    box.append(el("p", "missions-note", `Reviewer: ${latestReview.model || latestReview.profile || "unrecorded"} · up to ${packet.limits.extra_reads} extra reads × ${packet.limits.bytes_per_read} bytes · at most 6 steps / 8 minutes (shorter project limits apply).`));
    if (latestReview.review_reads != null) box.append(el("p", "", `Additional evidence reads used: ${latestReview.review_reads}/${packet.limits.extra_reads}`));
    box.append(el("p", "", "File names are routing hints. Excerpts may be incomplete; missing evidence must be reported explicitly. Acceptance rejects changed evidence."));
    box.append(el("p", "missions-note", `Snapshot ${packet.snapshot} · packet ${packet.id.slice(0,16)} · ${packet.omitted_source_count} files omitted`));
    for (const [path, source] of Object.entries(packet.sources)) box.append(el("p", "", `${source.kind} · ${path} · ${source.bytes} bytes · ${source.sha256.slice(0,12)}`));
    panel.append(box);
  }
  if (m.decision) {
    const d = m.decision, box = el("details", "mission-task");
    box.append(el("summary", "", `Task selection · ${d.mode || "select"} · ${d.status}`));
    box.append(el("p", "", d.reason || "Waiting for decision."));
    box.append(el("p", "missions-note", `Provider: ${d.provider || m.decision_profile || "none"} · model: ${d.model || "unavailable"} · baseline: ${d.baseline || "plan order"} · proposal: ${d.choice || "pending"}`));
    if (d.alternatives) box.append(el("p", "", `Eligible alternatives: ${d.alternatives.join(", ")}`));
    if (d.confidence != null) box.append(el("p", "missions-note", `Confidence: ${d.confidence.toFixed(3)}${d.provider === "chat" ? " (self-reported; not calibrated)" : ""}`));
    if (d.elapsed_seconds != null) box.append(el("p", "missions-note", `Decision latency: ${d.elapsed_seconds.toFixed(2)} s`));
    box.append(missionButton("Compare recorded proposals", async () => {
      const metrics = await api(`/api/decision-metrics?id=${encodeURIComponent(m.id)}`);
      const summary = el("p", "decision-metrics", `${metrics.requests} requests · ${metrics.fallbacks} fallbacks · ${metrics.superseded} stale · ${metrics.disagreements}/${metrics.comparisons} proposals differ from plan order. ${metrics.note}`);
      box.querySelector(".decision-metrics")?.remove(); box.append(summary);
    }));
    panel.append(box);
  }
  if (["draft", "paused", "blocked"].includes(m.status) && !m.active_attempt) {
    const details = el("details", "mission-task");
    details.append(el("summary", "", "Change models and continuation limits"));
    const form = el("form");
    for (const [name, title] of [["profile", "Model for work"], ["review_profile", "Review model"]]) {
      const label = el("label", "", title), select = el("select"); select.name = name;
      select.append(...state.data.profiles.map(p => { const option = el("option", "", `${p.model} · ${p.id}`); option.value = p.id; return option; }));
      select.value = m[name]; label.append(select); form.append(label);
    }
    const decisionLabel = el("label", "", "Decision provider (optional)"), decisionSelect = el("select");
    decisionSelect.name = "decision_profile"; populateDecisionModels(decisionSelect); decisionSelect.value = m.decision_profile || "";
    decisionLabel.append(decisionSelect); form.append(decisionLabel);
    const modeLabel = el("label", "", "Decision mode"), modeSelect = el("select"); modeSelect.name = "decision_mode";
    for (const [value, title] of [["off", "Off · no request"], ["shadow", "Shadow · compare only"], ["select", "Select · eligible tasks only"]]) {
      const option = el("option", "", title); option.value = value; modeSelect.append(option);
    }
    modeSelect.value = m.decision_mode || (m.decision_profile ? "select" : "off"); modeLabel.append(modeSelect); form.append(modeLabel);
    form.append(el("p", "missions-note", "Shadow keeps plan order. TypeSafe is an optional cloud service: enabling it sends the compact goal and eligible task summaries; the server needs TYPESAFE_API_KEY. Local profiles use their configured endpoint."));
    for (const [name, title, min, max] of [["attempt_minutes", "Minutes per run", 1, 360],
      ["max_turns", "Steps per run", 1, 200], ["max_attempts", "Total number of runs", Math.max(2, m.attempts.length + 1), 1000]]) {
      const label = el("label", "", title), input = el("input");
      input.type = "number"; input.name = name; input.min = min; input.max = max; input.value = m[name]; input.required = true;
      label.append(input); form.append(label);
    }
    form.oninput = () => form.dataset.dirty = "true";
    form.append(el("p", "missions-note", "Applies only to future runs. Saved results and overall deadline remain unchanged. The review model may be the same provider in a new session."));
    const save = el("button", "button", "Save models and limits"); save.type = "submit"; form.append(save);
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
    details.append(el("summary", "", "Independent checks"));
    const form = el("form");
    const commands = el("textarea"); commands.rows = 3;
    commands.placeholder = "npm test\nnpm run build";
    commands.setAttribute("aria-label", "Commands for independent checks");
    commands.value = (m.verification_checks || []).map(c => c.argv.map(a => "'" + a.replaceAll("'", "'\\''") + "'").join(" ")).join("\n");
    commands.oninput = () => commands.dataset.dirty = "true";
    form.append(el("p", "missions-note", "Each line is a separate command. Runs in the working directory, without shell operators, with a 5-minute time limit. Only enable commands you truly intend to run."), commands);
    const save = el("button", "button", "Approve check commands"); save.type = "submit"; form.append(save);
    form.onsubmit = async event => {
      event.preventDefault(); save.disabled = true;
      try { await missionAction(m.id, "set_checks", {verification_checks: commands.value}); }
      catch (error) { toast(error.message, true); save.disabled = false; }
    };
    details.append(form); panel.append(details);
    if (m.status === "awaiting_checks") {
      const manual = el("label", "mission-permission");
      const acknowledged = el("input"); acknowledged.type = "checkbox";
      manual.append(acknowledged, document.createTextNode(" I have manually verified the result and am accepting it without automatic checks."));
      const accept = missionButton("Accept manually", () => missionAction(m.id, "manual_accept", {acknowledge_unverified: true}));
      accept.disabled = true; acknowledged.onchange = () => accept.disabled = !acknowledged.checked;
      panel.append(manual, accept);
    }
  }
  const verificationId = m.verification_id || m.verification_result?.id;
  if (verificationId) {
    const evidence = el("details", "mission-task");
    evidence.append(el("summary", "", "Actual checks performed and logs"));
    evidence.append(missionButton("Load check results", async () => {
      const record = await api(`/api/verification?id=${encodeURIComponent(verificationId)}`);
      const output = el("pre");
      output.textContent = `${record.status}${record.error ? ": " + record.error : ""}\n` + record.checks.map(c => `${c.label} · exit ${c.exit_code}\n${c.log}`).join("\n\n");
      evidence.querySelector("pre")?.remove(); evidence.append(output);
    }));
    panel.append(evidence);
  }
  const timeline = el("details", "mission-task");
  timeline.append(el("summary", "", "Why the process changed · decisions and evidence"));
  timeline.append(missionButton("Load decision history", async () => {
    const data = await api(`/api/mission-trace?id=${encodeURIComponent(m.id)}`);
    const history = el("div", "mission-timeline");
    for (const event of data.events) {
      const item = el("details");
      item.append(el("summary", "", `${new Date(event.at * 1000).toLocaleString("en-GB")} · ${missionLabels[event.to] || event.to}`));
      item.append(el("p", "", event.reason));
      if (event.decision) item.append(el("p", "", `Task selection (${event.decision.status}): ${event.decision.reason}`));
      if (event.runtime) item.append(el("p", "missions-note", `Work: ${event.runtime.profile} · review: ${event.runtime.review_profile} · ${event.runtime.attempt_minutes} minutes / run`));
      if (event.phase) item.append(el("p", "missions-note", `Phase: ${missionPhaseLabels[event.phase] || event.phase} · run ${event.attempt}`));
      if (event.elapsed_seconds !== null && event.elapsed_seconds !== undefined) item.append(el("p", "", `Run duration: ${Math.round(event.elapsed_seconds)} s`));
      item.append(el("p", "missions-note", event.usage
        ? `${event.usage.estimated ? "Estimate" : "Reported by provider"}: ${event.usage.input} input / ${event.usage.output} output tokens. Monetary cost is not specified.`
        : "Token consumption is not available."));
      if (event.inputs.verification) item.append(el("p", "missions-note", `Independent check: ${event.inputs.verification}`));
      if (event.inputs.report_sha256) item.append(el("p", "missions-note", `Report hash: ${event.inputs.report_sha256.slice(0,16)}`));
      for (const change of event.file_changes) item.append(el("p", "", `${change.before ? change.after ? "Changed" : "Removed" : "Added"}: ${change.path}`));
      history.append(item);
    }
    timeline.querySelector(".mission-timeline")?.remove(); timeline.append(history);
  }));
  panel.append(timeline);
  const open = m.questions.filter(q => q.answer === null);
  if (open.length) panel.append(el("h3", "", "I need from you"));
  for (const q of open) {
    const card = el("form", "mission-question");
    card.append(el("strong", "", q.question), el("p", "", q.reason));
    if (q.task) card.append(el("p", "missions-note", `Blocks task: ${q.task}`));
    const answer = el("textarea");
    answer.required = true;
    answer.maxLength = 12000;
    answer.rows = 3;
    const answerKey = `${m.id}/${q.id}`;
    answer.value = missionAnswers.get(answerKey) || "";
    answer.oninput = () => missionAnswers.set(answerKey, answer.value);
    answer.setAttribute("aria-label", `Response: ${q.question}`);
    card.append(answer);
    const submit = el("button", "button primary", "Save response");
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
    answered.append(el("summary", "", "Saved decisions and responses"));
    for (const q of m.questions.filter(q => q.answer !== null)) answered.append(el("p", "", `${q.question}\n${q.answer}`));
    panel.append(answered);
  }
  if (m.tasks.length) panel.append(el("h3", "", "Plan and results"));
  if (m.plan_revisions?.length) {
    const revisions=el("details","mission-task");revisions.append(el("summary","","Task brief edit history"));
    for(const change of m.plan_revisions){revisions.append(el("p","",`${new Date(change.at*1000).toLocaleString("en-GB")} · ${change.task} · ${change.reason}`),el("pre","",`Before: ${change.before.criteria.join("\n")}\n\nAfter: ${change.after.criteria.join("\n")}`));}
    panel.append(revisions);
  }
  for (const task of m.tasks) {
    const card = el("details", "mission-task");
    card.append(el("summary", "", `${task.title} · ${missionLabels[task.status] || task.status}`));
    card.append(el("p", "", task.instructions));
    card.append(el("p", "missions-note", `Dependencies: ${task.depends_on.join(", ") || "none"}`));
    const list = el("ul"); list.append(...task.criteria.map(c => el("li", "", c))); card.append(list);
    if (task.feedback) card.append(el("p", "mission-feedback", task.feedback));
    if (task.review_summary) card.append(el("p", "", task.review_summary));
    for (const check of task.review_checks || []) card.append(el("p", "missions-note", `${check.outcome || (check.passed ? "passed (legacy)" : "failed")} · ${check.issue || "unspecified"} · ${check.criterion}: ${check.evidence}`));
    if (["paused", "blocked"].includes(m.status) && !m.active_attempt && ["pending", "waiting"].includes(task.status)) {
      const form = el("form", "mission-revision");
      form.append(el("p", "missions-note", "Edit the task brief without overwriting history. For corporate execution, first pause the Driver. Original product goals remain preserved."));
      const instructions = el("textarea"), criteria = el("textarea"), reason = el("textarea");
      for (const [input, label, value] of [[instructions,"Task instructions",task.instructions], [criteria,"Task criteria, one per line",task.criteria.join("\n")], [reason,"Reason for change",""]]) {
        input.value=value; input.required=true; input.rows=3; input.setAttribute("aria-label",label);
        input.oninput=()=>form.dataset.dirty="true";
        const wrapper=el("label","",label);wrapper.append(input);form.append(wrapper);
      }
      const save=el("button","button","Save corrected task brief");save.type="submit";form.append(save);
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
    panel.append(el("h3", "", "Final model review"), el("p", "", m.final_report.summary));
    for (const check of m.final_report.checks) panel.append(el("p", "", `${check.criterion}: ${check.evidence}`));
  }
  if (m.attempts.length) {
    const attempts = el("details", "mission-attempts");
    attempts.append(el("summary", "", `Run and evidence history (${m.attempts.length})`));
    for (const a of [...m.attempts].reverse()) {
      const row = el("div", "mission-attempt");
      row.append(el("p", "", `${missionPhaseLabels[a.phase]}${a.task ? " · " + a.task : ""} · ${new Date(a.started*1000).toLocaleString("en-GB")}`));
      if (a.error) row.append(el("p", "mission-feedback", a.error));
      if (a.report) row.append(missionButton("Open report", async () => {
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
      ? `Controller requires attention: ${data.controller_error}`
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
        if (hasDraft) { toast("First, save the partially completed response."); return; }
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
    if (state.dirty) throw new Error("First, save the open file in the editor.");
    const selected = state.project;
    const m = await api("/api/missions", {
      project:selected, title:$("#mission-title").value, goal:$("#mission-goal").value,
      criteria:$("#mission-criteria").value.split("\n").map(v => v.trim()).filter(Boolean),
      verification_checks:$("#mission-checks").value,
      sources:$("#mission-sources").value, constraints:$("#mission-constraints").value,
      profile:$("#mission-profile").value, review_profile:$("#mission-review-profile").value,
      decision_profile:$("#mission-decision-profile").value,
      decision_mode:$("#mission-decision-profile").value ? $("#mission-decision-mode").value : "off",
      days:Number($("#mission-days").value), max_attempts:Number($("#mission-attempts").value),
      attempt_minutes:Number($("#mission-minutes").value), max_turns:Number($("#mission-turns").value),
      auto_approve:$("#mission-auto").checked,
    });
    if (state.project !== selected) return;
    missionSelection = m.id;
    $("#mission-new").open = false;
    $("#mission-form").reset();
    await loadMissions(true);
    toast("Task brief saved. You will start preparation using a separate button.");
  });
  setInterval(() => loadMissions().catch(error => toast(error.message, true)), 3000);
}

function fillDecisionModels(id) {
  const select = $(id), previous = select.value;
  populateDecisionModels(select);
  select.value = previous;
}
function populateDecisionModels(select) {
  const none = el("option", "", "Plan order · no further model"); none.value = "";
  select.replaceChildren(none, ...state.data.profiles.filter(p => p.protocol === "chat_completions" && !p.oauth_provider).map(p => {
    const option = el("option", "", `${p.model} · decision pilot`); option.value = p.id; return option;
  }));
  const cloud = el("option", "", "TypeSafe Jev · optional cloud (sends task summaries)"); cloud.value = "typesafe:jev-latest"; select.append(cloud);
}
