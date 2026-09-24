# Contributing

This is an experimental self-hosted application. Please reproduce a problem in a temporary
project before reporting it. Include the version, platform, model backend and a redacted error;
never attach credentials, private company files or complete agent transcripts.

## Local checks

Install the application with `sh setup-studio`. From the repository root:

```sh
frontier/.venv/bin/python scripts/ci_check.py
```

Node.js is required for frontend tests, not for the running Studio UI. For a fresh-install check:

```sh
frontier/.venv/bin/python scripts/smoke_release.py dist/switch-studio-0.4.0-alpha.4-macos.zip --suite
```

Use the Linux `.tar.gz` archive on Linux. These tests validate mechanisms and deterministic
workflows; live-model quality requires separate, bounded evaluation with recorded outcomes.

Verification runs locally or on the operator's own server. Despite its historical
filename, `scripts/ci_check.py` does not invoke GitHub Actions: it runs backend and
frontend tests, scans publication files and checks release manifests. Its logs and
commit-bound receipt are in `dist/ci-evidence/`. For publication, require a passed
receipt with a clean worktree at the published commit, plus the locked dependency
audit described in [the audit](docs/AUDIT-2026-09-24.md).
No hosted CI service or GitHub `workflow` permission is required. Upstream workflows
under `frontier/.github/` are retained as upstream source and do not run here.

Keep upstream changes focused, preserve attribution, and document user-visible behavior and
validation in pull requests. Do not commit `agent.toml`, `.env`, `.switch-agent`, `.apodex`,
SSH target configuration, generated datasets or provider credentials.
