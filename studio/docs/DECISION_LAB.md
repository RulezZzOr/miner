# Bounded review and decision experiments

Version 0.4.0-alpha.6 adds original implementations of patterns investigated in the Jev ecosystem. No code from those example repositories is included. The optional TypeSafe transport follows its [HTTP API](https://docs.typesafe.ai/api); the normal local model path remains available.

## Review contract

The controller takes a content-addressed snapshot before each review. It prepares at most 24,000 UTF-8 bytes of evidence, including exact criteria and constraints, short worker claims marked as claims, snapshot identity, explicit truncation and up to 8,000 bytes of source excerpts. Exact file hashes stay in controller metadata and validate reads and acceptance; opaque hashes and duplicate worker checks are omitted from the model prompt. Required criteria and constraints are never silently cut to fit. Oversize required context stops with a concrete request to split the task.

The reviewer has only `read_review_evidence` and `save_mission_report`. Extra discovery is limited to two attempts of 3,000 source bytes each, including failed path requests. Only paths from the snapshot index are accepted. General `PROJECT.md` injection, native file browsing, web browsing and shell execution are unavailable in this phase. The complete prompt and packet are retained in the run request. Review has at most six turns and fifteen minutes, respecting shorter owner limits. A timeout blocks identical automatic retries.

Each criterion has `outcome` (`supported`, `contradicted`, `insufficient_evidence`), `issue` (`none`, `architecture`, `functionality`, `missing_evidence`), `passed`, `needs_owner`, and evidence text. Missing evidence, an unresolved defect or an owner decision cannot pass. A changed source invalidates acceptance. Old reports remain readable; newly launched bounded reviews require typed outcomes. Independent functional checks run before final functional review where configured. A model verdict does not replace those checks.

Review tools remain constrained by phase. Linux workers now additionally require bubblewrap isolation; network access and the host kernel remain shared. See ../../SECURITY.md.

## Optional task selection

Configure a provider and mode in a new project/product or in a stopped project's model settings:

| Mode | Behavior |
| --- | --- |
| Off | No decision request; keep deterministic plan order. |
| Shadow | Record the model's proposal and keep plan order immediately. |
| Select | Wait briefly for a valid choice among currently eligible tasks. Errors use plan order. |

New GUI selections start in Shadow. Existing explicitly enabled selectors retain Select behavior. Choosing no provider disables the pilot. Local chat confidence is self-reported, not a calibrated probability.

A signature binds full task state, owner answers, criteria, constraints, attempt IDs, profile configuration and policy to the compact decision frame. Stale, late, malformed and low-confidence replies cannot change execution. There is one request in flight, no automatic inference retries and one consumption of a selected decision. The model cannot approve a plan, override a pause, create a tool action, accept a product or bypass dependencies.

TypeSafe is optional and disabled until selected. It sends compact goal/task summaries to `https://api.typesafe.ai/v1/systemone` and reads `TYPESAFE_API_KEY` from the server environment or existing environment files. No key is entered into the browser or included in exports. The adapter supports the documented choice, score and noul shapes. It has fixture transport coverage; live TypeSafe quality and latency require credentials and a separate benchmark.

## Decision lab

Open **Decision lab**, select a provider, and paste a small evidence sample:

- **Product idea:** five original evidence axes (problem, demand, feasibility, differentiation, revenue), combined by a versioned deterministic rule. Missing evidence routes to validation. This is neither a revenue forecast nor launch authorization.
- **Document:** document purpose, support for a stated claim and missing evidence. Filename classification in review packets is only a routing hint; this lab performs the optional semantic experiment.
- **CRM request:** proposed department, stated urgency and whether an owner decision is needed. No CRM integration or outbound messages are executed.

The lab evaluates all questions in one request, validates every result, and saves the exact input, hashes, model identity, rubric, timing and reported usage. Empty text/scans require text extraction first; the lab does not pretend an empty scan is a blank document. Input is limited to 12,000 UTF-8 bytes without silent truncation. Lab experiments wait until project inference is idle, and one evaluation can run at a time; the result deadline is 120 seconds. A timeout or restart produces unknown, never a manufactured positive score. Polling does not alter the input form. **Save Markdown report to project** writes a new `analysis/decision-lab/<id>.md` file and refuses to overwrite an existing file.

Data stays in the existing Studio SQLite store and project Markdown files. No PostgreSQL extension, vector database or new service is required. The lab evaluates only the supplied text; it does not automatically inspect a private CRM or production server.

## Evidence and limits

Synthetic fixtures establish adapter, lifecycle and guard behavior, not model intelligence. A real AMD/Intel smoke review is recorded separately from fixture results. The CSS office is still a browser-rendered visualization; ComfyUI generation and a mesh renderer are separate work. No new 3D weights are bundled or downloaded by this release.

## Local browser pilot

Decision lab includes a small **Browser pilot** fixture. It enumerates permitted operation/target pairs from observed DOM state, supports an optional model proposal, checks a state fingerprint and a 15-second one-use lease before execution, and independently checks the visible result before accepting Finish. A page edit, reset, dialog close or expired reply invalidates the action. There is one request in flight and a five-second proposal budget with a visible deterministic fallback.

The fixture is deliberately limited to its own search input, Filter button and known result list. It is not a general browser connector and cannot operate other tabs, credentials, frames, uploads or a live CRM. Its value is a verified execution boundary for a later connector, not a claim of unrestricted browser automation.

## Evaluation commands

`python scripts/evaluate_decision_contracts.py --output contracts.json` runs 79 deterministic adversarial cases without calling a model. This does not measure model intelligence.

`python scripts/evaluate_judges.py --dataset synthetic-cases.json` exports 50 synthetic evidence cases (ten patterns with five context variants, not a representative production sample) without inference. Add `--run --config agent.toml --profile PROFILE --output results.json` to evaluate that dataset with an explicitly chosen provider. Reports separate missing/invalid answers, coverage, correctness, false accepts and fallback, and retain timing and reported tokens. Outputs contain no project or company evidence. Use separate result files when comparing models. No monetary cost is inferred.
