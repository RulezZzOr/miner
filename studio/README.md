# Switch Studio

**Company Builder & Driver:** companies, project portfolios, departments, dependencies, repeated tasks, and loops with limits. [Usage and boundaries](docs/COMPANY_DRIVER.md).

Local GUI built on top of the full FrontierAgent backend. Runs on `http://127.0.0.1:4317` and requires no frontend build, CDN, or cloud account.

## Launch

Installation for **macOS, Linux, and Windows via WSL2**: [INSTALL.md](../INSTALL.md).

In the project root directory:

```sh
./switch-studio
# or a specific working directory
./switch-studio --cwd /path/to/project
# without automatically opening the browser
./switch-studio --no-open --port 4317
```

On macOS, you can also double-click `Switch Studio.command`. The terminal window keeps the server running; Ctrl+C terminates the server and its active task. Closing the web tab alone does not interrupt the running task. First, use the **Stop** button if you want to terminate it.

Uses `frontier/.venv`; missing environment is created automatically on first launch via `setup-studio`.
Manual installation or update: `sh setup-studio` (requires `uv`). Installs the base runtime and document readers from the lockfile. `--all-extras` is intended for broader development environments.

## Optional ChatGPT and Claude Accounts

In the **Models → Accounts · optional** window, select a provider. Studio first verifies login. If missing, click **Login in browser**, then **Open login**, and complete the provider login. Upon return, the list of available models appears automatically. **Add selected model** creates a profile; the model for a task is selected in the right panel. The internal Ollama remains the default and requires no cloud account.

- **ChatGPT:** official [Codex App Server](https://learn.chatgpt.com/docs/app-server), sharing login with local Codex CLI. Codex `0.155.1` has been verified on this machine. The model runs via its own Codex agent environment, in single-agent mode. Task length is managed by Codex; the Stop button works. Studio explicitly sets sandbox `workspace-write` and policy `untrusted` when auto-approval is disabled. Command and file change requests are forwarded to the GUI. Unsupported interactions are rejected. This is not an OpenAI API call using a ChatGPT OAuth token, nor is it Frontier’s team mode.
- **Claude Console:** official [Anthropic CLI OAuth](https://platform.claude.com/docs/en/cli-sdks-libraries/cli/authentication), separate API billing in Console. This is not a Claude Pro/Max subscription login. Uses the original Studio agent environment, including ReAct, Agent Team, and compaction. Token refresh support is provided by Anthropic SDK `>=1.0.0`.

A cloud profile can be removed from Studio using the **Remove from Studio** button. This does not affect logins in other applications; a shared Codex account is not logged out. The task using this profile must first be stopped. Pending login attempts can be cancelled; they expire after five minutes.

Tokens are not stored in model profiles, tasks, or the GUI. Codex manages its own credentials. Claude has a separate profile `switch-studio` under `.switch-agent/studio/anthropic/`; CLI and SDK manage its credential files and token refresh. Global Claude Code login or shell environment remains unchanged. Login is triggered only by user action; without a cloud account, local operation continues.

Optional dependencies:

- `codex` must be on PATH; see [Codex CLI installation](https://developers.openai.com/codex/cli).
- `ant` must be on PATH or in `.switch-agent/tools/ant`; see [official Anthropic CLI](https://github.com/anthropics/anthropic-cli). On this Mac, `1.34.0` is locally installed for arm64 with SHA-256 verified against the official release. Nothing is installed into global configuration.

Login is local: open the authorization link in a browser on the same machine where Studio and the provider’s callback are running.

## Features

- **Verified delivery and recovery:** isolated working copy, actual approved test commands,
  decision history, content versions, controlled merging, and file rollback.
  Optional local service with HTTP monitoring and a queue of fixes.
  [Architecture and precise procedure](docs/CONTROL_LOOP.md).

- **Products:** persistent digital product card, queue of fixes and new features,
  subsequent executions with original criteria, history of accepted versions, and optional
  scheduled maintenance. Automatic continuation has its own limit and is disabled by default. [Usage and boundaries of product management](docs/PRODUCT_LIFECYCLE.md).

- **Project notes in Markdown:** if the selected project has `PROJECT.md` in its root, Studio adds its saved content to the new task brief. A short guide with links to decisions, insights, and sources is sufficient; files are edited in your existing editor. [Rules and usage](docs/MARKDOWN_NOTES.md).

- **Preview:** run a static HTML/CSS/JS website on a separate loopback port, click directly in Studio, mobile width 390 px, and auto-reload on saved changes. The **Create starter website** button saves a real `index.html` into a new folder. [Usage and boundaries of preview](docs/WEB_PREVIEW.md).
- **AI Projects:** experimental long-running task brief, persistent plan and questions, recovery after restart, and separate worker/reviewer. [Usage and verified boundaries](docs/LONG_RUNNING_PROJECTS.md).
- **Templates → AI Build Company:** clickable overview of 30 roles, saving your own copy into the project, and adding instructions to the task brief. [Template structure and usage](templates/README.md).
- Selection of existing project folders and file tree.
- UTF-8 file editor up to 2 MB, line numbering, Tab, Cmd/Ctrl+S, file creation, and reload from disk.
- Revision check on save: if the file was modified by an agent or another editor in the meantime, the server returns a conflict and does not overwrite changes.
- Selection and addition of model profiles, `/models` check for OpenAI-compatible API/Ollama.
- ReAct and Agent Team via the actual full backend, configurable limit for main agent steps.
- Live text, separate reasoning, tool calls, approval cards, activity, console, and output files.
- History of tasks created in Studio; view restored after page reload.
- Overview of main agent and requests for creating workers from real events.
- Stopping a monitored process tree including descendants in their own process groups. After 5 seconds, forced termination follows; the cancelled status appears only after cleanup.
- Waiting for approval is handled directly in the GUI. Auto-approval is disabled by default and can be enabled per task.

Switching project or model does not affect an already running task. The agent works with saved files. An open file is added to the task brief by its path; unsaved changes must be saved first. A new task brief creates a new session; the GUI currently does not restore context from an old session for further conversation.

## Saving and operation

- Original configuration: `agent.toml`.
- Model adjustments from GUI: `.switch-agent/studio/models.json`. Original TOML is not overwritten; overwrites apply only to Studio.
- Projects and history: `.switch-agent/studio/`.
- Events, approvals, and logs: `.switch-agent/studio/runs/<id>/`.
- Actual sessions and backend outputs: `<project>/.apodex/runs/<session-id>/`.
- API keys are read from environment or `.env` for original TOML / in `frontier/`; GUI stores only the variable name.

The server listens only on loopback. It checks Host, Origin, and token for modification requests. The editor does not allow paths outside the open project, symbolic links anywhere in the path, or `.env*` / `.git` / `.switch-agent`, regardless of case. Read and write operations use open directory descriptors and `O_NOFOLLOW`, so swapping a directory for a symlink between check and opening cannot bypass protection. These web API checks do not replace agent command isolation: **native backend runs with user permissions**, just like the terminal version.

Studio GUI currently runs one task at a time. Each native launch has its own stable working link under `.apodex/runtime/native/workspaces/<invocation>/workspace`; a separate CLI run does not overwrite it. Within team tasks, delegation remains functional. A successful process return alone does not mean a completed task: the GUI distinguishes confirmed completion, incomplete result, error, and cancellation.

## Scope of this version

This is a local web IDE with a simple text editor. It currently lacks LSP/autocomplete, debugger, manual interactive terminal, or multiple open editor tabs. Nested department hierarchies 1 → 5 → 25 and models for arbitrary individual roles are not yet implemented. AI Projects have separate model selection for work and review, and automatic fix return. Reliable complex delivery on a specific Qwen and week-long operation remain open verification.

## Tests

```sh
frontier/.venv/bin/python -m unittest discover -s studio/tests -v
frontier/.venv/bin/ruff check studio frontier/apodex/task_runner.py
node --check studio/static/app.js
node --test studio/tests/*.cjs
```

Integration tests run an actual Frontier process against a deterministic local test API. They verify approval → tool → file → completion, stopping, history, and HTTP/editing boundaries. These are not tests of intelligence or real model quality.

## Fixes for audit of 22 Sep 2026

All seven confirmed findings have been fixed and regression-verified in the [audit report](../analysis/audit/AUDIT.md). The helper summary uses the protocol and configuration of the selected workflow in both modes. Model check without authentication sends no key from the environment. Codex output list iterates through all event pages up to the log size recorded at the start of reading.

Native process management regularly tracks descendants and re-captures their identity before Stop. This is not OS-level isolation of an intentionally evading program; such limitation would require a container or other system sandbox. Working links are separate; project files and installation cache may still be shared across different runs.
