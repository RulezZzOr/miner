# Switch fork: provenance and local changes

This is a complete local fork of the public [ApodexAI/FrontierAgent](https://github.com/ApodexAI/FrontierAgent) repository, based on commit `9e533db6f6c34d16037ee5ec964c479d0eb51cde` (upstream HEAD verified on 2026-09-22). The public Miner repository vendors the source without the original Git database. Development originally used a local `switch/local` branch. Miner publication: https://github.com/RulezZzOr/miner.

Original source, tests, documentation, assets and Apache-2.0 [LICENSE](LICENSE) are retained. Upstream product names are retained for attribution and compatibility. This is not an independent reimplementation or a claim of authorship of the upstream work.

Switch modifications:

- `apodex/switch_cli.py`: launcher selecting models from the parent project's `agent.toml`, creating model-specific workflow YAML while preserving upstream tools, team behavior and UI. For Ollama, the launcher defaults to one in-flight model call and 600-second first-token and inter-chunk allowances, overridable with the upstream environment variables. Generated YAML contains credential placeholders; credentials are resolved only in the process environment.
- `apodex/profiles/react.yaml` and `agent_team.yaml`: opt-in external workflow-profile paths; original `tui` defaults remain unchanged for ordinary upstream launches.
- `frontier_agent/infra/openai_client.py`: explicit Ollama dialect for output limits (`max_tokens`) and outgoing reasoning history (`reasoning`), including streaming. Default OpenAI behavior is preserved.
- `workflows/stateful_react_agent/profile.py` and `workflows/agent_team/profile.py`: pass the selected wire dialect to all workflow client legs.
- `apodex/cli.py`: forward explicit `--max-turns` to the native workflow environment, which previously read only its YAML limit.
- `tests/test_switch_integration.py`: SDK wire-shape, profile-capability preservation and credential persistence checks.
- `pyproject.toml`: adds `switch-frontier` alongside the unchanged upstream CLI entry points.

The parent `./switch` launcher uses this checkout's isolated Python environment. The previous minimal Switch Agent remains available separately; its state format is not migrated into FrontierAgent sessions.

Operational equivalence depends on model capabilities, service credentials, sandbox runtime and optional benchmark data. The source fork preserves the public feature set, including the upstream limitations documented in the parent analysis. Private Apodex service components, private evaluators and model weights are not reproduced.

## Switch Studio (2026-09-22)

The local web IDE is in the adjacent folder `../studio/` and uses the full backend via a separate process. The launcher scripts `../switch-studio` and `../Switch Studio.command` open the editor, model configuration, task brief, acceptance, and outputs. The original terminal interface remains available.

During integration verification, the completion classification in `apodex/task_runner.py` was fixed: Stateful ReAct uses `no_tool_behavior="stop"`, so its natural `no_tool` termination may be marked as completed. Agent Team continues to preserve the incomplete state for `no_tool`; limits and explicitly incomplete responses do not count as success, except under this exception.

## Miner public source snapshot (2026-09-23)

This snapshot preserves all 745 files tracked at the upstream base, plus local additions.
The original compact Switch Agent prototype is not part of this application repository.
Studio and its launcher/scripts are in the parent directory. Additional integration changes
cover file output handling, native workspace behavior, cancellation/checkpoint recovery,
provider adapters, direct web fetch behavior and structured mission reports. Studio owns
the company/project/product controllers and explicitly scoped SSH inventory connector.
Private runtime state, local configurations and development session logs are excluded.
