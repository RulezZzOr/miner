"""Workspace-local mutable state for host-native execution.

Native mode is the default for Linux host installations and the convenience
fallback for macOS machines without a running Docker daemon. It is not an
operating-system security boundary.
"""
from __future__ import annotations

import atexit
import contextlib
import os
import time
import uuid
from collections.abc import MutableMapping
from pathlib import Path

try:
    import fcntl
except ImportError:  # no advisory locks (Windows): aliases are then never pruned
    fcntl = None  # type: ignore[assignment]

_ALIAS_OWNER = "owner.pid"
# Descriptors of this process's owner files. Each one holds an exclusive
# ``flock`` for the life of the invocation; the kernel releases it when the
# process ends, however it ends.
_HELD_OWNERS: dict[Path, int] = {}


def _claim_alias(alias_dir: Path) -> None:
    """Record this process as the owner of *alias_dir* and keep its lock."""
    fd = os.open(alias_dir / _ALIAS_OWNER, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
    if fcntl is not None:
        # On a filesystem without flock the alias is then never pruned.
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.write(fd, str(os.getpid()).encode())  # diagnostics only, never trusted
    _HELD_OWNERS[alias_dir] = fd


def _owner_alive(alias_dir: Path) -> bool:
    """True while the invocation that created *alias_dir* may still run.

    Liveness is the owner's ``flock``, not its PID. Locks hold across PID
    namespaces, and PIDs do not: every sandboxed runner is PID 2 inside
    ``bwrap --unshare-pid``. A PID check would keep the aliases of killed
    attempts forever and would remove the alias of a live host session that a
    sandboxed run cannot see. When the lock cannot be tested the alias is kept.
    """
    try:
        fd = os.open(alias_dir / _ALIAS_OWNER, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        if fcntl is None:
            return True
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:  # BlockingIOError: the owner still holds its lock
        return True
    finally:
        os.close(fd)
    return False


def _remove_alias(alias_dir: Path) -> None:
    """Remove one per-invocation alias directory. Only the symlink and the
    owner file are deleted; a directory with any other content is kept."""
    link = alias_dir / "workspace"
    try:
        if link.is_symlink():
            link.unlink()
        elif link.is_dir() and not any(link.iterdir()):
            link.rmdir()
        (alias_dir / _ALIAS_OWNER).unlink(missing_ok=True)
        alias_dir.rmdir()
    except OSError:
        pass
    fd = _HELD_OWNERS.pop(alias_dir, None)
    if fd is not None:
        os.close(fd)


def _prune_stale_aliases(aliases: Path) -> None:
    """Remove aliases left by invocations that were killed before cleanup."""
    try:
        candidates = list(aliases.iterdir())
    except OSError:
        return
    for alias_dir in candidates:
        try:
            # A concurrent invocation may not have written its owner file yet.
            fresh = time.time() - alias_dir.lstat().st_mtime < 60
        except OSError:
            continue
        if not fresh and alias_dir.is_dir() and not alias_dir.is_symlink() and not _owner_alive(alias_dir):
            _remove_alias(alias_dir)


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
        python_overlay,
    ):
        path.mkdir(parents=True, exist_ok=True)

    # Every invocation adds an alias; remove those whose process is gone, and
    # this one when the process exits normally.
    _prune_stale_aliases(workspace_link.parent.parent)
    workspace_link.parent.mkdir(parents=True, exist_ok=True)
    _claim_alias(workspace_link.parent)
    workspace_link.symlink_to(run_workspace, target_is_directory=True)
    atexit.register(_remove_alias, workspace_link.parent)

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
