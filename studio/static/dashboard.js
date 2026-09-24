"use strict";
// A read-only overview and a small composer over the existing task APIs.
let dashboardData = null, dashboardLoading = false, dashboardSubmitting = false;
let dashboardSnapshot = "", dashboardLastUpdated = null;
const dashboardClosed = new Set(["done", "accepted", "cancelled", "expired", "rejected"]);
function showDashboard(visible = true) {
  document.body.classList.toggle("dashboard-home", visible);
  $("#dashboard-button").setAttribute("aria-pressed", String(visible));
  $("#dashboard-button").classList.toggle("primary", visible);
  $("#workspace-button").classList.toggle("primary", !visible);
  if (visible) loadDashboard();
}
function dashboardOptions(select, entries, fallback) {
  const signature = JSON.stringify(entries);
  if (select.dataset.options === signature) return;
  const previous = select.value;
  select.replaceChildren(...entries.map(([value, title]) => {
    const option = el("option", "", title); option.value = value; return option;
  }));
  select.value = entries.some(([id]) => id === previous) ? previous : (fallback ?? entries[0]?.[0] ?? "");
  select.dataset.options = signature;
}
function dashboardCompany() {
  return dashboardData?.companies.find(c => c.id === $("#dashboard-destination").value);
}
function dashboardRouting(reset = false) {
  if (!dashboardData || dashboardSubmitting) return;
  const projects = state.data.projects;
  dashboardOptions($("#dashboard-project"), projects.map(p => [p.id, p.name]), state.project);
  const projectId = $("#dashboard-project").value;
  const companies = dashboardData.companies.filter(c => c.projects.includes(projectId));
  const destination = $("#dashboard-destination");
  if (reset) { destination.dataset.options = ""; destination.value = ""; }
  dashboardOptions(destination, [...companies.map(c => [c.id, c.name]), ["run", "Standalone task"]]);
  const company = dashboardCompany(), who = $("#dashboard-who");
  const kind = company ? "department" : "model";
  if (who.dataset.kind !== kind) { who.dataset.options = ""; who.value = ""; who.dataset.kind = kind; }
  dashboardOptions(who, company ? Object.entries(dashboardData.departments) : state.data.profiles.map(p => [p.id, `${p.model} · ${p.id}`]), company ? "delivery" : state.data.default_profile);
  // Some installations use another department vocabulary.
  if (!who.value && who.options.length) who.selectedIndex = 0;
  $("#dashboard-company-options").hidden = !company;
  $("#dashboard-run-options").hidden = Boolean(company);
  const codex = state.data.profiles.find(p => p.id === who.value)?.backend === "codex";
  $("#dashboard-mode").disabled = codex;
  if (codex) $("#dashboard-mode").value = "react";
  $("#dashboard-turns").disabled = Boolean(company) || codex;
  const label = id => state.data.profiles.find(p => p.id === id)?.model || id;
  $("#dashboard-routing").textContent = company
    ? `${label(company.profile)} works → ${label(company.review_profile)} reviews. ${company.status === "active" ? "Driver will pick this up when eligible." : "Task will be saved in the queue. Start the Driver from Companies when ready."} Existing company limits and permissions apply.`
    : "Starts in the selected project. Tool actions use the standard approval policy. This standalone run has no independent review; use a company for reviewed delivery.";
  $("#dashboard-submit").textContent = company ? "Add to queue →" : "Start task →";
  $("#dashboard-submit").disabled = !projects.length || !who.value;
}
function dashboardSummary(data, runs, filter = "open") {
  const live = runs.filter(r => ["running", "waiting", "stopping"].includes(r.status));
  const linked = new Set(data.companies.flatMap(c => c.tasks.map(t => t.last_mission)).filter(Boolean));
  const attention = data.missions.filter(m => ["waiting", "blocked", "awaiting_checks", "awaiting_plan", "ready"].includes(m.status));
  const tasks = data.companies.flatMap(c => c.tasks.map(t => ({...t, company: c})))
    .filter(t => filter === "all" || (filter === "done" ? t.status === "done" : !dashboardClosed.has(t.status)))
    .sort((a,b) => Number(!a.enabled) - Number(!b.enabled) || Number(b.status === "running") - Number(a.status === "running") || a.priority - b.priority || a.created - b.created);
  const standalone = data.missions.filter(m => !linked.has(m.id) && !m.company_context && (filter === "all" || (filter === "done" ? m.status === "accepted" : !dashboardClosed.has(m.status))));
  return {live, attention, tasks, standalone,
    queued: data.companies.reduce((sum,c) => sum + c.tasks.filter(t => t.enabled && ["queued", "scheduled"].includes(t.status)).length, 0)};
}
async function dashboardOpenCompany(id, taskId = null) {
  if (companyDirty) throw new Error("Save or refresh the open company form first.");
  companySelection = id; companySnapshot = ""; await showCompanies();
  if (taskId) { const task = document.getElementById(`company-task-${taskId}`); task?.scrollIntoView({block:"center"}); task?.focus(); }
}
async function dashboardOpenMission(m) {
  if (state.dirty) throw new Error("Save the open file before changing projects.");
  if (state.project !== m.project) await selectProject(m.project);
  if (state.project !== m.project) return;
  missionSelection = m.id; missionViewProject = m.project; missionSnapshot = ""; await showMissions();
}
function dashboardCard(title, status, detail, action, label = "Open details") {
  const card = el("article", "dash-card");
  const heading = el("div", "dash-card-heading");
  heading.append(el("h3", "", title), el("span", "dash-badge", status));
  card.append(heading, el("p", "dash-card-detail", detail));
  if (action) card.append(missionButton(label, action));
  return card;
}
function dashboardList(selector, rows, empty) {
  $(selector).replaceChildren(...(rows.length ? rows : [el("p", "dash-empty", empty)]));
}
function renderDashboard() {
  if (!dashboardData) return;
  const data = dashboardData, runs = state.data.runs, summary = dashboardSummary(data, runs, $("#dashboard-filter").value);
  const snapshot = JSON.stringify([data, runs, $("#dashboard-filter").value, $("#approval-count").textContent]);
  if (snapshot === dashboardSnapshot) return;
  dashboardSnapshot = snapshot;
  const projectName = id => state.data.projects.find(p => p.id === id)?.name || id;
  const status = value => companyStatus[value] || missionLabels[value] || labels[value] || value;
  const approvals = Number($("#approval-count").textContent) || 0;
  const metrics = [[summary.live.length, "Active runs", "Across all projects"], [summary.queued, "Queued tasks", "Enabled company work"], [approvals, "Your decisions", "Approvals & answers"], [data.companies.filter(c => c.status === "active").length, "Active companies", "Driver is enabled"]];
  $("#dashboard-metrics").replaceChildren(...metrics.map(([n,title,note]) => {
    const box = el("div", "dash-metric"); box.append(el("span", "", title), el("strong", "", String(n)), el("small", "", note)); return box;
  }));
  dashboardList("#dashboard-active", summary.live.map(r => {
    const mission = data.missions.find(m => m.active_attempt === r.id);
    const attempt = mission?.attempts.find(a => a.id === r.id);
    const phase = attempt?.phase || mission?.phase;
    const title = mission?.title || r.task?.split("\n")[0] || "Agent task";
    const card = dashboardCard(title, labels[r.status] || status(r.status), `${projectName(r.project)} · ${r.model || r.profile} · ${missionPhaseLabels[phase] || "Execution"}`, async () => { await refreshState(); await selectRun(r.id); }, "View activity");
    card.classList.add("dash-card-live");
    if (mission?.message) card.append(el("p", "dash-progress", mission.message));
    if (mission) card.append(missionButton("Live map", () => showFlow(mission.id)));
    return card;
  }), "No agent is running right now. Add a task or inspect a queued company's status below.");
  const attentionCards = summary.attention.map(m => dashboardCard(m.title, status(m.status), m.message || "Open the execution for the next step.", () => dashboardOpenMission(m), "Review next step"));
  for (const c of data.companies) for (const t of c.tasks.filter(t => t.status === "needs_owner")) attentionCards.push(dashboardCard(t.title, "Owner action", t.outcome || `${c.name} · ${data.departments[t.department] || t.department}`, () => dashboardOpenCompany(c.id, t.id)));
  if (approvals) attentionCards.unshift(dashboardCard(`${approvals} pending decision${approvals === 1 ? "" : "s"}`, "Waiting for you", "Approve, decline, or send your own instruction in the approval inbox.", focusApprovalInbox, "Answer now"));
  dashboardList("#dashboard-attention", attentionCards, "No open blockers or decisions in the latest snapshot.");
  const taskCards = summary.tasks.map(t => dashboardCard(t.title, t.enabled ? status(t.status) : "Disabled", `${t.company.name} · ${projectName(t.project)} · ${data.departments[t.department] || t.department}${t.company.status !== "active" && ["queued","scheduled"].includes(t.status) ? " · Driver paused" : ""}`, () => dashboardOpenCompany(t.company.id, t.id)));
  for (const m of summary.standalone) taskCards.push(dashboardCard(m.title, status(m.status), `${projectName(m.project)} · ${m.message || ""}`, () => dashboardOpenMission(m)));
  const missionRuns = new Set(data.missions.flatMap(m => m.attempts.map(a => a.id)));
  for (const r of runs.filter(r => !missionRuns.has(r.id) && !summary.live.includes(r) && ($("#dashboard-filter").value === "all" || ($("#dashboard-filter").value === "done" ? r.status === "completed" : !["completed","cancelled"].includes(r.status)))).slice(0,20)) {
    taskCards.push(dashboardCard(r.task?.split("\n")[0] || "Standalone task", labels[r.status] || r.status, `${projectName(r.project)} · ${r.model || r.profile}`, async () => { await refreshState(); await selectRun(r.id); }));
  }
  dashboardList("#dashboard-tasks", taskCards, "No tasks in this view. Give your team its next assignment.");
  dashboardList("#dashboard-companies", data.companies.map(c => dashboardCard(c.name, companyStatus[c.status] || c.status, c.message || `${c.tasks.length} tasks`, () => dashboardOpenCompany(c.id), "Manage company")), "No company yet. Use Company to create one, or start a standalone task.");
}
async function loadDashboard() {
  if (dashboardLoading || dashboardSubmitting || !state.data || !document.body.classList.contains("dashboard-home")) return;
  dashboardLoading = true;
  try {
    const [companies, missions, latestState] = await Promise.all([api("/api/companies"), api("/api/missions"), api("/api/state")]);
    state.data = latestState;
    dashboardData = {...companies, missions: missions.missions};
    dashboardLastUpdated = new Date();
    const error = [companies.controller_error, missions.controller_error].filter(Boolean).join(" · ");
    $("#dashboard-error").textContent = error; $("#dashboard-error").hidden = !error;
    dashboardRouting(); renderDashboard();
    $("#dashboard-sync").textContent = `Live · updated ${dashboardLastUpdated.toLocaleTimeString("en-GB")}`;
  } catch (error) {
    $("#dashboard-sync").textContent = dashboardLastUpdated ? `Offline · last update ${dashboardLastUpdated.toLocaleTimeString("en-GB")}` : "Unable to load dashboard";
    $("#dashboard-error").textContent = `Showing the last available snapshot. ${error.message}`; $("#dashboard-error").hidden = false;
  } finally { dashboardLoading = false; }
}
function dashboardTaskPayload(values, company) {
  const goal = values.brief.trim(), criteria = values.criteria.split("\n").map(s => s.trim()).filter(Boolean);
  if (!goal || !criteria.length) throw new Error("Describe the task and how you will know it is done.");
  if (company) {
    if (!company.projects.includes(values.project)) throw new Error("This project is no longer assigned to that company.");
    return {url:"/api/companies/action", body:{id:company.id, revision:company.revision, action:"add_task", project:values.project, department:values.who,
      title:goal.split("\n")[0].slice(0,160), goal, criteria, kind:"work", priority:Number(values.priority),
      constraints:values.constraints, verification_checks:values.checks, depends_on:[], interval_hours:0, max_cycles:1}};
  }
  return {url:"/api/runs", body:{project:values.project, profile:values.who, mode:values.mode, max_turns:Number(values.turns), auto_approve:false,
    task:`${goal}\n\nDone when:\n${criteria.map(c => `- ${c}`).join("\n")}${values.constraints.trim() ? `\n\nConstraints:\n${values.constraints.trim()}` : ""}`}};
}
async function submitDashboardTask(event) {
  event.preventDefault();
  if (dashboardSubmitting) return;
  const feedback = $("#dashboard-task-feedback"), form = $("#dashboard-task-form");
  feedback.hidden = true;
  if (state.dirty) { feedback.textContent = "Save the open file before starting new work."; feedback.hidden = false; return; }
  const values = Object.fromEntries(["project","destination","who","brief","criteria","priority","checks","mode","turns","constraints"].map(k => [k,$(`#dashboard-${k}`).value]));
  dashboardSubmitting = true;
  const controls = $$('input, select, textarea, button', form), disabled = controls.map(c => c.disabled);
  controls.forEach(c => c.disabled = true);
  let saved = false;
  try {
    let company = null;
    if (values.destination !== "run") {
      const latest = await api("/api/companies");
      company = latest.companies.find(c => c.id === values.destination);
      if (!company) throw new Error("Company no longer exists. Refresh and choose a destination.");
    }
    const request = dashboardTaskPayload(values, company);
    const result = await api(request.url, request.body); saved = true;
    for (const k of ["brief","criteria","checks","constraints"]) $(`#dashboard-${k}`).value = "";
    feedback.textContent = company ? `Task saved to ${company.name}. ${result.status === "active" ? "Driver will schedule it when eligible." : "Driver is paused; open Manage company to start it."}` : "Task started. Follow it in Working now.";
    feedback.className = "dash-success"; feedback.hidden = false;
    try { await refreshState(); } catch (_) { feedback.textContent += " Status refresh failed; the task was saved. Do not submit it again."; }
  } catch (error) {
    feedback.className = "dash-error"; feedback.textContent = `${error.message}${saved ? " The task was already saved; do not submit it again." : " Your brief is kept below."}`; feedback.hidden = false;
  } finally {
    controls.forEach((c,i) => c.disabled = disabled[i]); dashboardSubmitting = false;
    dashboardRouting(); await loadDashboard();
  }
}
function initDashboard() {
  bind("#dashboard-button", "click", () => showDashboard());
  bind("#workspace-button", "click", () => showDashboard(false));
  bind("#dashboard-new-task", "click", () => { $("#dashboard-brief").scrollIntoView({block:"center"}); $("#dashboard-brief").focus(); });
  bind("#dashboard-refresh", "click", async () => { await refreshState(); await loadDashboard(); });
  bind("#dashboard-approvals", "click", focusApprovalInbox);
  bind("#dashboard-project", "change", () => dashboardRouting(true));
  bind("#dashboard-destination", "change", () => dashboardRouting());
  bind("#dashboard-who", "change", () => dashboardRouting());
  bind("#dashboard-filter", "change", renderDashboard);
  bind("#dashboard-task-form", "submit", submitDashboardTask);
  $(".nav-more").addEventListener("click", event => { if (event.target.closest("button:not(.context-help-trigger)")) $(".nav-more").open = false; });
  showDashboard();
  setInterval(loadDashboard, 5000);
}
