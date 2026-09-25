"""Fail-closed Linux worker isolation. No host home, credentials or controller state."""
import os
import shutil
import sys
from pathlib import Path


def clean_environment(environ):
    allowed = {'PATH', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    return {k:v for k,v in environ.items() if k in allowed} | {'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1'}


def isolated_command(argv, workspace, *, writable=(), readonly=(), protected=(), environment=None):
    if sys.platform != 'linux' or not shutil.which('bwrap'):
        raise RuntimeError('Secure execution requires Linux with working bubblewrap. Native fallback is disabled.')
    root = Path(__file__).resolve().parents[1]
    workspace = Path(workspace).resolve(strict=True)
    # A project cannot be the user's home, OS root, application or controller directory.
    forbidden_roots = [Path.home().resolve(), root]
    if workspace.is_relative_to(root) or workspace == Path('/') or any(p == workspace or p.is_relative_to(workspace) for p in forbidden_roots):
        raise ValueError('Choose a dedicated project directory outside the application and home root')
    cmd = [shutil.which('bwrap'), '--die-with-parent', '--new-session', '--unshare-pid',
           '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL', '--proc', '/proc', '--dev', '/dev',
           '--tmpfs', '/tmp', '--tmpfs', '/run', '--dir', '/home/worker', '--clearenv',
           '--setenv', 'HOME', '/home/worker', '--setenv', 'TMPDIR', '/tmp',
           '--setenv', 'PATH', str(Path(sys.executable).parent)+':/usr/local/bin:/usr/bin:/bin',
           '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
           '--setenv', 'PYTHONUNBUFFERED', '1', '--setenv', 'PYTHONPATH', str(root)+os.pathsep+str(root/'frontier')]
    mounts = ['/usr', '/bin', '/lib', '/lib64', '/etc/ssl', '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/passwd', '/etc/group']
    for path in mounts:
        if Path(path).exists(): cmd += ['--ro-bind', path, path]
    prefixes = [root/'studio', *(root/'frontier'/name for name in ('apodex','frontier_agent','plugins','config','assets','workflows','benchmarks/public/core','benchmarks/public/families')), Path(sys.prefix), Path(sys.base_prefix)]
    executable = Path(sys.executable)
    if executable.is_symlink():
        target = Path(os.readlink(executable))
        if target.is_absolute() and target.parent.name == 'bin':
            prefixes.append(target.parent.parent)
    for path in dict.fromkeys(prefixes):
        if str(path) not in mounts: cmd += ['--ro-bind',str(path.resolve()),str(path)]
    cmd += ['--bind', str(workspace), str(workspace)]
    # A selected workspace can contain controller data: mask it before granting
    # the current attempt its narrow capability mounts.
    for item in protected:
        path = Path(item).resolve(strict=True)
        if workspace == path:
            raise ValueError('Controller state cannot be selected as a project')
        if path.is_relative_to(workspace):
            cmd += ['--tmpfs', str(path)]
    # Only controller-selected capabilities are mounted after the private-state masks.
    for path in writable:
        path = Path(path).resolve(strict=True)
        cmd += ['--bind',str(path),str(path)]
    for path in readonly:
        path = Path(path).resolve(strict=True)
        cmd += ['--ro-bind',str(path),str(path)]
    for key, value in (environment or {}).items():
        if key not in {'SWITCH_DATA_DIR', 'SWITCH_RELEASE_ID'} or not isinstance(value, str):
            raise ValueError('Unexpected execution environment')
        cmd += ['--setenv', key, value]
    return cmd + ['--chdir',str(workspace),'--',*map(str,argv)]
