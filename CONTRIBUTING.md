# Contributing

This is an experimental self-hosted application. Please reproduce a problem in a temporary
project before reporting it. Include the version, platform, model backend and a redacted error;
never attach credentials, private company files or complete agent transcripts.

## Local checks

Install the application with `sh setup-studio`. From the repository root:

```sh
frontier/.venv/bin/python -m unittest discover -s studio/tests -v
node --test studio/tests/*.cjs
frontier/.venv/bin/python scripts/build_release.py
```

Node.js is required for frontend tests, not for the running Studio UI. For a fresh-install check:

```sh
frontier/.venv/bin/python scripts/smoke_release.py dist/switch-studio-0.4.0-alpha.1-macos.zip --suite
```

Use the Linux `.tar.gz` archive on Linux. These tests validate mechanisms and deterministic
workflows; live-model quality requires separate, bounded evaluation with recorded outcomes.

The optional GitHub Actions template is in `docs/ci/studio-platforms.example.yml`.
It is not installed as an active workflow in this initial publication. To enable it, a maintainer
with workflow-write permission can copy it into `.github/workflows/studio-platforms.yml`.
Upstream workflows under `frontier/.github/` are retained as upstream source and do not run here.

Keep upstream changes focused, preserve attribution, and document user-visible behavior and
validation in pull requests. Do not commit `agent.toml`, `.env`, `.switch-agent`, `.apodex`,
SSH target configuration, generated datasets or provider credentials.
