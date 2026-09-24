"""Evidence cards and attributed acceptance notes, derived from controller records.

Notes never become a second task queue. They describe an accepted snapshot and
explicitly make no deployment or external-service assertion.
"""
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import quote

try:
    from .verification import source_manifest, source_modes
    from .test_evidence import git_revision
except ImportError:
    from verification import source_manifest, source_modes
    from test_evidence import git_revision


def evidence_card(controller, m):
    version = controller.versions.get(m["version_id"]) if m.get("version_id") else None
    record = controller.verifications.get(m["verification_id"]) if m.get("verification_id") else None
    root = controller.studio.project(m["project"]) if m["status"] == "accepted" else m["workspace"]
    baseline = version["files"] if version else (record or {}).get("sources")
    modes = version["modes"] if version else (record or {}).get("source_modes")
    current, freshness_error = None, None
    try:
        current = source_manifest(root)
        expected = dict(baseline or {})
        expected_modes = dict(modes or {})
        if m["status"] == "accepted":
            expected.update(m.get("notes_sync", {}).get("hashes", {}))
            expected_modes.update(m.get("notes_sync", {}).get("modes", {}))
        changed = sorted(p for p in current.keys() | expected.keys() if current.get(p) != expected.get(p)) if baseline is not None else []
        permissions_changed = bool(baseline is not None and source_modes(root, current) != expected_modes)
    except Exception as exc:
        changed, permissions_changed, freshness_error = [], False, str(exc)[:1500]
    # An isolated copy has no git metadata. Do not attribute its checks to the parent HEAD.
    commit = git_revision(root)
    commit_changed = bool(record and "git_revision" in record and record["git_revision"] is not None and commit != record["git_revision"])
    stale = bool(changed or permissions_changed or freshness_error or commit_changed)
    limitations = ["No deployment or external integration claim is implied by local acceptance."]
    if not record:
        limitations.append("No controller-owned command checks were recorded.")
    if not commit:
        limitations.append("No Git commit is bound to this workspace; evidence is bound to file hashes.")
    if stale:
        limitations.append("Current files or revision differ from the verified delivery. Re-verification is required for the current state.")
    if m.get("notes_sync", {}).get("status") == "complete":
        limitations.append("Provenance notes were updated after acceptance in a separate documentation version; they were not part of the product test run.")
    return {"mission": m["id"], "title": m["title"], "status": m["status"], "acceptance": m.get("acceptance"),
            "version": m.get("version_id"), "verification": (record or {}).get("id"),
            "verified_git_revision": (record or {}).get("git_revision"), "current_git_revision": commit,
            "manifest_sha256": hashlib.sha256(json.dumps(baseline, sort_keys=True).encode()).hexdigest() if baseline is not None else None,
            "freshness": "stale" if stale else "current" if baseline is not None else "not_verified",
            "changed_files": changed, "permissions_changed": permissions_changed, "commit_changed": commit_changed,
            "freshness_error": freshness_error,
            "artifacts": (m.get("final_report") or {}).get("verified_artifacts", []),
            "checks": [{k: c.get(k) for k in ("label", "argv", "exit_code", "error", "test_count", "started", "ended")}
                       for c in (record or {}).get("checks", [])],
            "check_status": (record or {}).get("status", "not_run"),
            "review": {"profile": m["review_profile"], "run": (m.get("final_report") or {}).get("run"),
                       "checks": (m.get("final_report") or {}).get("checks", [])},
            "notes": m.get("notes_sync"), "limitations": limitations}


def markdown_text(value):
    """Render supplied text literally in generated Markdown, including link labels."""
    value = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~-])", r"\\\1", value)


def note_updates(controller, m, base):
    marker = "<!-- miner-acceptance:" + m["id"] + " -->"
    at = datetime.fromtimestamp(m["acceptance"]["at"], timezone.utc).isoformat()
    title = markdown_text(json.dumps(m["title"], ensure_ascii=False))
    record = (f"\n\n{marker}\n### Accepted snapshot {m['id']}\n\n"
              f"- Recorded at: {at}\n- Title: {title}\n- Product version: `{m['version_id']}`\n"
              f"- Acceptance: {m['acceptance']['kind']}\n"
              f"- Independent check record: `{m.get('verification_id') or 'not recorded'}`\n"
              f"- Final review run: `{(m.get('final_report') or {}).get('run', 'not recorded')}`\n")
    report = m.get("final_report") or {}
    # A reviewed summary is still a model assessment. Preserve its attribution and
    # scope, and quote it as data rather than turning it into new instructions.
    summary = str(report.get("summary", "No review summary recorded.")).encode()[:2400].decode("utf-8", errors="ignore")
    summary = markdown_text(summary)
    description = "\n#### Accepted-change description\n\nReview assessment for this snapshot (not a live deployment claim):\n\n"
    description += "\n".join("> " + line for line in summary.splitlines()) + "\n"
    if len(str(report.get("summary", "")).encode()) > 2400:
        description += "\nSummary excerpt truncated; the complete assessment is in the recorded final review.\n"
    artifacts = report.get("verified_artifacts", [])
    references = "\n#### Output references\n\n"
    for item in artifacts[:24]:
        # Paths and fragments are URL encoded; supplied names cannot inject Markdown.
        label = markdown_text(item["path"].replace("\n", " ").replace("\r", " "))
        references += f"- [{label}](../{quote(item['path'], safe='/')}) — accepted SHA-256 `{item['sha256']}`\n"
    if len(artifacts) > 24:
        references += f"- {len(artifacts) - 24} additional outputs are listed in the result card.\n"
    if not artifacts:
        references += "No output references were recorded.\n"
    references += "\nLinks open the current files; the hashes above identify the accepted snapshot. Changed files require new verification.\n"
    values = {
        "notes/NOTES.md": record + description + references + "\nThis records local acceptance, not deployment or live integration health. Older entries are historical; compare the latest applicable snapshot with current source files. See the Studio result card for evidence freshness.\n",
        "notes/DECISIONS.md": record + "- Decision: accept this product snapshot. The original brief, revisions, owner decisions and pending work remain in Studio's database.\n",
        "notes/SOURCES.md": record + f"- Provenance: Studio mission `{m['id']}`, immutable output hashes and controller check logs. Model statements are not independent proof.\n" + references,
    }
    updates = {}
    for path, suffix in values.items():
        existing = controller.versions.read_object(base["files"][path]).decode("utf-8") if path in base["files"] else "# " + path.split("/")[-1][:-3].title() + "\n"
        if marker not in existing:
            updates[path] = existing.rstrip() + suffix
    project = controller.versions.read_object(base["files"]["PROJECT.md"]).decode("utf-8") if "PROJECT.md" in base["files"] else "# Project guide\n"
    index_marker = "<!-- miner-delivery-notes -->"
    if index_marker not in project:
        updates["PROJECT.md"] = project.rstrip() + ("\n\n" + index_marker + "\n## Delivery records\n\n"
            "Accepted snapshots: [Notes](notes/NOTES.md). Recorded acceptance decisions: [Decisions](notes/DECISIONS.md). "
            "Evidence references: [Sources](notes/SOURCES.md).\n\n"
            "Studio's database is the authority for task status and pending work. These files are reference notes, not another task queue. "
            "Historical acceptance does not prove the current deployment or external service state; consult the live authoritative host.\n")
    return updates


def sync_notes(controller, m):
    """Journal once, apply with existing conflict checks, and atomically link completion."""
    if m["status"] != "accepted" or m.get("notes_sync", {}).get("status") == "complete":
        return
    root = controller.studio.project(m["project"])
    scope = m.get("constraints", "")
    if re.search(r"\bwrite only\b|do not (?:edit|modify|change|write).{0,60}(?:PROJECT\.md|notes/|project files|any files)|bez změn (?:souborů|projektu)", scope, re.I):
        m["notes_sync"] = {"status": "restricted", "reason": "The explicit task constraints restrict documentation writes. Notes were not changed."}
        controller.save(m)
        return
    try:
        plan = m.get("notes_sync", {})
        if not plan.get("target"):
            base = controller.versions.snapshot(root, label="Before acceptance notes " + m["id"])
            updates = note_updates(controller, m, base)
            target = controller.versions.derive_notes(base, updates, "Acceptance notes " + m["id"])
            plan = {"status": "pending", "base": base["id"], "target": target["id"], "paths": sorted(updates)}
            m["notes_sync"] = plan
            controller.save(m)
        base = controller.versions.get(plan["base"])
        target = controller.versions.get(plan["target"])
        def commit(db, operation):
            m["notes_sync"] = {**plan, "status": "complete", "operation": operation["id"],
                               "hashes": {p: target["files"][p] for p in plan["paths"]},
                               "modes": {p: target["modes"][p] for p in plan["paths"]}}
            controller.save(m, db)
        controller.versions.apply(root, target, base=base, commit=commit)
    except Exception as exc:
        m["notes_sync"] = {**m.get("notes_sync", {}), "status": "needs_attention", "error": str(exc)[:1500]}
        controller.save(m)
