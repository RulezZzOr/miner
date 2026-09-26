"""Fail-closed Linux worker isolation. No host home, credentials or controller state."""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Read-only system files a worker shell needs. Debian/Ubuntu route awk, which, cc and
# similar commands through /etc/alternatives; the loader cache and time zone keep
# libraries and timestamps consistent with the host. Missing paths are skipped.
SYSTEM_MOUNTS = ('/usr', '/bin', '/sbin', '/lib', '/lib64', '/etc/alternatives', '/etc/ld.so.cache',
                 '/etc/ld.so.conf', '/etc/ld.so.conf.d', '/etc/localtime', '/etc/timezone', '/etc/ssl',
                 '/etc/pki', '/etc/ca-certificates', '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf',
                 '/etc/passwd', '/etc/group')
# Credential stores inside the home directory are never valid projects.
CREDENTIAL_DIRS = ('.ssh', '.gnupg', '.aws', '.azure', '.kube', '.docker', '.config/gcloud', '.password-store')


def clean_environment(environ):
    allowed = {'PATH', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    return {k:v for k,v in environ.items() if k in allowed} | {'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1'}


def managed_workspace(workspace, data):
    """Controller-created sandboxes: <data>/workspaces/<id> and <data>/deployments/<id>/release."""
    if data is None or workspace == data or not workspace.is_relative_to(data):
        return False
    parts = workspace.relative_to(data).parts
    return (len(parts) == 2 and parts[0] == 'workspaces' or
            len(parts) == 3 and parts[0] == 'deployments' and parts[2] == 'release')


def check_workspace(workspace, managed_root=None, protected=()):
    """Reject the OS root, home, application tree and controller state as a project.

    A workspace inside the application tree or inside controller state is accepted only
    when it is one of the controller's own managed sandboxes under ``managed_root``.
    """
    workspace = Path(workspace).resolve(strict=True)
    data = Path(managed_root).resolve(strict=True) if managed_root else None
    home = Path.home().resolve()
    if workspace == Path('/') or workspace == home or home.is_relative_to(workspace):
        raise ValueError('Choose a dedicated project directory outside the application and home root')
    if any(workspace.is_relative_to(home / name) for name in CREDENTIAL_DIRS):
        raise ValueError('Credential directories cannot be selected as a project')
    if workspace == ROOT or ROOT.is_relative_to(workspace):
        raise ValueError('Choose a dedicated project directory outside the application and home root')
    managed = managed_workspace(workspace, data)
    if workspace.is_relative_to(ROOT) and not managed:
        raise ValueError('Choose a dedicated project directory outside the application and home root')
    for item in ([data] if data else []) + [Path(p).resolve(strict=True) for p in protected]:
        if workspace == item or (item == data and item.is_relative_to(workspace)):
            raise ValueError('Controller state cannot be selected as a project')
        if workspace.is_relative_to(item) and not (item == data and managed):
            raise ValueError('A project cannot be located inside controller state')
    return workspace, data


def isolated_command(argv, workspace, *, writable=(), readonly=(), protected=(), environment=None, managed_root=None):
    """Build a bubblewrap command; ``managed_root`` is the controller data directory."""
    if sys.platform != 'linux' or not shutil.which('bwrap'):
        raise RuntimeError('Secure execution requires Linux with working bubblewrap. Native fallback is disabled.')
    root = ROOT
    workspace, data = check_workspace(workspace, managed_root, protected)
    controls = list(dict.fromkeys([*([data] if data else []), *(Path(p).resolve(strict=True) for p in protected)]))
    cmd = [shutil.which('bwrap'), '--die-with-parent', '--new-session', '--unshare-pid',
           '--unshare-ipc', '--unshare-uts', '--cap-drop', 'ALL', '--proc', '/proc', '--dev', '/dev',
           '--tmpfs', '/tmp', '--tmpfs', '/run', '--dir', '/home/worker', '--clearenv',
           '--setenv', 'HOME', '/home/worker', '--setenv', 'TMPDIR', '/tmp',
           '--setenv', 'PATH', str(Path(sys.executable).parent)+':/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
           '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
           '--setenv', 'PYTHONUNBUFFERED', '1', '--setenv', 'PYTHONPATH', str(root)+os.pathsep+str(root/'frontier')]
    for path in SYSTEM_MOUNTS:
        if Path(path).exists(): cmd += ['--ro-bind', path, path]
    # Controller state outside the workspace (including a parent of a managed sandbox) is
    # replaced by an empty tmpfs before anything below it is mounted, so only the explicit
    # capability mounts that follow can ever appear inside it.
    for path in controls:
        if not path.is_relative_to(workspace):
            cmd += ['--tmpfs', str(path)]
    prefixes = [root/'studio', *(root/'frontier'/name for name in ('apodex','frontier_agent','plugins','config','assets','workflows','benchmarks/public/core','benchmarks/public/families')), Path(sys.prefix), Path(sys.base_prefix)]
    executable = Path(sys.executable)
    if executable.is_symlink():
        target = Path(os.readlink(executable))
        if target.is_absolute() and target.parent.name == 'bin':
            prefixes.append(target.parent.parent)
    for path in dict.fromkeys(prefixes):
        if str(path) not in SYSTEM_MOUNTS: cmd += ['--ro-bind',str(path.resolve()),str(path)]
    cmd += ['--bind', str(workspace), str(workspace)]
    # A selected workspace can contain controller data: mask it before granting
    # the current attempt its narrow capability mounts.
    for path in controls:
        if path.is_relative_to(workspace):
            cmd += ['--tmpfs', str(path)]
    # Only controller-selected capabilities are mounted after the private-state masks.
    for flag, paths in (('--bind', writable), ('--ro-bind', readonly)):
        for path in paths:
            path = Path(path).resolve(strict=True)
            if any(item == path or item.is_relative_to(path) for item in controls):
                raise ValueError('Controller state cannot be mounted into a worker')
            cmd += [flag,str(path),str(path)]
    for key, value in (environment or {}).items():
        if key not in {'SWITCH_DATA_DIR', 'SWITCH_RELEASE_ID'} or not isinstance(value, str):
            raise ValueError('Unexpected execution environment')
        cmd += ['--setenv', key, value]
    return cmd + ['--chdir',str(workspace),'--',*map(str,argv)]
