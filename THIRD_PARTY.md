# Third-party attribution

## FrontierAgent

This repository includes a modified copy of [ApodexAI/FrontierAgent](https://github.com/ApodexAI/FrontierAgent)
under `frontier/`, based on commit `9e533db6f6c34d16037ee5ec964c479d0eb51cde`.
The original source, copyright notices, licenses, tests and documentation are retained.
See [frontier/LICENSE](frontier/LICENSE) and [modification notes](frontier/SWITCH.md).
Upstream project names are retained for attribution and API compatibility.

FrontierAgent supplies the agent runtime, ReAct and Agent Team workflows, tools, TUI and
evaluation framework. Switch Studio supplies the browser IDE and project/company/product
controllers and includes adaptations for model providers and workflow reporting.

## Dependencies and assets

Pinned Python dependencies are listed in `frontier/uv.lock`; their respective licenses apply.
Nested components, assets and benchmark packages retain their own notices and licenses,
including `frontier/benchmarks/frontierchallenge/LICENSE`.
Model weights and private benchmark datasets are not included. Model and provider names are
used only to describe compatibility. This project is not affiliated with or endorsed by Apodex.
