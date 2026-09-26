"""Shared gating for tests that need the Linux bubblewrap sandbox.

Secure worker, check and service execution is Linux + bubblewrap only (see SECURITY.md).
On other hosts these tests are skipped with an explicit reason instead of failing.
"""
import shutil
import subprocess
import sys
import unittest


def sandbox_available():
    """True when bubblewrap can create the namespaces Studio's isolation uses."""
    bwrap = shutil.which('bwrap')
    if sys.platform != 'linux' or not bwrap:
        return False
    probe = [bwrap, '--ro-bind', '/', '/', '--unshare-pid', '--unshare-ipc', '--unshare-uts',
             '--dev', '/dev', '--proc', '/proc', '--cap-drop', 'ALL', 'true']
    try:
        return subprocess.run(probe, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


SANDBOX = sandbox_available()
SANDBOX_REASON = 'requires Linux with working bubblewrap (secure worker execution)'
requires_sandbox = unittest.skipUnless(SANDBOX, SANDBOX_REASON)
