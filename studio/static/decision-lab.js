"use strict";
let decisionLabProject = null;
let decisionLabGeneration = 0;
let decisionLabLoading = false;
let decisionLabSnapshot = "";
function renderDecisionLab(records) {
  const panel = $("#decision-lab-results"); panel.replaceChildren();
  if (!records.length) panel.append(el("p", "missions-note", "No saved evaluations for this project."));
  for (const record of records) {
    const card = el("details", "mission-task"); card.open = record.status === "running" || record === records[0];
    card.append(el("summary", "", `${record.kind} · ${record.status} · ${new Date(record.started * 1000).toLocaleString("en-GB")}`));
    card.append(el("p", "missions-note", `${record.model} · ${record.provider} · rubric ${record.rubric} · ${record.input_bytes} bytes`));
    if (record.status === "running") card.append(el("p", "", "Evaluating supplied evidence. No business action is being taken."));
    if (record.error) card.append(el("p", "mission-feedback", record.error));
    if (record.result) card.append(el("strong", "", record.result.recommendation.replaceAll("_", " ")), el("p", "", record.result.reason));
    if (record.result?.score != null) card.append(el("p", "", `Evidence score: ${record.result.score}/100`));
    for (const [key, answer] of Object.entries(record.answers || {})) {
      card.append(el("p", "", `${key.replaceAll("_", " ")}: ${answer.choice ?? answer.score ?? answer.noul}${answer.confidence == null ? "" : ` · confidence ${answer.confidence.toFixed(3)}`}`));
    }
    if (record.provider === "chat") card.append(el("p", "missions-note", "Confidence is the chat model's own estimate; it is not calibrated."));
    if (record.elapsed_seconds != null) card.append(el("p", "missions-note", `${record.elapsed_seconds.toFixed(1)} s · input ${record.sha256.slice(0,16)}`));
    const evidence = el("details"); evidence.append(el("summary", "", "Exact supplied evidence and rubric"), el("pre", "", record.text), el("pre", "", JSON.stringify(record.questions, null, 2))); card.append(evidence);
    if (record.status !== "running") {
      const exportButton = missionButton("Save Markdown report to project", async () => {
        try {
          const result = await api("/api/decision-lab/export", {id:record.id});
          card.append(el("p", "", `Saved: ${result.path}`)); exportButton.hidden = true;
        } catch (error) { card.append(el("p", "mission-feedback", error.message)); }
      }); card.append(exportButton);
    }
    panel.append(card);
  }
  if (records[0]?.status !== "running" && $("#decision-lab-feedback").textContent.startsWith("Evaluation started")) $("#decision-lab-feedback").textContent = "Evaluation finished. Inspect the saved result below.";
}
async function loadDecisionLab() {
  const dialog = $("#decision-lab-dialog");
  if (!dialog?.open || decisionLabLoading || !decisionLabProject) return;
  const generation = decisionLabGeneration, project = decisionLabProject;
  decisionLabLoading = true;
  try {
    const data = await api(`/api/decision-lab?project=${encodeURIComponent(project)}`);
    if (!dialog.open || generation !== decisionLabGeneration || project !== decisionLabProject) return;
    const snapshot = JSON.stringify(data);
    if (snapshot !== decisionLabSnapshot) { renderDecisionLab(data.evaluations); decisionLabSnapshot = snapshot; }
  } finally { decisionLabLoading = false; }
}
function initDecisionLab() {
  bind("#decision-lab-button", "click", async () => {
    decisionLabGeneration++; decisionLabProject = state.project; decisionLabSnapshot = "";
    $("#decision-lab-project").textContent = `Workspace: ${state.data.projects.find(p => p.id === decisionLabProject)?.name || decisionLabProject}`;
    const select = $("#decision-lab-profile"), previous = select.value;
    populateDecisionModels(select); select.firstElementChild?.remove();
    select.value = [...select.options].some(o => o.value === previous) ? previous : state.data.profiles.find(p => p.protocol === "chat_completions" && !p.oauth_provider)?.id || "typesafe:jev-latest";
    $("#decision-lab-dialog").showModal(); await loadDecisionLab();
  });
  bind("#decision-lab-dialog", "close", () => { decisionLabGeneration++; });
  bind("#decision-lab-form", "submit", async event => {
    event.preventDefault(); const button = $("#decision-lab-submit"); if (button.disabled) return;
    button.disabled = true; const project = decisionLabProject;
    try {
      await api("/api/decision-lab", {project, kind:$("#decision-lab-kind").value, profile:$("#decision-lab-profile").value,
        claim:$("#decision-lab-claim").value, text:$("#decision-lab-text").value});
      $("#decision-lab-feedback").textContent = "Evaluation started. You can close this panel and return to the saved result.";
      await loadDecisionLab();
    } catch (error) { $("#decision-lab-feedback").textContent = error.message; }
    finally { button.disabled = false; }
  });
  setInterval(() => loadDecisionLab().catch(error => { $("#decision-lab-feedback").textContent = error.message; }), 3000);
}
