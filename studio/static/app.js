"use strict";
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const paths = {
  preview: '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 22h8m-4-4v4m-2-14 5 4-5 4Z"/>',
  folder: '<path d="M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11H3Z"/>',
  files: '<path d="M8 3h7l5 5v13H8Z"/><path d="M15 3v6h5M4 7v13"/>',
  history: '<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v6l4 2"/>',
  team: '<circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 4a3 3 0 0 1 0 6m2 4a5 5 0 0 1 3 4v3"/>',
  sliders:
    '<path d="M4 7h5m4 0h7M4 17h11m4 0h1"/><circle cx="11" cy="7" r="2"/><circle cx="17" cy="17" r="2"/>',
  spark:
    '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
  shield:
    '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  terminal:
    '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3m6 1h4"/>',
  pulse: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9 8a3 3 0 0 1 6 1c0 2-3 2-3 5m0 3v.1"/>',
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.files}</svg>`;
function icons(root = document) {
  $$("[data-icon]", root).forEach((el) => {
    el.innerHTML = icon(el.dataset.icon);
  });
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function defaultModelForRole(role, profiles = state.data?.profiles || []) {
  if (role === "review") {
    return profiles.find((profile) => profile.reviewer_default)?.id || state.data?.default_profile || "";
  }
  return state.data?.default_profile || profiles[0]?.id || "";
}
const state = {
  data: null,
  project: localStorage.getItem("switch.project"),
  view: "files",
  bottom: "activity",
  mode: "react",
  file: null,
  dirty: false,
  fileEpoch: 0,
  run: null,
  offset: 0,
  events: [],
  polling: false,
  stream: null,
  thinking: null,
  approvalKey: "",
  treeEpoch: 0,
};
const labels = {
  running: "Working",
  waiting: "Awaiting approval",
  stopping: "Stopping",
  completed: "Completed",
  failed: "Error",
  incomplete: "Incomplete",
  cancelled: "Stopped",
  interrupted: "Interrupted",
};
const active = (r) =>
  r && ["running", "waiting", "stopping"].includes(r.status);
const emptyConversation = $("#conversation").cloneNode(true);
function resetConversation() {
  const container = $("#conversation");
  container.replaceChildren(
    ...[...emptyConversation.childNodes].map((node) => node.cloneNode(true)),
  );
  icons(container);
}
let toastTimer;
function toast(message, error = false) {
  // Background polls must not repeat what the open sign-in dialog already says.
  if (message === sessionExpiredMessage && $("#session-dialog")?.open) return;
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), error ? 8000 : 3500);
}
// Session expiry: keep the page and its drafts, pause requests, and resume after sign-in.
let sessionExpired = false;
const sessionExpiredMessage = "Your Studio session has expired. Sign in again; your drafts stay on this page.";
// Errors carry the HTTP status so callers can tell a missing item from a transient failure.
const apiError = (message, status) => Object.assign(new Error(message), { status });
async function api(path, body) {
  if (sessionExpired) throw apiError(sessionExpiredMessage, 401);
  const response = await fetch(
    path,
    body === undefined
      ? { cache: "no-store" }
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Studio-Token": state.data?.token || "",
          },
          body: JSON.stringify(body),
        },
  );
  if (response.status === 401) { showSessionExpired(); throw apiError(sessionExpiredMessage, 401); }
  const result = await response.json();
  if (!response.ok) throw apiError(result.error || "Request failed.", response.status);
  return result;
}
function signInMessage(status, error) {
  if (status === 401) return "Access key was not accepted.";
  if (status === 429) return error && error !== "Authentication failed." ? error : "Too many sign-in attempts or active sessions. Wait one minute and try again.";
  return `Studio rejected the sign-in (${status}): ${error || "no details"}`;
}
function showSessionExpired() {
  if (sessionExpired) return;
  sessionExpired = true;
  const dialog = $("#session-dialog");
  if (!dialog) return;
  const reopen = el("button", "button small", "Signed out · Sign in");
  reopen.type = "button";
  reopen.onclick = () => { if (!dialog.open) dialog.showModal(); $("#session-key").focus(); };
  $("#connection").replaceChildren(reopen);
  $("#session-error").textContent = "";
  if (!dialog.open) dialog.showModal();
  $("#session-key").focus();
}
async function resumeSession() {
  sessionExpired = false;
  if ($("#session-dialog")?.open) $("#session-dialog").close();
  $("#connection").replaceChildren(el("i"), document.createTextNode(" Connected to Studio"));
  // A restarted server issues a new request token; load it before any mutation.
  await refreshState();
  toast("Signed in again. Updates resumed; your drafts were kept.");
}
async function probeSession() {
  // Detects a sign-in completed in another tab without sending the paused requests.
  const response = await fetch("/api/state", { cache: "no-store" });
  if (response.ok) await resumeSession();
}
async function signInAgain(event) {
  event.preventDefault();
  const field = $("#session-key"), error = $("#session-error");
  error.textContent = "";
  let response;
  try {
    response = await fetch("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: field.value }) });
  } catch (_) { error.textContent = "Cannot reach Studio. Check the connection."; return; }
  field.value = "";
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).error || ""; } catch (_) { detail = ""; }
    error.textContent = signInMessage(response.status, detail);
    return;
  }
  await resumeSession();
}
function bind(selector, event, fn) {
  $(selector).addEventListener(event, (e) => {
    // preventDefault and duplicate-submit guards must run during dispatch.
    try { Promise.resolve(fn(e)).catch((err) => toast(err.message, true)); }
    catch (err) { toast(err.message, true); }
  });
}
function project() {
  return state.data.projects.find((p) => p.id === state.project);
}
function run() {
  return state.data?.runs.find((r) => r.id === state.run);
}
function canLeave() {
  return (
    !state.dirty || confirm("You have unsaved changes. Are you sure you want to discard them?")
  );
}
function markDirty() {
  state.fileEpoch++;
  state.dirty = state.file && $("#editor").value !== state.file.content;
  $("#dirty-label").textContent = state.dirty ? "Unsaved" : "";
  $("#save-file").disabled = !state.dirty;
}
function updateModelLabel() {
  const p = state.data.profiles.find((p) => p.id === $("#model-select").value);
  $("#footer-model").textContent = p
    ? `${p.model} · ${p.oauth_provider === "chatgpt" ? "ChatGPT / Codex" : p.oauth_provider === "claude_console" ? "Claude Console" : p.chat_dialect === "ollama" ? "Ollama" : p.protocol}`
    : "Select a model";
  const codex = p?.backend === "codex";
  $("#mode-team").disabled = codex;
  $("#turn-limit").disabled = codex;
  $("#turn-limit").parentElement.title = codex
    ? "Run duration is managed by Codex. The task can be stopped at any time."
    : "Maximum number of main agent steps";
  $("#turn-limit").parentElement.classList.toggle("hidden", codex);
  setMode(codex ? "react" : state.mode);
}
function profileLabel(profile) {
  return profile.oauth_provider === "chatgpt"
    ? "ChatGPT"
    : profile.oauth_provider === "claude_console"
      ? "Claude Console"
      : profile.id;
}
// The secure worker runtime refuses OAuth and Codex profiles; never offer them for work or review.
function isRunnableProfile(profile) {
  return Boolean(profile) && !profile.oauth_provider && profile.backend !== "codex";
}
function runnableProfiles(profiles = state.data?.profiles || []) {
  return profiles.filter(isRunnableProfile);
}
const executionUnavailableDefault = "Workers need Linux with bubblewrap; this host can edit and review but not run agents.";
// Returns why agents cannot run on this host, or "" when execution is available or not reported.
function executionUnavailable(data = state.data) {
  const execution = data?.execution;
  if (!execution || execution.available !== false) return "";
  return execution.reason || executionUnavailableDefault;
}
function renderExecution() {
  const banner = $("#execution-banner"), reason = executionUnavailable();
  if (!banner) return;
  banner.hidden = !reason;
  banner.textContent = reason ? `Agent execution is unavailable on this host. ${reason}` : "";
}
function fillSelectors() {
  const current =
    $("#model-select").value ||
    localStorage.getItem("switch.model") ||
    state.data.default_profile;
  $("#project-select").replaceChildren(
    ...state.data.projects.map((p) => {
      const o = el("option", "", p.name);
      o.value = p.id;
      return o;
    }),
  );
  $("#project-select").value = state.project;
  const profiles = runnableProfiles();
  $("#model-select").replaceChildren(
    ...profiles.map((p) => {
      const o = el("option", "", `${p.model} · ${profileLabel(p)}`);
      o.value = p.id;
      return o;
    }),
  );
  $("#model-select").value = profiles.some((p) => p.id === current)
    ? current
    : profiles.some((p) => p.id === state.data.default_profile)
      ? state.data.default_profile
      : profiles[0]?.id || "";
  $("#project-label").textContent = "⌄  " + (project()?.name || "Project");
  $("#footer-project").textContent = project()?.name || "";
  updateModelLabel();
}
async function refreshState() {
  state.data = await api("/api/state");
  if (!state.data.projects.some((p) => p.id === state.project))
    state.project = state.data.projects[0]?.id;
  renderExecution();
}
// A missing (404), refused (403) or unreadable (500) folder; sign-out, network and busy errors are transient.
const projectFolderProblem = (error) => !sessionExpired && [403, 404, 500].includes(error?.status);
// Loads the explorer; a missing or unreadable folder falls back to the next project that opens.
async function loadProjectTree() {
  const tried = new Set(), first = state.project;
  const restore = () => { if (state.project !== first) { state.project = first; fillSelectors(); } };
  while (state.project && !tried.has(state.project)) {
    tried.add(state.project);
    try {
      await loadTree();
      if (state.project !== first) localStorage.setItem("switch.project", state.project);
      return true;
    } catch (error) {
      // Keep the owner's choice and let polling retry the whole load.
      if (!projectFolderProblem(error)) { restore(); throw error; }
      toast(`Project folder is missing or unreadable (${project()?.name || state.project}): ${error.message}`, true);
      const next = state.data.projects.find((p) => !tried.has(p.id));
      if (!next) break;
      state.project = next.id;
      fillSelectors();
    }
  }
  // Nothing opened: keep the saved choice so a remounted folder opens again on the next load.
  restore();
  $("#sidebar-content").replaceChildren(el("p", "sidebar-note", state.data.projects.length
    ? "No project folder could be opened. Use Open project to choose an existing folder."
    : "Open a project folder to start."));
  return false;
}
async function loadTree() {
  const epoch = ++state.treeEpoch;
  const list = $("#sidebar-content");
  const result = await api(`/api/tree?project=${state.project}`);
  if (epoch !== state.treeEpoch || state.view !== "files") return;
  list.replaceChildren();
  await appendTree(list, result, epoch);
}
async function appendTree(parent, items, epoch) {
  for (const item of items) {
    const row = el(
      "button",
      "tree-row" +
        (item.directory ? " directory" : "") +
        (state.file?.path === item.path ? " active" : ""),
    );
    row.title = item.path;
    row.append(el("span", "tree-chevron", item.directory ? "›" : ""));
    const glyph = el("span", "file-glyph");
    if (item.directory) glyph.innerHTML = icon("folder");
    else
      glyph.textContent = item.name.endsWith(".py")
        ? "py"
        : item.name.endsWith(".js")
          ? "JS"
          : item.name.endsWith(".md")
            ? "M↓"
            : "◇";
    row.append(glyph, el("span", "", item.name));
    parent.append(row);
    if (item.directory) {
      const children = el("div", "tree-children hidden");
      parent.append(children);
      let loaded = false;
      row.addEventListener("click", async () => {
        try {
          if (!loaded) {
            const data = await api(
              `/api/tree?project=${state.project}&path=${encodeURIComponent(item.path)}`,
            );
            if (epoch !== state.treeEpoch) return;
            await appendTree(children, data, epoch);
            loaded = true;
          }
          children.classList.toggle("hidden");
          row.firstChild.textContent = children.classList.contains("hidden")
            ? "›"
            : "⌄";
        } catch (e) {
          toast(e.message, true);
        }
      });
    } else
      row.addEventListener("click", () =>
        openFile(item.path).catch((e) => toast(e.message, true)),
      );
  }
}
async function openFile(path, force = false) {
  if (!force && !canLeave()) return;
  const epoch = ++state.fileEpoch;
  const selectedProject = state.project;
  const file = await api(
    `/api/file?project=${selectedProject}&path=${encodeURIComponent(path)}`,
  );
  if (state.project !== selectedProject || state.fileEpoch !== epoch) return;
  state.file = file;
  state.dirty = false;
  if (typeof showDashboard === "function") showDashboard(false);
  $("#welcome").classList.add("hidden");
  $("#editor-pane").classList.remove("hidden");
  $("#save-file").classList.remove("hidden");
  $("#reload-file").classList.remove("hidden");
  $("#tab-name").textContent = "◇  " + path.split("/").at(-1);
  $("#breadcrumb").textContent =
    project().name + "  /  " + path.replaceAll("/", "  /  ");
  $("#editor").value = file.content;
  $("#editor").scrollTop = 0;
  $("#file-type").textContent = path.split(".").at(-1).toUpperCase();
  $("#context-file").textContent = "◇ " + path.split("/").at(-1);
  markDirty();
  updateLines();
  updateCursor();
  $$(".tree-row").forEach((row) =>
    row.classList.toggle("active", row.title === path),
  );
}
// One save at a time: a second save during a request waits and then uses the new revision.
async function saveFile() {
  if (state.saving) {
    state.saveAgain = true;
    return state.saving;
  }
  if (!state.file || !state.dirty) return;
  state.saving = (async () => {
    try {
      do {
        state.saveAgain = false;
        await saveFileOnce();
      } while (state.saveAgain && state.file && state.dirty);
    } finally {
      state.saving = null;
      state.saveAgain = false;
    }
  })();
  return state.saving;
}
async function saveFileOnce() {
  const content = $("#editor").value;
  const file = state.file;
  const result = await api("/api/file", {
    project: state.project,
    path: file.path,
    content,
    revision: file.revision,
  });
  if (state.file === file) {
    state.file = { ...file, content, revision: result.revision };
    markDirty();
  }
  toast("File saved.");
}
function updateLines() {
  $("#line-numbers").textContent = Array.from(
    { length: $("#editor").value.split("\n").length },
    (_, i) => i + 1,
  ).join("\n");
}
function updateCursor() {
  const text = $("#editor")
    .value.slice(0, $("#editor").selectionStart)
    .split("\n");
  $("#cursor-position").textContent =
    `Line ${text.length}, column ${text.at(-1).length + 1}`;
}
function clearFile() {
  state.fileEpoch++;
  state.file = null;
  state.dirty = false;
  $("#welcome").classList.remove("hidden");
  $("#editor-pane").classList.add("hidden");
  $("#save-file").classList.add("hidden");
  $("#reload-file").classList.add("hidden");
  $("#tab-name").textContent = "ϟ Welcome to Studio";
  $("#dirty-label").textContent = "";
  $("#context-file").textContent = "Project context";
}
async function selectProject(id) {
  if (id === state.project) {
    // Same project: keep the open file, run and conversation.
    $("#project-select").value = state.project;
    return;
  }
  if (!canLeave()) {
    $("#project-select").value = state.project;
    return;
  }
  state.project = id;
  clearFile();
  localStorage.setItem("switch.project", id);
  state.run = null;
  state.events = [];
  state.offset = 0;
  state.stream = null;
  state.thinking = null;
  state.approvalKey = "";
  resetConversation();
  updateRunControls();
  await renderBottom();
  fillSelectors();
  await renderSidebar();
}
async function renderSidebar() {
  $$(".rail-btn[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === state.view),
  );
  $("#sidebar-title").textContent = {
    files: "EXPLORER",
    runs: "TASK HISTORY",
    team: "AGENT TEAM",
  }[state.view];
  $("#file-actions").classList.toggle("hidden", state.view !== "files");
  const list = $("#sidebar-content");
  if (state.view === "files") {
    delete list.dataset.signature;
    return loadTree();
  }
  state.treeEpoch++;
  if (state.view === "runs") {
    const runs = state.data.runs.filter((r) => r.project === state.project);
    // Polling must not rebuild an unchanged list: that drops focus and swallows clicks.
    const signature = JSON.stringify([state.project, state.run, runs.map((r) => [r.id, r.task, r.status, r.created])]);
    if (list.dataset.signature === signature) return;
    const focused = list.contains(document.activeElement) ? document.activeElement.dataset?.run : null;
    list.dataset.signature = signature;
    list.replaceChildren();
    if (!runs.length)
      list.append(
        el("p", "sidebar-note", "No tasks yet. Enter your first task on the right."),
      );
    for (const r of runs) {
      const b = el(
        "button",
        "run-item" + (r.id === state.run ? " active" : ""),
      );
      b.dataset.run = r.id;
      b.append(
        el("strong", "", r.task),
        el(
          "small",
          "",
          `${labels[r.status] || r.status} · ${new Date(r.created * 1000).toLocaleString("en-GB", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}`,
        ),
      );
      b.onclick = () => selectRun(r.id).catch((e) => toast(e.message, true));
      list.append(b);
      if (r.id === focused) b.focus({ preventScroll: true });
    }
  } else {
    delete list.dataset.signature;
    list.replaceChildren();
    const r = run();
    if (!r) {
      list.append(
        el(
          "p",
          "sidebar-note",
          "Launch a team task. Here, the coordinator and workers you actually create will appear.",
        ),
      );
      return;
    }
    const card = el("div", "agent-card");
    card.append(
      el(
        "strong",
        "",
        r.mode === "agent_team" ? "Coordinator" : "Main agent",
      ),
      el("small", "", r.model),
      el("small", "", labels[r.status] || r.status),
    );
    list.append(card);
    const names = new Set();
    for (const e of state.events.filter(
      (e) => e.type === "tool_call" && e.name === "create_subagent",
    ))
      for (const a of e.args?.agents || []) {
        if (names.has(a.name)) continue;
        names.add(a.name);
        const c = el("div", "agent-card worker");
        c.append(
          el("strong", "", a.name),
          el("small", "", "Worker creation request"),
          el("pre", "", a.system_prompt || ""),
        );
        list.append(c);
      }
    list.append(
      el(
        "p",
        "sidebar-note",
        "The tree is based on tool calls. Verify the delegation result in the activity. Deeper delegation levels are not supported yet.",
      ),
    );
  }
}
function setMode(mode) {
  const codex =
    state.data?.profiles.find((p) => p.id === $("#model-select").value)
      ?.backend === "codex";
  if (codex) mode = "react";
  state.mode = mode;
  $$("[data-mode]").forEach((b) =>
    b.classList.toggle("selected", b.dataset.mode === mode),
  );
  $("#mode-description").textContent = codex
    ? "ChatGPT via Codex is not available in the secure worker runtime. Select an API or local model."
    : mode === "agent_team"
      ? "The coordinator distributes work among its workers."
      : "One agent handles the task and uses tools.";
}
function message(kind, text, label) {
  const m = el("div", "message " + kind);
  if (label) m.append(el("span", "message-label", label));
  const body = el("div", "message-body", text);
  m.append(body);
  $("#conversation").append(m);
  return body;
}
function renderEvent(e) {
  if (e.type === "thinking") {
    if (!state.thinking) {
      const d = el("details", "thinking-block");
      d.append(el("summary", "", "Model reasoning"));
      state.thinking = el("pre", "", "");
      d.append(state.thinking);
      $("#conversation").append(d);
    }
    state.thinking.append(document.createTextNode(e.text));
  }
  if (e.type === "content") {
    if (!state.stream) state.stream = message("assistant", "", "Agent");
    state.stream.append(document.createTextNode(e.text));
  }
  if (e.type === "turn_end") {
    state.stream = null;
    state.thinking = null;
  }
  if (e.type === "tool_call") {
    state.stream = null;
    state.thinking = null;
    const badge = el("div", "tool-badge");
    badge.append(el("span", "", `↳ ${e.name}`));
    const d = el("details");
    d.append(
      el("summary", "", "Show parameters"),
      el("pre", "", JSON.stringify(e.args, null, 2)),
    );
    badge.append(d);
    $("#conversation").append(badge);
  }
  if (["final", "incomplete", "error"].includes(e.type)) {
    message(
      e.type === "error" ? "error" : "final",
      e.text,
      e.type === "final"
        ? "Result"
        : e.type === "incomplete"
          ? "Partial result"
          : "Error",
    );
    state.stream = null;
    state.thinking = null;
  }
  if (e.type === "plan") message("assistant", e.text, "Plan proposal");
}
function updateRunControls() {
  const r = run();
  const anyActive = state.data?.runs.some(active);
  const unavailable = executionUnavailable();
  $("#run-task").disabled = !!anyActive || !!unavailable;
  // The title only explains a disabled Run; its aria-label keeps the name and context help stable.
  $("#run-task").title = unavailable || (anyActive ? "A worker is busy. Wait for the current run or stop it." : "");
  $("#stop-task").classList.toggle("hidden", !active(r));
  $("#stop-task").disabled = r?.status === "stopping";
  $("#run-status").textContent = r
    ? labels[r.status] || r.status
    : "Ready";
  if (r) {
    const seconds = Math.max(
      0,
      Math.floor((r.ended || Date.now() / 1000) - r.created),
    );
    $("#run-status").textContent +=
      ` · ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
  $("#run-status").className = "status-chip " + (r?.status || "");
  $("#activity-count").textContent = state.events.filter(
    (e) => e.type === "tool_call",
  ).length;
}
async function selectRun(id) {
  const r = state.data.runs.find((r) => r.id === id);
  if (!r) return;
  if (typeof showDashboard === "function") showDashboard(false);
  if (state.project !== r.project) {
    await selectProject(r.project);
    if (state.project !== r.project) return;
  }
  state.run = id;
  state.offset = 0;
  state.events = [];
  state.stream = null;
  state.thinking = null;
  state.approvalKey = "";
  localStorage.setItem("switch.run", id);
  $("#conversation").replaceChildren();
  message("user", r.task, "Task brief");
  updateRunControls();
  await pollRun();
  if (state.view !== "files") await renderSidebar();
}
async function pollRun() {
  const id = state.run;
  if (!id) return;
  const offset = state.offset;
  const data = await api(`/api/events?run=${id}&offset=${offset}`);
  if (state.run !== id || state.offset !== offset) return;
  state.offset = data.offset;
  state.events.push(...data.events);
  const index = state.data.runs.findIndex((r) => r.id === id);
  if (index >= 0) state.data.runs[index] = data.run;
  const c = $("#conversation");
  const nearBottom = c.scrollHeight - c.scrollTop - c.clientHeight < 100;
  data.events.forEach(renderEvent);
  if (nearBottom) c.scrollTop = c.scrollHeight;
  updateRunControls();
  if (data.approvals.length) $("#run-status").textContent = "Awaiting approval";
  // Finished runs keep their outputs; refetch them only when the run or its status changes.
  const outputsKey = `${id}:${data.run?.status}`;
  if (data.events.length || (state.bottom === "outputs" && state.outputsKey !== outputsKey)) {
    await renderBottom();
    if (state.bottom === "outputs" && state.run === id) state.outputsKey = outputsKey;
  }
  if (state.view === "team" && data.events.length) await renderSidebar();
}
const activityTypes = ["tool_call", "tool_result", "error", "note", "finished", "started"];
function scrolledToBottom(node) {
  return node.scrollHeight - node.scrollTop - node.clientHeight < 60;
}
async function renderBottom() {
  const panel = $("#bottom-content");
  if (state.bottom === "activity") {
    const all = state.events.filter((e) => activityTypes.includes(e.type));
    // Content chunks do not change the activity list; skip the rebuild.
    const signature = `activity:${state.run}:${all.length}`;
    if (panel.dataset.signature === signature) return;
    const follow = !panel.dataset.signature?.startsWith(`activity:${state.run}:`) || scrolledToBottom(panel);
    panel.dataset.signature = signature;
    const events = all.slice(-100);
    panel.replaceChildren();
    if (!events.length) {
      panel.append(
        el("div", "empty-inline", "Once you launch the task, you will see each step here."),
      );
      return;
    }
    for (const e of events) {
      const row = el(
        "div",
        "activity-row" + (e.is_error || e.type === "error" ? " error" : ""),
      );
      row.append(
        el("time", "", new Date(e.time * 1000).toLocaleTimeString("en-GB")),
        el(
          "strong",
          "",
          e.name ||
            {
              started: "start",
              finished: "finished",
              error: "error",
              note: "info",
            }[e.type],
        ),
        el(
          "span",
          "detail",
          e.type === "tool_call"
            ? JSON.stringify(e.args)
            : e.text || labels[e.status] || e.model || "",
        ),
      );
      row.title = e.text || JSON.stringify(e.args || {});
      panel.append(row);
    }
    if (follow) panel.scrollTop = panel.scrollHeight;
  } else if (state.bottom === "outputs") {
    const id = state.run;
    const files = id ? await api(`/api/artifacts?run=${id}`) : [];
    if (id !== state.run || state.bottom !== "outputs") return;
    const signature = "outputs:" + JSON.stringify([id, files]);
    if (panel.dataset.signature === signature) return;
    panel.dataset.signature = signature;
    panel.replaceChildren();
    if (!files.length)
      panel.append(
        el(
          "div",
          "empty-inline",
          "No files in the outputs of this task yet.",
        ),
      );
    for (const f of files) {
      const row = el("div", "artifact-item");
      const b = el("button", "artifact-row");
      b.append(
        el("span", "", "◇"),
        el("span", "", f.name),
        el("small", "", `${f.size} B`),
      );
      b.onclick = () => openFile(f.path).catch((e) => toast(e.message, true));
      const download = el("a", "download-link", "Download ↓");
      download.href = `/api/download?project=${state.project}&path=${encodeURIComponent(f.path)}`;
      download.download = f.name;
      row.append(b, download);
      panel.append(row);
    }
  } else {
    const id = state.run;
    const data = id
      ? await api(`/api/log?run=${id}`)
      : { text: "The console will be populated after the task is started." };
    if (id !== state.run || state.bottom !== "log") return;
    const signature = "log:" + id + ":" + data.text.length + ":" + data.text.slice(-200);
    if (panel.dataset.signature === signature) return;
    const follow = !panel.dataset.signature?.startsWith("log:" + id + ":") || scrolledToBottom(panel);
    panel.dataset.signature = signature;
    panel.replaceChildren(el("pre", "log-text", data.text));
    if (follow) panel.scrollTop = panel.scrollHeight;
  }
}
async function launchTask(e) {
  e.preventDefault();
  if (state.dirty)
    throw new Error(
      "First, save the open file. The agent works with files on disk.",
    );
  let task = $("#task-input").value.trim();
  if (!task) {
    $("#task-input").focus();
    return;
  }
  const unavailable = executionUnavailable();
  if (unavailable) throw new Error(unavailable);
  if (state.file) task += `\n\nFile open in editor: ${state.file.path}`;
  $("#run-task").disabled = true;
  try {
    const r = await api("/api/runs", {
      project: state.project,
      task,
      profile: $("#model-select").value,
      mode: state.mode,
      max_turns: Number($("#turn-limit").value),
      auto_approve: $("#auto-approve").checked,
    });
    state.data.runs.unshift(r);
    await selectRun(r.id);
    $("#task-input").value = "";
    toast("Task started.");
  } finally {
    updateRunControls();
  }
}
function fillModel(profile) {
  const oauth = Boolean(profile?.oauth_provider);
  $("#model-form").classList.toggle("hidden", oauth);
  $("#oauth-model-detail").classList.toggle("hidden", !oauth);
  if (oauth) {
    $("#oauth-model-name").textContent = profile.model;
    $("#oauth-model-info").textContent =
      (profile.oauth_provider === "chatgpt"
        ? "ChatGPT via Codex login. "
        : "Claude Console via OAuth. ") +
      "Not available in the secure worker runtime: workers and reviewers only run API or local models. Remove this profile or keep it for later.";
    $("#oauth-remove").dataset.profile = profile.id;
  }
  const form = $("#model-form");
  form.reset();
  const defaults = {
    id: "",
    model: "",
    protocol: "chat_completions",
    chat_dialect: "ollama",
    base_url: "http://127.0.0.1:11434/v1",
    context_window: 32768,
    max_output_tokens: 4096,
    auth: "none",
    api_key_env: "",
  };
  for (const [k, v] of Object.entries({ ...defaults, ...profile }))
    if (form.elements[k]) form.elements[k].value = v;
  $("#probe-result").textContent = "";
  $$(".model-card").forEach((b) =>
    b.classList.toggle("active", b.dataset.id === profile?.id),
  );
}
function renderModels() {
  const list = $("#models-list");
  list.replaceChildren();
  for (const p of state.data.profiles) {
    const b = el("button", "model-card");
    b.dataset.id = p.id;
    b.append(el("strong", "", profileLabel(p)), el("small", "", isRunnableProfile(p) ? p.model : `${p.model} · not available for workers`));
    b.onclick = () => fillModel(p);
    list.append(b);
  }
  fillModel(
    state.data.profiles.find((p) => p.id === $("#model-select").value) ||
      state.data.profiles[0],
  );
}
async function saveModel() {
  const data = Object.fromEntries(new FormData($("#model-form")));
  await api("/api/models", data);
  await refreshState();
  fillSelectors();
  renderModels();
  fillModel(state.data.profiles.find((p) => p.id === data.id));
  return data.id;
}

let oauthProvider = null;
let oauthTimer = null;
let oauthBusy = false;
function renderOAuth(data) {
  const login = data.login;
  const waiting = login?.status === "waiting";
  const suffix =
    login?.status === "failed"
      ? " Login failed; try again."
      : login?.status === "cancelled"
        ? " Login was cancelled."
        : "";
  $("#oauth-status").textContent = waiting
    ? "Complete login in your browser. Waiting for confirmation…"
    : data.message + suffix;
  $("#oauth-login").disabled = waiting;
  $("#oauth-login").textContent = data.connected
    ? "Log in with another account"
    : "Log in via browser";
  $("#oauth-cancel").classList.toggle("hidden", !waiting);
  $("#oauth-link").classList.toggle("hidden", !(waiting && login?.url));
  if (waiting && login?.url) $("#oauth-link").href = login.url;
  else $("#oauth-link").removeAttribute("href");
  $("#oauth-model-picker").classList.toggle(
    "hidden",
    !data.connected || waiting || !data.models.length,
  );
  const selected = $("#oauth-models").value;
  $("#oauth-models").replaceChildren(
    ...data.models.map((m) => {
      const option = el("option", "", m.name);
      option.value = m.id;
      return option;
    }),
  );
  if (data.models.some((m) => m.id === selected))
    $("#oauth-models").value = selected;
  clearTimeout(oauthTimer);
  if (waiting)
    oauthTimer = setTimeout(
      () => refreshOAuth().catch((e) => toast(e.message, true)),
      1500,
    );
}
async function refreshOAuth() {
  if (!oauthProvider) return;
  if (oauthBusy) {
    clearTimeout(oauthTimer);
    oauthTimer = setTimeout(
      () => refreshOAuth().catch((e) => toast(e.message, true)),
      300,
    );
    return;
  }
  const provider = oauthProvider;
  oauthBusy = true;
  try {
    const data = await api("/api/oauth/status", { provider });
    if (provider === oauthProvider) renderOAuth(data);
  } catch (e) {
    $("#oauth-status").textContent = e.message;
    throw e;
  } finally {
    oauthBusy = false;
  }
}
async function showOAuth(provider) {
  clearTimeout(oauthTimer);
  oauthProvider = provider;
  $("#oauth-panel").classList.remove("hidden");
  $("#oauth-description").textContent =
    (provider === "chatgpt"
      ? "ChatGPT · login via official Codex. "
      : "Claude Console · OAuth for API. Usage is billed in Console, separately from the Claude Pro/Max subscription. ") +
    "Not available in the secure worker runtime: profiles from this account cannot run workers or reviews.";
  $("#oauth-status").textContent = "Verifying connection…";
  $("#oauth-model-picker").classList.add("hidden");
  $("#oauth-link").classList.add("hidden");
  $("#oauth-cancel").classList.add("hidden");
  $("#oauth-login").disabled = false;
  await refreshOAuth();
}
let companyPreview = null;
let companyEpoch = 0;
let companyBusy = false;
function renderCompany(data) {
  const t = data.template;
  const roleName = (id) => t.roles.find((r) => r.id === id)?.title || "You · owner";
  $("#company-summary").replaceChildren(...[
    `${t.role_count} AI roles`, `${t.departments.length} departments`,
    `${t.max_reporting_layers} reporting layers`, "3 dev teams × 5",
  ].map((text) => el("span", "company-badge", text)));
  const departments = $("#company-departments");
  departments.replaceChildren();
  for (const department of t.departments) {
    const card = el("section", "company-department");
    card.append(el("h3", "", `${department.name} · ${department.count}`));
    card.append(el("p", "", department.purpose));
    for (const role of t.roles.filter((r) => r.department === department.id)) {
      const detail = el("details", "company-role");
      const summary = el("summary", "", role.title);
      if (role.squad) summary.append(el("span", "company-squad", role.squad));
      detail.append(summary, el("p", "", role.mission));
      detail.append(el("p", "company-role-meta", `Supervisor: ${roleName(role.reports_to)} · Reviewer: ${roleName(role.reviewed_by)}`));
      for (const [title, items] of [["Responsibilities", role.responsibilities], ["Outputs", role.deliverables]]) {
        detail.append(el("strong", "", title));
        const list = el("ul");
        list.append(...items.map((text) => el("li", "", text)));
        detail.append(list);
      }
      if (role.player_coach) detail.append(el("p", "company-role-meta", "The supervisor also performs expert work."));
      card.append(detail);
    }
    departments.append(card);
  }
  $("#company-workflow").replaceChildren(...t.delivery_workflow.map((step) => {
    const item = el("li");
    item.append(el("strong", "", step.label), el("p", "", step.exit_criteria));
    return item;
  }));
  $("#company-rules").replaceChildren(...t.operating_rules.map((text) => el("li", "", text)));
  $("#company-notice").textContent = t.runtime_notice;
  $("#company-project").textContent = `Project: ${project().name} · ${project().path}`;
  $("#company-status").textContent = data.installed
    ? `Saved: ${data.path}. A preview of the default template is shown above; your custom edits are in the project file.`
    : `Will be saved to ${data.path}. Then you can edit it in the editor and add it to the task brief. No task will start automatically.`;
  $("#company-install").disabled = data.installed;
  $("#company-install").textContent = data.installed ? "Saved in project" : "Save to project";
  $("#company-open").disabled = !data.installed;
  $("#company-context").disabled = !data.installed;
}
async function showCompany() {
  const selected = state.project;
  const epoch = ++companyEpoch;
  const data = await api(`/api/templates/company?project=${encodeURIComponent(selected)}`);
  if (selected !== state.project || epoch !== companyEpoch) return;
  companyPreview = data;
  renderCompany(data);
  if (!$("#company-dialog").open) $("#company-dialog").showModal();
}
function currentCompany() {
  if (!companyPreview || companyPreview.project !== state.project)
    throw new Error("The project has changed. Reopen the template.");
  return companyPreview;
}
async function installCompany() {
  if (companyBusy) return;
  const preview = currentCompany();
  companyBusy = true;
  $("#company-install").disabled = true;
  try {
    await api("/api/templates/company/install", { project: preview.project });
    if (preview.project !== state.project || companyPreview !== preview) return;
    preview.installed = true;
    renderCompany(preview);
    await loadTree();
    toast("AI Build Company is saved in the project.");
  } finally {
    companyBusy = false;
    if (companyPreview === preview)
      $("#company-install").disabled = preview.installed;
  }
}
function companyTaskContext(draft, path) {
  const marker = `[AI Build Company: ${path}]`;
  if (draft.includes(marker)) return draft;
  return (draft ? draft + "\n\n" : "") + marker + "\n" +
    `Read the file ${path} in the root of the current project. Use its rules, ` +
    "roles, and handover procedures for this task. Activate only the necessary roles in the " +
    "selected mode; do not assert the existence of separate workers or independent reviews " +
    "if the runtime did not actually create them. Save outputs to agreed files and document " +
    "performed checks. If a specific goal is missing, request it first. " +
    "Write all reports, questions, summaries, notes and generated documentation in English, regardless of the language of the input.";
}
async function addCompanyContext() {
  const preview = currentCompany();
  // Check that the project copy still exists before referencing it in a task.
  await api(`/api/file?project=${encodeURIComponent(preview.project)}&path=${encodeURIComponent(preview.path)}`);
  if (state.project !== preview.project || companyPreview !== preview) return;
  if (state.dirty && state.file?.path === preview.path)
    throw new Error("First, save template changes in the editor; the agent reads the file from disk.");
  const input = $("#task-input");
  input.value = companyTaskContext(input.value, preview.path);
  $("#company-dialog").close();
  input.focus();
  toast("Instructions added. Add a specific goal and start the task once you are ready.");
}
// Rebuilding a panel must not collapse open sections or drop evidence the owner loaded.
// Sections carry data-key; loaded blocks carry the "kept-result" class.
// Rebuilds keep only the sections the owner opened or closed; untouched sections follow the
// defaults of the new build, so a status change can still open, for example, the checks section.
function keepPanelState(panel, scope, build) {
  const open = new Map(), kept = new Map();
  if (panel.dataset.scope === scope)
    for (const section of panel.querySelectorAll("details[data-key]")) {
      const byDefault = section.dataset.defaultOpen;
      if (byDefault !== undefined && byDefault !== String(section.open)) open.set(section.dataset.key, section.open);
      const loaded = [...section.children].filter((node) => node.classList.contains("kept-result"));
      if (loaded.length) kept.set(section.dataset.key, loaded);
    }
  build();
  panel.dataset.scope = scope;
  for (const section of panel.querySelectorAll("details[data-key]")) {
    section.dataset.defaultOpen = String(section.open);
    if (open.has(section.dataset.key)) section.open = open.get(section.dataset.key);
    for (const node of kept.get(section.dataset.key) || []) section.append(node);
  }
}
async function loadWorkspace() {
  await refreshState();
  fillSelectors();
  await loadProjectTree();
  state.loaded = true;
  const previous = localStorage.getItem("switch.run");
  if (state.data.runs.some((r) => r.id === previous && r.project === state.project)) {
    const home = document.body.classList.contains("dashboard-home");
    try {
      await selectRun(previous);
      // Restoring the last run must not leave the dashboard landing view.
      if (home && typeof showDashboard === "function") showDashboard(true);
    } catch (error) {
      localStorage.removeItem("switch.run");
      toast("The previous task could not be reopened: " + error.message, true);
    }
  }
  if (typeof loadDashboard === "function") loadDashboard();
  if (typeof pollApprovalInbox === "function") pollApprovalInbox();
}
async function init() {
  icons();
  initMissions();
  initDecisionLab();
  initBrowserPilot();
  initProducts();
  initCompanies();
  initFlow();
  initPreview();
  // Bind every control before loading data, so a failed request never leaves a dead page.
  bind("#session-form", "submit", signInAgain);
  bind("#project-select", "change", (e) => selectProject(e.target.value));
  for (const id of ["#open-project", "#welcome-project"])
    bind(id, "click", () => {
      $("#project-path").value = project()?.path || "";
      $("#project-dialog").showModal();
      $("#project-path").focus();
    });
  $$(".close-dialog").forEach(
    (b) => (b.onclick = () => b.closest("dialog").close()),
  );
  bind("#project-form", "submit", async (e) => {
    e.preventDefault();
    const p = await api("/api/projects", { path: $("#project-path").value });
    await refreshState();
    if (p.id === state.project) {
      fillSelectors();
      await loadTree();
    } else await selectProject(p.id);
    $("#project-dialog").close();
  });
  bind("#refresh-files", "click", loadTree);
  bind("#new-file", "click", () => {
    $("#new-file-path").value = "";
    $("#file-dialog").showModal();
    $("#new-file-path").focus();
  });
  bind("#file-form", "submit", async (e) => {
    e.preventDefault();
    if (!canLeave()) return;
    const path = $("#new-file-path").value.trim();
    await api("/api/file", {
      project: state.project,
      path,
      content: "",
      revision: null,
    });
    $("#file-dialog").close();
    await openFile(path, true);
    await loadTree();
  });
  $$(".rail-btn[data-view]").forEach(
    (b) =>
      (b.onclick = () => {
        state.view = b.dataset.view;
        renderSidebar().catch((e) => toast(e.message, true));
      }),
  );
  $$("[data-mode]").forEach((b) => (b.onclick = () => setMode(b.dataset.mode)));
  $$(".bottom-tab").forEach(
    (b) =>
      (b.onclick = () => {
        state.bottom = b.dataset.bottom;
        $$(".bottom-tab").forEach((t) => t.classList.toggle("active", t === b));
        renderBottom().catch((e) => toast(e.message, true));
      }),
  );
  bind("#model-select", "change", () => {
    localStorage.setItem("switch.model", $("#model-select").value);
    updateModelLabel();
  });
  bind("#templates-button", "click", showCompany);
  bind("#company-install", "click", installCompany);
  bind("#company-context", "click", addCompanyContext);
  bind("#company-open", "click", async () => {
    const preview = currentCompany();
    $("#company-dialog").close();
    await openFile(preview.path);
  });
  bind("#models-button", "click", () => {
    renderModels();
    $("#models-dialog").showModal();
  });
  bind("#add-model", "click", () => fillModel({}));
  bind("#oauth-chatgpt", "click", () => showOAuth("chatgpt"));
  bind("#oauth-claude", "click", () => showOAuth("claude_console"));
  bind("#oauth-refresh", "click", refreshOAuth);
  bind("#oauth-login", "click", async () => {
    const provider = oauthProvider;
    $("#oauth-login").disabled = true;
    try {
      await api("/api/oauth/start", { provider });
      await refreshOAuth();
    } catch (e) {
      $("#oauth-login").disabled = false;
      throw e;
    }
  });
  bind("#oauth-cancel", "click", async () => {
    await api("/api/oauth/cancel", { provider: oauthProvider });
    await refreshOAuth();
  });
  bind("#oauth-add", "click", async () => {
    const result = await api("/api/oauth/models", {
      provider: oauthProvider,
      model: $("#oauth-models").value,
    });
    await refreshState();
    fillSelectors();
    renderModels();
    fillModel(state.data.profiles.find((p) => p.id === result.id));
    toast("Model added. Select it for the task in the model list.");
  });
  bind("#oauth-remove", "click", async () => {
    await api("/api/oauth/disconnect", {
      profile: $("#oauth-remove").dataset.profile,
    });
    await refreshState();
    fillSelectors();
    renderModels();
    toast("Profile removed from Studio. The account login remains intact.");
  });
  bind("#model-form", "submit", async (e) => {
    e.preventDefault();
    await saveModel();
    toast("Model saved.");
  });
  bind("#probe-model", "click", async () => {
    const b = $("#probe-model");
    b.disabled = true;
    $("#probe-result").textContent = "Verifying availability…";
    try {
      const id = await saveModel();
      const r = await api("/api/probe", { profile: id });
      $("#probe-result").textContent = r.model_found
        ? `Connected · model found · ${r.ms} ms`
        : `Server responds, but the model is not in the list. Available: ${r.models.join(", ")}`;
    } catch (e) {
      $("#probe-result").textContent = e.message;
    } finally {
      b.disabled = false;
    }
  });
  bind("#help-button", "click", () => $("#help-dialog").showModal());
  bind("#welcome-task", "click", () => $("#task-input").focus());
  $$(".starter").forEach(
    (b) =>
      (b.onclick = () => {
        $("#task-input").value = b.dataset.prompt;
        $("#task-input").focus();
      }),
  );
  bind("#task-form", "submit", launchTask);
  bind("#stop-task", "click", async () => {
    await api("/api/stop", { run: state.run });
    await pollRun();
    toast("Stopping task…");
  });
  bind("#new-task", "click", () => {
    if (active(run())) {
      $("#task-input").focus();
      toast("First, finish or stop the running task.");
      return;
    }
    state.run = null;
    state.events = [];
    state.offset = 0;
    state.stream = null;
    state.thinking = null;
    state.approvalKey = "";
    localStorage.removeItem("switch.run");
    resetConversation();
      $("#task-input").value = "";
    updateRunControls();
    renderBottom();
    $("#task-input").focus();
  });
  bind("#save-file", "click", saveFile);
  bind("#reload-file", "click", () => state.file && openFile(state.file.path));
  bind("#editor", "input", () => {
    markDirty();
    updateLines();
    updateCursor();
  });
  bind("#editor", "scroll", () => {
    $("#line-numbers").scrollTop = $("#editor").scrollTop;
  });
  bind("#editor", "click", updateCursor);
  bind("#editor", "keyup", updateCursor);
  bind("#editor", "keydown", (e) => {
    if (e.key === "Tab") {
      e.preventDefault();
      const t = e.target;
      t.setRangeText("    ", t.selectionStart, t.selectionEnd, "end");
      markDirty();
      updateLines();
    }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "s") {
      e.preventDefault();
      saveFile().catch((err) => toast(err.message, true));
    }
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && !$("dialog[open]")) {
      e.preventDefault();
      if (document.body.classList.contains("dashboard-home")) {
        if (!$("#dashboard-submit").disabled) $("#dashboard-task-form").requestSubmit();
      } else if (!$("#run-task").disabled) $("#task-form").requestSubmit();
    }
  });
  window.addEventListener("beforeunload", (e) => {
    if (state.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  updateRunControls();
  initApprovalInbox();
  initDashboard();
  setInterval(async () => {
    if (state.polling) return;
    state.polling = true;
    try {
      if (sessionExpired) {
        await probeSession();
        return;
      }
      if (!state.loaded) await loadWorkspace();
      else {
        await refreshState();
        await pollRun();
        updateRunControls();
        if (state.view === "runs") await renderSidebar();
      }
      $("#connection").replaceChildren(
        el("i"),
        document.createTextNode(" Connected to Studio"),
      );
    } catch (e) {
      if (!sessionExpired) $("#connection").textContent = "Connection lost";
    } finally {
      state.polling = false;
    }
  }, 1500);
  document.body.inert = false;
  state.polling = true;
  try {
    await loadWorkspace();
  } catch (error) {
    // Polling retries the initial load; every control is already bound.
    toast("Failed to load Studio: " + error.message + " Retrying automatically.", true);
    if (!sessionExpired) $("#connection").textContent = "Connection lost";
  } finally {
    state.polling = false;
  }
}
init()
  .catch((e) => toast("Failed to load Studio: " + e.message, true))
  .finally(() => {
    document.body.inert = false;
  });
