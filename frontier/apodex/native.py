"""Workspace-local mutable state for host-native execution.

Native mode is the default for Linux host installations and the convenience
fallback for macOS machines without a running Docker daemon. It is not an
operating-system security boundary.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import MutableMapping
from pathlib import Path


def prepare_native_runtime(
    workspace: str,
    session_id: str,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    """Create and export the workspace-local native runtime directories."""
    env = os.environ if environ is None else environ
    workspace_path = Path(workspace).expanduser().resolve()
    original_home = Path(env.get("HOME") or Path.home()).expanduser().resolve()
    root = workspace_path / ".apodex" / "runtime" / "native"
    runs = workspace_path / ".apodex" / "runs"
    home = root / "home"
    cache = root / "cache"
    state = root / "state"
    config = root / "config"
    tmp = root / "tmp" / session_id
    inputs = root / "inputs" / session_id
    run_workspace = runs / session_id / "workspace"
    # An invocation owns its alias even when another process resumes the same
    # session. /new and /resume may retarget this alias without moving peers.
    workspace_link = root / "workspaces" / uuid.uuid4().hex / "workspace"
    outputs = runs / session_id / "outputs"
    dependencies = root / "dependencies"
    python_overlay = home / ".local" / "site-packages"

    for path in (
        home, cache, state, config, tmp, inputs, run_workspace, outputs, runs,
        dependencies,
        python_overlay, workspace_link.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)

    workspace_link.symlink_to(run_workspace, target_is_directory=True)

    inherited_pythonpath = env.get("PYTHONPATH", "").strip()
    pythonpath = str(python_overlay)
    if inherited_pythonpath:
        pythonpath = f"{pythonpath}{os.pathsep}{inherited_pythonpath}"
    inherited_path = env.get("PATH", "").strip()
    native_bins = [
        home / ".local" / "bin",
        dependencies / "npm" / "bin",
        dependencies / "pnpm",
        dependencies / "go" / "bin",
        dependencies / "ruby" / "bin",
        dependencies / "cargo" / "bin",
    ]
    native_path = os.pathsep.join(str(path) for path in native_bins)
    if inherited_path:
        native_path = f"{native_path}{os.pathsep}{inherited_path}"

    # ``pinned`` is reserved for container mount mappings. Native runs must be
    # able to follow the cwd stored in a checkpoint during an in-app resume.
    env.pop("APODEX_RUNS_ROOT_PINNED", None)
    env.update({
        "APODEX_IN_NATIVE": "1",
        "APODEX_NATIVE_ROOT": str(root),
        "APODEX_SESSION_ID": session_id,
        "APODEX_RUNS_ROOT": str(runs),
        "APODEX_HOST_RUNS_ROOT": str(runs),
        "APODEX_LEGACY_SESSION_ROOTS": os.pathsep.join((
            str(workspace_path / ".apodex" / "native" / "home" / ".apodex" / "sessions"),
            str(original_home / ".apodex" / "sessions"),
        )),
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "XDG_CACHE_HOME": str(cache),
        "XDG_CONFIG_HOME": str(config),
        "XDG_STATE_HOME": str(state),
        "UV_CACHE_DIR": str(cache / "uv"),
        "PIP_CACHE_DIR": str(cache / "pip"),
        "PIP_TARGET": str(python_overlay),
        "PYTHONPATH": pythonpath,
        "PATH": native_path,
        "NPM_CONFIG_CACHE": str(cache / "npm"),
        "NPM_CONFIG_PREFIX": str(dependencies / "npm"),
        "YARN_CACHE_FOLDER": str(cache / "yarn"),
        "PNPM_HOME": str(dependencies / "pnpm"),
        "CARGO_HOME": str(dependencies / "cargo"),
        "RUSTUP_HOME": str(dependencies / "rustup"),
        "GOPATH": str(dependencies / "go"),
        "GOBIN": str(dependencies / "go" / "bin"),
        "BUNDLE_PATH": str(dependencies / "ruby"),
        "GEM_HOME": str(dependencies / "ruby"),
        "SANDBOX_BACKEND": "native",
        "FRONTIER_AGENT_WORKSPACE_DIR": str(workspace_link),
        "APODEX_SESSION_WORKSPACES_ROOT": str(runs),
        "APODEX_WORKSPACE_LINK": str(workspace_link),
        "APODEX_HOST_WORKSPACE_ROOT": str(runs),
        "APODEX_HOST_WORKSPACE_DIR": str(run_workspace),
        "FRONTIER_AGENT_OUTPUTS_DIR": str(outputs),
        "APODEX_SESSION_OUTPUTS_ROOT": str(runs),
        "FRONTIER_AGENT_INPUTS_DIR": str(inputs),
        "APODEX_INPUT_STAGING_DIR": str(inputs),
        "APODEX_HOST_OUTPUTS_DIR": str(outputs),
        "APODEX_HOST_OUTPUTS_ROOT": str(runs),
        "APODEX_HOST_INPUTS_DIR": str(inputs),
    })
    return root


__all__ = ["prepare_native_runtime"]
