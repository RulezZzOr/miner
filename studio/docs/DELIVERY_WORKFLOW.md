# Delivery workflow

Managed AI Projects, company tasks and product executions use the same controller.
Dashboard standalone runs remain immediate single runs, without the managed
planning/review/acceptance lifecycle. Choose a company destination for reviewed work.

## 1. Check the brief before execution

Required fields, registered project and configured model profiles are validated at
creation. Brief readiness catches a conservative set of contradictory scopes, such
as a read-only audit whose success requires making every integration operational.
It does not assume that a remote service is reachable or that credentials work.

Complex or sensitive briefs require a short readiness assessment in the existing
bounded planning report. Necessary owner questions are grouped before execution.
Clear small briefs need no additional model call. This is a heuristic and model
assessment, not proof that every possible contradiction has been detected.

In the project detail, open **Brief readiness → Revise task brief** before a plan
exists. Give the corrected scope and a reason. The API checks the previous brief
hash and retains before/after wording. Pause the parent company before revising its
brief. For an existing plan, use the existing task-revision controls instead.

## 2. Inspect one result card

Open **Result card → Refresh result evidence** in the project detail. The card
combines artifact hashes, accepted product snapshot, controller command outcomes,
executed test counts, final review and explicit limitations. It compares the current
files and permissions to the recorded evidence. If checks ran in a Git root, a
changed HEAD also invalidates those checks, even when file content is identical.
An isolated workspace has file-hash evidence; it is not falsely attributed to the
parent repository's commit.

Known test commands (`unittest`, `pytest`, Node `--test`, common npm test commands)
must report at least one executed test. Skipped-only, zero-test and unrecognized
test summaries fail. API checks can explicitly specify `kind: "test"` and
`minimum_tests`. Ordinary non-test commands remain valid for document or build
checks; do not label an ordinary file assertion as a test suite.

`.github/workflows/verify.yml` runs the shipped backend and frontend suites,
publication credential checks, package manifest verification and a locked runtime
dependency audit. The downloadable receipt identifies the exact GitHub commit.
Repository branch protection must be configured separately if CI is to be required
before merge. This application does not enable automatic merging.

## 3. Resume using a compact handoff

Each reserved attempt stores a controller-generated handoff in SQLite: goal,
completed task IDs, pending tasks, blockers, the last successful controller command
and precise source hashes. Changed references are marked `changed_read_again`.
It is routing data, not a skip-list or substitute for reading relevant evidence.

The packet is at most 4.5 KB for Light and 8 KB otherwise. Omitted entries are
counted; current task criteria remain exact in the phase prompt. Restart reconciles
the existing attempt and task states before scheduling more work. The handoff is
not a claim that a particular model will finish faster.

## 4. Keep Markdown reference notes current

After acceptance, the controller appends a dated acceptance record to
`notes/NOTES.md`, `notes/DECISIONS.md` and `notes/SOURCES.md`, and adds their index to
`PROJECT.md` once. Existing text is preserved. The record identifies the snapshot,
check record, review run and manual or verified acceptance. It never invents a
deployment, external-service status, architectural decision or new task queue.
SQLite remains the task authority; deployed-state questions still require the
project's actual authoritative host.

Notes are a **separate documentation version after product verification**, clearly
labeled in the result card. A recoverable file journal and revision checks prevent
automatic overwrites of concurrent edits. A conflict leaves acceptance recorded
and notes marked `needs_attention`; it does not claim the notes were updated.
Explicit brief restrictions on documentation writes are respected and displayed.
Old accepted missions are not retroactively edited during upgrade.

## 5. Match process to impact

Use the single **Delivery process** selector in advanced task options, or leave Auto.

| Mode | Behavior |
| --- | --- |
| Light | Up to 10 minutes / 16 turns per implementation attempt, smaller note and inline review excerpts. Shorter owner limits still apply. |
| Standard | Existing owner budgets and bounded planning/review. |
| Sensitive | Retains the full process; requested Light is overridden for detected security, identity, data, payment or deployment scope and sensitive changed paths. |

All modes retain the configured review profile, architecture review, independent
owner-approved checks and final review. Modes do not grant tool permissions or
automatically approve actions. Classification is conservative and heuristic; select
Sensitive explicitly for risks not recognizable from the brief or file names.

API additions: `process_mode` on mission/company-task creation;
`revise_brief` with `expected_brief_hash`, corrected fields and `reason`;
`GET /api/delivery?id=...`; `sync_notes` to retry an interrupted/conflicted notes
operation without silently discarding newer edits. All mutations retain the normal
same-origin request-token checks.
