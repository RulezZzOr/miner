"""Hermetic defaults for apodex tests.

Production sessions intentionally persist under the user's home directory.
Tests must never read or write that state, regardless of who runs them.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _restore_process_environment():
    """Switch: TerminalSession and the native runtime write ``os.environ``
    directly. Restore it after each test so one test's workspace variables
    cannot change the behavior of the next one."""
    environ = os.environ
    saved = dict(environ)
    yield
    if dict(environ) != saved:
        environ.clear()
        environ.update(saved)


@pytest.fixture(autouse=True)
def _isolated_user_dirs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
