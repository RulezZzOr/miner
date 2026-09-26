"use strict";
let productSelection = null;
let productViewProject = null;
let productLoading = false;
let productMutating = false;
let productEpoch = 0;
let productSnapshot = "";
let productDirty = false;
const productKinds = {web:"Websites and apps", service:"API and service", automation:"Automation", data:"Data and analytics", content:"Content and documentation", custom:"Custom digital product"};
const productWork = {initial:"First version", feature:"New feature", bug:"Fix", maintenance:"Maintenance"};
const productStatus = {queued:"In queue", in_progress:"In progress", done:"Accepted", cancelled:"Deferred"};
function productButton(label, action, primary = false) {
  return missionButton(label, action, primary);
}
function productField(form, name, label, value, type = "text") {
  const wrapper = el("label", "", label);
  const input = el(type === "textarea" ? "textarea" : "input");
  input.name = name;
  if (type !== "textarea") input.type = type;
  else { input.rows = 3; input.maxLength = 12000; }
  if (type === "checkbox") { input.checked = Boolean(value); wrapper.className = "mission-permission"; }
  else input.value = value ?? "";
  input.oninput = () => { productDirty = true; form.dataset.dirty = "true"; };
  if (type === "checkbox") wrapper.prepend(input);
  else wrapper.append(input);
  form.append(wrapper); return input;
}
async function productAction(id, action, extra = {}, savedForm = null) {
  if (productMutating) return;
  const project = state.project;
  productMutating = true; productEpoch++;
  try {
    await api("/api/products/action", {id, action, ...extra});
    if (project !== state.project) return;
    if (savedForm) {
      delete savedForm.dataset.dirty;
      if (action === "add") savedForm.reset();
      productDirty = $$("#product-detail form").some(f => f.dataset.dirty === "true");
    }
    productSnapshot = "";
  } finally { productMutating = false; }
  await loadProducts(!productDirty);
  if (action === "pause") toast("Product management is paused.");
}
async function openProductMission(id) {
  if (productDirty) throw new Error("First, save the pending product changes.");
  $("#products-dialog").close();
  missionViewProject = state.project; missionSelection = id; missionSnapshot = "";
  await showMissions();
}
function renderProduct(p) {
  const panel = $("#product-detail"); panel.replaceChildren();
  if (!p) { panel.append(el("p", "", "Create a product. Its development, requirements, and accepted versions will stay together.")); return; }
  panel.append(el("h3", "", p.title), el("span", "mission-state", p.status === "paused" ? "Paused" : p.releases.length ? "Further development" : "First version"));
  panel.append(el("p", "", p.goal), el("p", "missions-note", `${productKinds[p.kind]} · ${p.cycles.length}/${p.cycle_limit} execution · ${p.releases.length} accepted versions`));
  panel.append(el("p", "mission-message", p.message));
  const criteria = el("ul"); criteria.append(...p.criteria.map(c => el("li", "", c))); panel.append(criteria);
  const active = p.cycles.find(c => !["accepted", "cancelled", "expired"].includes(c.status));
  const initial = p.backlog.find(i => i.kind === "initial" && i.status === "queued");
  const actions = el("div", "mission-actions");
  if (active) actions.append(productButton(`Open execution · ${missionLabels[active.status] || active.status}`, () => openProductMission(active.mission), true));
  actions.append(productButton(p.status === "paused" ? "Restore management" : "Pause product", () => productAction(p.id, p.status === "paused" ? "resume" : "pause")));
  if (p.releases.length && !active) actions.append(productButton("Verify files of the latest version", () => productAction(p.id, "check")));
  panel.append(actions);
  if (p.health) {
    panel.append(el("p", p.health.findings.length ? "mission-feedback" : "missions-note",
      `${new Date(p.health.checked * 1000).toLocaleString("en-GB")} · ${p.health.status === "unchanged" ? "Files match the accepted version." : "Files have changed or are unavailable."}`));
    for (const finding of p.health.findings) panel.append(el("p", "mission-feedback", `${finding.path}: ${finding.reason}`));
  }
  panel.append(el("h3", "", "Fixes, new features, and maintenance"));
  for (const item of p.backlog) {
    const row = el("div", "product-work-item");
    row.append(el("strong", "", item.title), el("p", "missions-note", `${productWork[item.kind]} · ${productStatus[item.status]} · priority ${item.priority}`));
    const detail = el("details"); detail.append(el("summary", "", "Task brief and criteria"), el("p", "", item.goal));
    const checks = el("ul"); checks.append(...item.criteria.map(c => el("li", "", c))); detail.append(checks); row.append(detail);
    if (item.status === "queued") {
      if (!active && p.status === "active" && (!initial || initial.id === item.id)) row.append(productButton("Start execution", async () => {
        if (productDirty) throw new Error("First, save the pending product changes.");
        if (state.dirty) throw new Error("First save the open file.");
        await productAction(p.id, "start", {item:item.id});
      }, true));
      row.append(productButton("Defer", () => productAction(p.id, "dismiss", {item:item.id})));
    } else if (item.mission) row.append(productButton("Progress and reports", () => openProductMission(item.mission)));
    panel.append(row);
  }
  const add = el("details", "product-form-panel"); add.append(el("summary", "", "Add requirement"));
  const form = el("form");
  const kindLabel = el("label", "", "Work type"), kind = el("select"); kind.name = "kind";
  for (const key of ["feature", "bug", "maintenance"]) { const o = el("option", "", productWork[key]); o.value = key; kind.append(o); }
  kind.onchange = () => { productDirty = true; form.dataset.dirty = "true"; }; kindLabel.append(kind); form.append(kindLabel);
  const title = productField(form, "title", "Requirement name", ""); title.required = true; title.maxLength = 160;
  const goal = productField(form, "goal", "What needs to change?", "", "textarea"); goal.required = true;
  const checks = productField(form, "criteria", "Change criteria, one per line. Original product criteria are preserved.", "", "textarea"); checks.required = true;
  const priority = productField(form, "priority", "Priority: 1 highest, 3 lowest", 2, "number"); priority.min = 1; priority.max = 3;
  const submit = el("button", "button primary", "Save requirement"); submit.type = "submit"; form.append(submit);
  form.onsubmit = async event => {
    event.preventDefault(); submit.disabled = true;
    try { await productAction(p.id, "add", {kind:kind.value, title:title.value, goal:goal.value, criteria:checks.value.split("\n").map(v=>v.trim()).filter(Boolean), priority:Number(priority.value)}, form); }
    catch (error) { toast(error.message, true); }
    finally { submit.disabled = false; }
  };
  add.append(form); panel.append(add);
  const settingsPanel = el("details", "product-form-panel"); settingsPanel.append(el("summary", "", "Regular maintenance and automatic continuation"));
  const settings = el("form");
  const autopilot = productField(settings, "autopilot", "Automatically start queue and confirm plans without open questions", p.autopilot, "checkbox");
  const interval = productField(settings, "maintenance_days", "Maintenance every N days (0 = disabled)", p.maintenance_days, "number"); interval.min = 0; interval.max = 30;
  const limit = productField(settings, "cycle_limit", "Total execution limit for the product", p.cycle_limit, "number"); limit.min = 1; limit.max = 100;
  settings.append(el("p", "missions-note", `Each execution: up to ${p.settings.days} days, ${p.settings.max_attempts} runs, ${p.settings.attempt_minutes} minutes per run. Work: ${p.settings.profile}; review: ${p.settings.review_profile}. ${p.settings.auto_approve ? "Tool actions are pre-approved." : "Tool actions require approval."} ${p.deployment_settings?.auto_release ? "Verified versions are automatically accepted and deployed in autopilot mode." : "Version acceptance remains your responsibility."} Studio must be running; computer sleep will interrupt the work.`));
  if (p.next_maintenance) settings.append(el("p", "missions-note", `Next maintenance: ${new Date(p.next_maintenance * 1000).toLocaleString("en-GB")}`));
  const save = el("button", "button primary", "Save management mode"); save.type = "submit"; settings.append(save);
  settings.onsubmit = async event => {
    event.preventDefault(); save.disabled = true;
    try { await productAction(p.id, "settings", {autopilot:autopilot.checked, maintenance_days:Number(interval.value), cycle_limit:Number(limit.value)}, settings); }
    catch (error) { toast(error.message, true); }
    finally { save.disabled = false; }
  };
  settingsPanel.append(settings); panel.append(settingsPanel);
  const deploymentPanel = el("details", "product-form-panel");
  deploymentPanel.append(el("summary", "", "Local deployment, availability, and fixes"));
  const deploymentForm = el("form"), deployment = p.deployment_settings || {};
  const command = productField(deploymentForm, "argv", "Service command", (deployment.argv || []).map(a => "'" + a.replaceAll("'", "'\\''") + "'").join(" "));
  command.placeholder = "python3 -m http.server {port} --bind {host}";
  const healthPath = productField(deploymentForm, "health_path", "Path for availability verification", deployment.health_path || "/");
  const healthText = productField(deploymentForm, "expected_text", "Expected text in response (optional)", deployment.expected_text || "");
  const repair = productField(deploymentForm, "auto_repair", "Add discovered incidents to the fix queue", deployment.auto_repair, "checkbox");
  const releaseAutomatically = productField(deploymentForm, "auto_release", "When automatic continuation is enabled, accept and deploy the version after independent checks", deployment.auto_release, "checkbox");
  const restart = productField(deploymentForm, "restart_on_start", "Restore service after Studio restart (up to 3 attempts)", deployment.restart_on_start, "checkbox");
  deploymentForm.append(el("p", "missions-note", "Runs only on this computer. {host} is 127.0.0.1, {port} is a free port, and {data_dir} is the persistent data folder. Each deployment has its own URL. A new version first passes two HTTP checks; then the previous process is terminated. Three availability failures create an incident. An older verified version can be redeployed using the button below."));
  const deploySave = el("button", "button", "Save service settings"); deploySave.type = "submit"; deploymentForm.append(deploySave);
  deploymentForm.onsubmit = async event => {
    event.preventDefault(); deploySave.disabled = true;
    try { await productAction(p.id, "deployment_settings", {argv:command.value, health_path:healthPath.value,
      expected_text:healthText.value, auto_repair:repair.checked, auto_release:releaseAutomatically.checked, restart_on_start:restart.checked}, deploymentForm); }
    catch (error) { toast(error.message, true); }
    finally { deploySave.disabled = false; }
  };
  deploymentPanel.append(deploymentForm);
  deploymentPanel.append(productButton("Load service status and incidents", async () => {
    const data = await api(`/api/deployments?product=${encodeURIComponent(p.id)}`);
    const status = el("div", "deployment-history");
    const labels = {starting:"Verifying", healthy:"Available", unhealthy:"Incident", stopped:"Stopped", interrupted:"Interrupted", failed:"Startup failed"};
    for (const record of data.deployments) {
      const row = el("details"); row.append(el("summary", "", `Version ${record.number} · ${labels[record.status] || record.status}`));
      if (record.status === "healthy") {
        const link = el("a", "button", "Open service ↗"); link.href = record.url; link.target = "_blank"; link.rel = "noopener noreferrer"; row.append(link);
      }
      if (record.error) row.append(el("p", "mission-feedback", record.error));
      if (record.incident_item) row.append(el("p", "", "Incident has been forwarded to the fix queue."));
      const log = el("pre"); log.textContent = record.log || "No process output."; row.append(log);
      for (const check of record.health.slice(-5)) row.append(el("p", "missions-note", `${new Date(check.at*1000).toLocaleString("en-GB")} · ${check.passed ? "HTTP check passed" : check.error || "HTTP check failed"}`));
      status.append(row);
    }
    if (!data.deployments.length) status.append(el("p", "", "Nothing is deployed yet."));
    deploymentPanel.querySelector(".deployment-history")?.remove(); deploymentPanel.append(status);
  }));
  deploymentPanel.append(productButton("Stop local service", () => productAction(p.id, "deployment_stop")));
  panel.append(deploymentPanel);
  panel.append(el("h3", "", "Accepted versions"));
  const lastRestore = p.restorations?.at(-1);
  if (lastRestore?.backup && lastRestore.kind !== "undo") {
    const backup = el("details"); backup.append(el("summary", "", "Backup before last restore"));
    backup.append(productButton("Preview before restore", async () => {
      const preview = await api("/api/products/action", {id:p.id, action:"undo_restore_preview", operation:lastRestore.operation});
      const details = el("div", "restore-preview");
      details.append(el("p", "", `Will change ${preview.changed.length} files. Previous custom file modifications will also be restored. The running service remains unchanged.`));
      const paths = el("pre"); paths.textContent = preview.changed.join("\n"); details.append(paths);
      if (preview.shape_conflicts?.length) details.append(el("p", "mission-feedback",
        "First, manually resolve the file/folder swap and restore the preview: " + preview.shape_conflicts.join(", ")));
      else details.append(productButton("Restore last restore", () => productAction(p.id, "undo_restore",
        {operation:lastRestore.operation, revision:preview.revision}), true));
      backup.querySelector(".restore-preview")?.remove(); backup.append(details);
    }));
    panel.append(backup);
  }
  if (!p.releases.length) panel.append(el("p", "missions-note", "The first version appears after successful review and acceptance in AI Projects."));
  for (const release of [...p.releases].reverse()) {
    const card = el("details", "product-work-item"); card.append(el("summary", "", `Version ${release.number} · ${new Date(release.accepted * 1000).toLocaleString("en-GB")}`), el("p", "", release.summary));
    const files = el("ul"); files.append(...release.artifacts.map(a => el("li", "", `${a.path} · ${a.sha256.slice(0,12)}`))); card.append(files);
    card.append(productButton("Report of accepted version", () => openProductMission(release.mission))); panel.append(card);
    if (release.version_id && release.verification_id && p.deployment_settings)
      card.append(productButton(`Deploy locally version ${release.number}`, () => productAction(p.id, "deploy", {number:release.number}), true));
    if (release.version_id) card.append(productButton("Preview of restoring this version", async () => {
      const preview = await api("/api/products/action", {id:p.id, action:"restore_preview", number:release.number});
      const details = el("div");
      details.append(el("p", "", `Will change ${preview.changed.length} files. Current state will be saved before restoration.`));
      const paths = el("pre"); paths.textContent = preview.changed.join("\n"); details.append(paths);
      if (preview.shape_conflicts?.length) details.append(el("p", "mission-feedback",
        "First, manually resolve the file/folder swap and restore the preview: " + preview.shape_conflicts.join(", ")));
      else details.append(productButton(`Restore version ${release.number}`, () => productAction(p.id, "restore", {number:release.number, revision:preview.revision}), true));
      card.append(details);
    }));
  }
  panel.append(el("p", "missions-note", "New versions save the content of source files for restoration. The environment, dependencies, .env, and operational data outside the project are not part of this restoration. Acceptance or restoration does not confirm deployment."));
}
async function loadProducts(force = false) {
  if (productLoading || productMutating || !$("#products-dialog").open) return;
  productLoading = true;
  const project = state.project, epoch = productEpoch;
  try {
    const data = await api(`/api/products?project=${encodeURIComponent(project)}`);
    if (project !== state.project || epoch !== productEpoch || productMutating || !$("#products-dialog").open) return;
    $("#products-health").textContent = data.controller_error || "The product continues with further versions. Studio on this machine manages scheduled work.";
    const signature = JSON.stringify(data.products);
    if (!force && (productDirty || signature === productSnapshot)) return;
    productSnapshot = signature;
    if (!data.products.some(p => p.id === productSelection)) productSelection = data.products[0]?.id || null;
    const list = $("#product-list"); list.replaceChildren();
    for (const p of data.products) {
      const button = productButton(`${p.title} · ${p.releases.length} versions`, async () => {
        if (productDirty) throw new Error("First, save the pending product changes.");
        productSelection = p.id; productSnapshot = ""; await loadProducts(true);
      });
      button.classList.toggle("active", p.id === productSelection); list.append(button);
    }
    renderProduct(data.products.find(p => p.id === productSelection));
  } finally { productLoading = false; }
}
async function showProducts() {
  fillDecisionModels("#product-decision-profile");
  if (productViewProject !== state.project) {
    productSelection = null; productSnapshot = ""; productDirty = false; productEpoch++;
    $("#product-form").reset(); productViewProject = state.project;
  }
  for (const id of ["#product-profile", "#product-review-profile"]) {
    const old = $(id).value;
    const role = id === "#product-review-profile" ? "review" : "work";
    const preferred = defaultModelForRole(role);
    const profiles = runnableProfiles();
    $(id).replaceChildren(...profiles.map(p => { const option = el("option", "", `${p.model} · ${profileLabel(p)}`); option.value = p.id; return option; }));
    $(id).value = profiles.some(p => p.id === old) ? old : profiles.some(p => p.id === preferred) ? preferred : profiles[0]?.id || "";
  }
  $("#products-dialog").showModal(); await loadProducts(!productDirty);
}
async function adoptProduct(m) {
  const p = await api("/api/products", {project:m.project, source_mission:m.id, kind:"custom"});
  $("#missions-dialog").close();
  productViewProject = state.project; productSelection = p.id; productSnapshot = ""; productDirty = false;
  await showProducts();
}
function initProducts() {
  bind("#products-button", "click", showProducts);
  bind("#product-form", "submit", async event => {
    event.preventDefault();
    if (productMutating) return;
    const project = state.project;
    productMutating = true; productEpoch++;
    try {
      const p = await api("/api/products", {
        project, title:$("#product-title").value, goal:$("#product-goal").value,
        kind:$("#product-kind").value,
        criteria:$("#product-criteria").value.split("\n").map(v=>v.trim()).filter(Boolean),
        verification_checks:$("#product-checks").value,
        decision_profile:$("#product-decision-profile").value,
        decision_mode:$("#product-decision-profile").value ? $("#product-decision-mode").value : "off",
        constraints:$("#product-constraints").value, sources:$("#product-sources").value,
        profile:$("#product-profile").value, review_profile:$("#product-review-profile").value,
        auto_approve:$("#product-auto").checked, days:Number($("#product-days").value),
        max_attempts:Number($("#product-attempts").value), attempt_minutes:Number($("#product-minutes").value),
        max_turns:Number($("#product-turns").value),
      });
      if (project !== state.project) return;
      productSelection = p.id; productSnapshot = ""; productDirty = false;
      $("#product-form").reset(); $("#product-new").open = false;
    } finally { productMutating = false; }
    await loadProducts(true);
    toast("Product saved. You’ll launch the first version from its queue.");
  });
  setInterval(() => loadProducts().catch(error => toast(error.message, true)), 3000);
}
