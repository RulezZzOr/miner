"use strict";
let productSelection = null;
let productViewProject = null;
let productLoading = false;
let productMutating = false;
let productEpoch = 0;
let productSnapshot = "";
let productDirty = false;
const productKinds = {web:"Web a aplikace", service:"API a služba", automation:"Automatizace", data:"Data a analytika", content:"Obsah a dokumentace", custom:"Vlastní digitální produkt"};
const productWork = {initial:"První verze", feature:"Nová funkce", bug:"Oprava", maintenance:"Údržba"};
const productStatus = {queued:"Ve frontě", in_progress:"Probíhá", done:"Převzato", cancelled:"Odloženo"};
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
  if (action === "pause") toast("Správa produktu je pozastavená.");
}
async function openProductMission(id) {
  if (productDirty) throw new Error("Nejdřív ulož rozepsané změny produktu.");
  $("#products-dialog").close();
  missionViewProject = state.project; missionSelection = id; missionSnapshot = "";
  await showMissions();
}
function renderProduct(p) {
  const panel = $("#product-detail"); panel.replaceChildren();
  if (!p) { panel.append(el("p", "", "Založ produkt. Jeho vývoj, požadavky a převzaté verze zůstanou pohromadě.")); return; }
  panel.append(el("h3", "", p.title), el("span", "mission-state", p.status === "paused" ? "Pozastaveno" : p.releases.length ? "Další rozvoj" : "První verze"));
  panel.append(el("p", "", p.goal), el("p", "missions-note", `${productKinds[p.kind]} · ${p.cycles.length}/${p.cycle_limit} realizací · ${p.releases.length} převzatých verzí`));
  panel.append(el("p", "mission-message", p.message));
  const criteria = el("ul"); criteria.append(...p.criteria.map(c => el("li", "", c))); panel.append(criteria);
  const active = p.cycles.find(c => !["accepted", "cancelled", "expired"].includes(c.status));
  const initial = p.backlog.find(i => i.kind === "initial" && i.status === "queued");
  const actions = el("div", "mission-actions");
  if (active) actions.append(productButton(`Otevřít realizaci · ${missionLabels[active.status] || active.status}`, () => openProductMission(active.mission), true));
  actions.append(productButton(p.status === "paused" ? "Obnovit správu" : "Pozastavit produkt", () => productAction(p.id, p.status === "paused" ? "resume" : "pause")));
  if (p.releases.length && !active) actions.append(productButton("Ověřit soubory poslední verze", () => productAction(p.id, "check")));
  panel.append(actions);
  if (p.health) {
    panel.append(el("p", p.health.findings.length ? "mission-feedback" : "missions-note",
      `${new Date(p.health.checked * 1000).toLocaleString("cs-CZ")} · ${p.health.status === "unchanged" ? "Soubory odpovídají převzaté verzi." : "Soubory se změnily nebo nejsou dostupné."}`));
    for (const finding of p.health.findings) panel.append(el("p", "mission-feedback", `${finding.path}: ${finding.reason}`));
  }
  panel.append(el("h3", "", "Opravy, nové funkce a údržba"));
  for (const item of p.backlog) {
    const row = el("div", "product-work-item");
    row.append(el("strong", "", item.title), el("p", "missions-note", `${productWork[item.kind]} · ${productStatus[item.status]} · priorita ${item.priority}`));
    const detail = el("details"); detail.append(el("summary", "", "Zadání a kritéria"), el("p", "", item.goal));
    const checks = el("ul"); checks.append(...item.criteria.map(c => el("li", "", c))); detail.append(checks); row.append(detail);
    if (item.status === "queued") {
      if (!active && p.status === "active" && (!initial || initial.id === item.id)) row.append(productButton("Spustit realizaci", async () => {
        if (productDirty) throw new Error("Nejdřív ulož rozepsané změny produktu.");
        if (state.dirty) throw new Error("Nejdřív ulož otevřený soubor.");
        await productAction(p.id, "start", {item:item.id});
      }, true));
      row.append(productButton("Odložit", () => productAction(p.id, "dismiss", {item:item.id})));
    } else if (item.mission) row.append(productButton("Průběh a reporty", () => openProductMission(item.mission)));
    panel.append(row);
  }
  const add = el("details", "product-form-panel"); add.append(el("summary", "", "Přidat požadavek"));
  const form = el("form");
  const kindLabel = el("label", "", "Druh práce"), kind = el("select"); kind.name = "kind";
  for (const key of ["feature", "bug", "maintenance"]) { const o = el("option", "", productWork[key]); o.value = key; kind.append(o); }
  kind.onchange = () => { productDirty = true; form.dataset.dirty = "true"; }; kindLabel.append(kind); form.append(kindLabel);
  const title = productField(form, "title", "Název požadavku", ""); title.required = true; title.maxLength = 160;
  const goal = productField(form, "goal", "Co se má změnit?", "", "textarea"); goal.required = true;
  const checks = productField(form, "criteria", "Kritéria změny, každé na řádek. Původní kritéria produktu se zachovají.", "", "textarea"); checks.required = true;
  const priority = productField(form, "priority", "Priorita: 1 nejvyšší, 3 nejnižší", 2, "number"); priority.min = 1; priority.max = 3;
  const submit = el("button", "button primary", "Uložit požadavek"); submit.type = "submit"; form.append(submit);
  form.onsubmit = async event => {
    event.preventDefault(); submit.disabled = true;
    try { await productAction(p.id, "add", {kind:kind.value, title:title.value, goal:goal.value, criteria:checks.value.split("\n").map(v=>v.trim()).filter(Boolean), priority:Number(priority.value)}, form); }
    catch (error) { toast(error.message, true); }
    finally { submit.disabled = false; }
  };
  add.append(form); panel.append(add);
  const settingsPanel = el("details", "product-form-panel"); settingsPanel.append(el("summary", "", "Pravidelná údržba a automatické navazování"));
  const settings = el("form");
  const autopilot = productField(settings, "autopilot", "Automaticky spouštět frontu a potvrzovat plány bez otevřených otázek", p.autopilot, "checkbox");
  const interval = productField(settings, "maintenance_days", "Údržba každých N dní (0 = vypnutá)", p.maintenance_days, "number"); interval.min = 0; interval.max = 30;
  const limit = productField(settings, "cycle_limit", "Celkový limit realizací produktu", p.cycle_limit, "number"); limit.min = 1; limit.max = 100;
  settings.append(el("p", "missions-note", `Každá realizace: nejvýše ${p.settings.days} dní, ${p.settings.max_attempts} běhů, ${p.settings.attempt_minutes} minut na běh. Práce: ${p.settings.profile}; kontrola: ${p.settings.review_profile}. ${p.settings.auto_approve ? "Akce nástrojů jsou předem povolené." : "Akce nástrojů vyžadují schválení."} ${p.deployment_settings?.auto_release ? "Ověřené verze se v autopilotu přebírají a nasazují automaticky." : "Převzetí verze zůstává na tobě."} Studio musí běžet; uspání počítače práci přeruší.`));
  if (p.next_maintenance) settings.append(el("p", "missions-note", `Další údržba: ${new Date(p.next_maintenance * 1000).toLocaleString("cs-CZ")}`));
  const save = el("button", "button primary", "Uložit režim správy"); save.type = "submit"; settings.append(save);
  settings.onsubmit = async event => {
    event.preventDefault(); save.disabled = true;
    try { await productAction(p.id, "settings", {autopilot:autopilot.checked, maintenance_days:Number(interval.value), cycle_limit:Number(limit.value)}, settings); }
    catch (error) { toast(error.message, true); }
    finally { save.disabled = false; }
  };
  settingsPanel.append(settings); panel.append(settingsPanel);
  const deploymentPanel = el("details", "product-form-panel");
  deploymentPanel.append(el("summary", "", "Místní nasazení, dostupnost a opravy"));
  const deploymentForm = el("form"), deployment = p.deployment_settings || {};
  const command = productField(deploymentForm, "argv", "Příkaz služby", (deployment.argv || []).map(a => "'" + a.replaceAll("'", "'\\''") + "'").join(" "));
  command.placeholder = "python3 -m http.server {port} --bind {host}";
  const healthPath = productField(deploymentForm, "health_path", "Cesta pro ověření dostupnosti", deployment.health_path || "/");
  const healthText = productField(deploymentForm, "expected_text", "Očekávaný text v odpovědi (volitelné)", deployment.expected_text || "");
  const repair = productField(deploymentForm, "auto_repair", "Přidávat zjištěné incidenty do fronty oprav", deployment.auto_repair, "checkbox");
  const releaseAutomatically = productField(deploymentForm, "auto_release", "Při zapnutém automatickém navazování převzít a nasadit verzi po nezávislých kontrolách", deployment.auto_release, "checkbox");
  const restart = productField(deploymentForm, "restart_on_start", "Obnovit službu po restartu Studia (nejvýše 3 pokusy)", deployment.restart_on_start, "checkbox");
  deploymentForm.append(el("p", "missions-note", "Běží pouze na tomto počítači. {host} je 127.0.0.1, {port} je volný port a {data_dir} je trvalá složka dat. Každé nasazení má vlastní URL. Nová verze nejdřív projde dvěma HTTP kontrolami; pak se předchozí proces ukončí. Tři selhání dostupnosti vytvoří incident. Starší ověřenou verzi lze znovu nasadit tlačítkem níže."));
  const deploySave = el("button", "button", "Uložit nastavení služby"); deploySave.type = "submit"; deploymentForm.append(deploySave);
  deploymentForm.onsubmit = async event => {
    event.preventDefault(); deploySave.disabled = true;
    try { await productAction(p.id, "deployment_settings", {argv:command.value, health_path:healthPath.value,
      expected_text:healthText.value, auto_repair:repair.checked, auto_release:releaseAutomatically.checked, restart_on_start:restart.checked}, deploymentForm); }
    catch (error) { toast(error.message, true); }
    finally { deploySave.disabled = false; }
  };
  deploymentPanel.append(deploymentForm);
  deploymentPanel.append(productButton("Načíst stav služby a incidenty", async () => {
    const data = await api(`/api/deployments?product=${encodeURIComponent(p.id)}`);
    const status = el("div", "deployment-history");
    const labels = {starting:"Ověřuje se", healthy:"Dostupná", unhealthy:"Incident", stopped:"Zastavena", interrupted:"Přerušena", failed:"Start selhal"};
    for (const record of data.deployments) {
      const row = el("details"); row.append(el("summary", "", `Verze ${record.number} · ${labels[record.status] || record.status}`));
      if (record.status === "healthy") {
        const link = el("a", "button", "Otevřít službu ↗"); link.href = record.url; link.target = "_blank"; link.rel = "noopener noreferrer"; row.append(link);
      }
      if (record.error) row.append(el("p", "mission-feedback", record.error));
      if (record.incident_item) row.append(el("p", "", "Incident byl předán do fronty oprav."));
      const log = el("pre"); log.textContent = record.log || "Bez výstupu procesu."; row.append(log);
      for (const check of record.health.slice(-5)) row.append(el("p", "missions-note", `${new Date(check.at*1000).toLocaleString("cs-CZ")} · ${check.passed ? "HTTP kontrola prošla" : check.error || "HTTP kontrola neprošla"}`));
      status.append(row);
    }
    if (!data.deployments.length) status.append(el("p", "", "Zatím není nic nasazeno."));
    deploymentPanel.querySelector(".deployment-history")?.remove(); deploymentPanel.append(status);
  }));
  deploymentPanel.append(productButton("Zastavit místní službu", () => productAction(p.id, "deployment_stop")));
  panel.append(deploymentPanel);
  panel.append(el("h3", "", "Převzaté verze"));
  const lastRestore = p.restorations?.at(-1);
  if (lastRestore?.backup && lastRestore.kind !== "undo") {
    const backup = el("details"); backup.append(el("summary", "", "Záloha před poslední obnovou"));
    backup.append(productButton("Náhled návratu před obnovu", async () => {
      const preview = await api("/api/products/action", {id:p.id, action:"undo_restore_preview", operation:lastRestore.operation});
      const details = el("div", "restore-preview");
      details.append(el("p", "", `Změní se ${preview.changed.length} souborů. Vrátí se také tehdejší vlastní úpravy souborů. Běžící služba se nemění.`));
      const paths = el("pre"); paths.textContent = preview.changed.join("\n"); details.append(paths);
      if (preview.shape_conflicts?.length) details.append(el("p", "mission-feedback",
        "Nejdřív ručně vyřeš záměnu souboru a složky a obnov náhled: " + preview.shape_conflicts.join(", ")));
      else details.append(productButton("Vrátit poslední obnovu", () => productAction(p.id, "undo_restore",
        {operation:lastRestore.operation, revision:preview.revision}), true));
      backup.querySelector(".restore-preview")?.remove(); backup.append(details);
    }));
    panel.append(backup);
  }
  if (!p.releases.length) panel.append(el("p", "missions-note", "První verze se objeví po úspěšném review a převzetí v Projektech AI."));
  for (const release of [...p.releases].reverse()) {
    const card = el("details", "product-work-item"); card.append(el("summary", "", `Verze ${release.number} · ${new Date(release.accepted * 1000).toLocaleString("cs-CZ")}`), el("p", "", release.summary));
    const files = el("ul"); files.append(...release.artifacts.map(a => el("li", "", `${a.path} · ${a.sha256.slice(0,12)}`))); card.append(files);
    card.append(productButton("Report převzaté verze", () => openProductMission(release.mission))); panel.append(card);
    if (release.version_id && release.verification_id && p.deployment_settings)
      card.append(productButton(`Nasadit místně verzi ${release.number}`, () => productAction(p.id, "deploy", {number:release.number}), true));
    if (release.version_id) card.append(productButton("Náhled obnovy této verze", async () => {
      const preview = await api("/api/products/action", {id:p.id, action:"restore_preview", number:release.number});
      const details = el("div");
      details.append(el("p", "", `Změní se ${preview.changed.length} souborů. Současný stav se před obnovou uloží.`));
      const paths = el("pre"); paths.textContent = preview.changed.join("\n"); details.append(paths);
      if (preview.shape_conflicts?.length) details.append(el("p", "mission-feedback",
        "Nejdřív ručně vyřeš záměnu souboru a složky a obnov náhled: " + preview.shape_conflicts.join(", ")));
      else details.append(productButton(`Obnovit verzi ${release.number}`, () => productAction(p.id, "restore", {number:release.number, revision:preview.revision}), true));
      card.append(details);
    }));
  }
  panel.append(el("p", "missions-note", "Nové verze ukládají obsah zdrojových souborů pro obnovu. Prostředí, závislosti, .env a provozní data mimo projekt nejsou součástí této obnovy. Převzetí ani obnova nepotvrzují nasazení."));
}
async function loadProducts(force = false) {
  if (productLoading || productMutating || !$("#products-dialog").open) return;
  productLoading = true;
  const project = state.project, epoch = productEpoch;
  try {
    const data = await api(`/api/products?project=${encodeURIComponent(project)}`);
    if (project !== state.project || epoch !== productEpoch || productMutating || !$("#products-dialog").open) return;
    $("#products-health").textContent = data.controller_error || "Produkt pokračuje dalšími verzemi. Naplánovanou práci řídí Studio na tomto počítači.";
    const signature = JSON.stringify(data.products);
    if (!force && (productDirty || signature === productSnapshot)) return;
    productSnapshot = signature;
    if (!data.products.some(p => p.id === productSelection)) productSelection = data.products[0]?.id || null;
    const list = $("#product-list"); list.replaceChildren();
    for (const p of data.products) {
      const button = productButton(`${p.title} · ${p.releases.length} verzí`, async () => {
        if (productDirty) throw new Error("Nejdřív ulož rozepsané změny produktu.");
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
    const old = $(id).value || $("#model-select").value;
    $(id).replaceChildren(...state.data.profiles.map(p => { const option = el("option", "", `${p.model} · ${profileLabel(p)}`); option.value = p.id; return option; }));
    $(id).value = old;
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
    toast("Produkt uložen. První verzi spustíš z jeho fronty.");
  });
  setInterval(() => loadProducts().catch(error => toast(error.message, true)), 3000);
}
