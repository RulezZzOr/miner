"use strict";
let previewCurrent = null;
let previewEpoch = 0;
let previewPolling = false;
let previewBusy = false;
let previewRevision = null;
let previewProject = null;

function previewURL(session) {
  const url = new URL(session.url);
  // Keep the cookie first-party when Studio was opened through localhost.
  url.hostname = location.hostname;
  return url.href;
}
function renderPreview(session) {
  const sameProject = session.running && session.project === state.project;
  const previous = previewCurrent;
  previewCurrent = session.running ? session : null;
  $("#preview-start").disabled = session.running;
  $("#preview-stop").disabled = !session.running;
  $("#preview-refresh").disabled = !sameProject;
  $("#preview-edit").disabled = !sameProject;
  $("#preview-external").classList.toggle("hidden", !sameProject);
  $("#preview-empty").classList.toggle("hidden", sameProject);
  $("#preview-frame").classList.toggle("hidden", !sameProject);
  $("#preview-starter-form").classList.toggle("hidden", session.running);
  if (sameProject) {
    $("#preview-entry").value = session.entry;
    $("#preview-external").href = previewURL(session);
    $("#preview-status").textContent = `Running · ${session.entry}`;
    if (previous?.id !== session.id || !$("#preview-frame").hasAttribute("src")) {
      previewRevision = session.revision;
      $("#preview-frame").src = previewURL(session);
    }
  } else {
    $("#preview-frame").removeAttribute("src");
    $("#preview-external").removeAttribute("href");
    previewRevision = null;
    $("#preview-status").textContent = session.running
      ? "Preview is running for another project. Stop it first, or switch projects."
      : "Preview is stopped. Select an HTML file or create a starter website.";
  }
}
async function showPreview() {
  const epoch = ++previewEpoch;
  const projectId = state.project;
  if (previewProject !== projectId) {
    previewProject = projectId;
    $("#preview-entry").value = state.file?.path?.match(/\.html?$/i) ? state.file.path : "index.html";
  } else if (!previewCurrent && state.file?.path?.match(/\.html?$/i)) {
    $("#preview-entry").value = state.file.path;
  }
  $("#preview-project").textContent = project().name;
  $("#preview-dialog").showModal();
  const session = await api("/api/preview");
  if (epoch === previewEpoch && projectId === state.project && $("#preview-dialog").open) renderPreview(session);
}
async function previewMutation(action) {
  if (previewBusy) return;
  previewBusy = true;
  const epoch = ++previewEpoch;
  const projectId = state.project;
  $("#preview-form").inert = true;
  $("#preview-starter-form").inert = true;
  try {
    const session = await action(projectId);
    if (epoch === previewEpoch && projectId === state.project) renderPreview(session);
  } finally {
    previewBusy = false;
    $("#preview-form").inert = false;
    $("#preview-starter-form").inert = false;
  }
}
function refreshPreviewFrame() {
  if (previewCurrent?.project !== state.project) return;
  $("#preview-frame").src = previewURL(previewCurrent);
}
async function pollPreview() {
  if (previewPolling || previewBusy || !$("#preview-dialog").open) return;
  previewPolling = true;
  const epoch = previewEpoch;
  const projectId = state.project;
  try {
    const session = await api("/api/preview");
    if (epoch !== previewEpoch || projectId !== state.project || !$("#preview-dialog").open) return;
    const changed = previewCurrent?.id === session.id && previewRevision !== session.revision;
    renderPreview(session);
    if (session.running && session.project === projectId && $("#preview-auto").checked && changed) {
      previewRevision = session.revision;
      refreshPreviewFrame();
    }
  } catch (error) {
    if (epoch === previewEpoch) $("#preview-status").textContent = "Preview connection interrupted: " + error.message;
  } finally { previewPolling = false; }
}
function initPreview() {
  bind("#preview-button", "click", showPreview);
  bind("#preview-form", "submit", async event => {
    event.preventDefault();
    const entry = $("#preview-entry").value.trim();
    if (state.dirty) throw new Error("First, save the unsaved file. Preview reads files from disk.");
    await previewMutation(projectId => api("/api/preview/start", {project: projectId, entry}));
  });
  bind("#preview-stop", "click", () => previewMutation(() => api("/api/preview/stop", {id: previewCurrent?.id})));
  bind("#preview-refresh", "click", () => {
    previewRevision = previewCurrent?.revision;
    refreshPreviewFrame();
  });
  bind("#preview-size", "change", () => $("#preview-stage").classList.toggle("mobile", $("#preview-size").value === "mobile"));
  bind("#preview-edit", "click", async () => {
    if (previewCurrent?.project !== state.project) return;
    $("#preview-dialog").close();
    await openFile(previewCurrent.entry);
  });
  // Prevent navigation synchronously; bind executes callbacks in a microtask.
  $("#preview-form").addEventListener("submit", event => event.preventDefault());
  $("#preview-starter-form").addEventListener("submit", event => {
    event.preventDefault();
    const folder = $("#preview-folder").value.trim();
    previewMutation(async projectId => {
      const starter = await api("/api/preview/starter", {project: projectId, folder});
      if (projectId !== state.project) return {running: false};
      $("#preview-entry").value = starter.entry;
      await loadTree();
      toast(`Website saved to ${starter.entry}.`);
      return api("/api/preview/start", {project: projectId, entry: starter.entry});
    }).catch(error => toast(error.message, true));
  });
  $("#preview-dialog").addEventListener("close", () => { previewEpoch++; });
  setInterval(pollPreview, 2000);
}
