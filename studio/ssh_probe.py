"Pevně daný skript jen pro čtení odeslaný výslovně nakonfigurovanému SSH hostu.\n\nŽádné příkazy, obsahy souborů, argumenty procesů ani hodnoty prostředí dodané volajícím\nnejsou vraceny. Tento modul běží také na vzdáleném hostu pomocí standardní knihovny Pythonu.\n"
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

SKIP = {'.ssh', '.git', '.cache', '.local', '.venv', 'venv', 'node_modules', 'vendor',
        '__pycache__', '.npm', '.pnpm-store', '.next', 'dist', 'build', 'backups'}
ROOTS = [str(Path.home()), '/var/www', '/opt', '/srv']


def command(argv):
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=7, env={**os.environ, 'LC_ALL': 'C', 'SYSTEMD_PAGER': ''})
        return {'argv': argv, 'exit_code': r.returncode, 'output': r.stdout[:18000],
                'error': "Příkaz selhal nebo přístup odepřen" if r.returncode else None}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'argv': argv, 'exit_code': None, 'output': '', 'error': type(exc).__name__}


def small(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256000:
        return ''
    return path.read_text(errors='replace')


def project_files():
    files, scanned = [], 0
    for root in ROOTS:
        if not Path(root).is_dir():
            continue
        for folder, dirs, names in os.walk(root, followlinks=False):
            scanned += 1
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith('.')
                             and not Path(folder, d).is_symlink())[:60]
            if len(Path(folder).relative_to(root).parts) >= 4:
                dirs[:] = []
            for name in sorted(names):
                if name in {'package.json', 'pyproject.toml', 'requirements.txt', 'composer.json',
                            'docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml',
                            'Cargo.toml', 'go.mod', '.env', '.env.production', '.env.local'}:
                    files.append(Path(folder, name))
            if scanned >= 1600 or len(files) >= 300:
                return files[:300], True
    return files, False


def integration_names(path):
    "Vraťte pouze názvy závislostí/klíčů, nikoli konfigurační hodnoty."
    raw = small(path)
    if path.name.startswith('.env'):
        return {'variable_names': sorted(set(m.group(1) for m in re.finditer(
            r'^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,80})\s*=', raw, re.M)
            if re.search("VAPI|BUFFER|CRM", m.group(1))))}
    if path.name == 'package.json':
        data = json.loads(raw)
        names = sorted(set(data.get('dependencies', {})) | set(data.get('devDependencies', {})))
        return {'dependency_names': [n for n in names if re.fullmatch(r'[@a-zA-Z0-9/_.-]{1,100}', n)],
                'integration_hints': [n for n in names if re.search(r'vapi|buffer|crm|hubspot|salesforce', n, re.I)]}
    # Other manifests: identify relevant product names, without exposing lines.
    return {'integration_hints': sorted(set(re.findall(r'\b(?:vapi|buffer|hubspot|salesforce|twenty|espocrm|suitecrm)\b', raw.lower())))}


def nginx_routes():
    rows = []
    base = Path('/etc/nginx')
    for sub in ('sites-enabled', 'conf.d'):
        for p in sorted((base / sub).glob('*'))[:80]:
            try:
                resolved = p.resolve()
                if base not in resolved.parents:
                    continue
                raw = small(resolved)
                routes = []
                for match in re.finditer(r'^\s*(server_name|root|proxy_pass)\s+([^;\n]+);', raw, re.M):
                    name, value = match.groups()
                    if name == 'proxy_pass':
                        u = urlsplit(value)
                        value = f'{u.scheme}://{u.hostname or "dynamic"}' + (f':{u.port}' if u.port else '')
                    if len(value) < 300:
                        routes.append({name: value})
                rows.append({'source': str(p), 'routes': routes})
            except (OSError, ValueError):
                rows.append({'source': str(p), 'error': "Nelze prozkoumat"})
    return rows


def collect(section):
    if section == 'system':
        os_info = {}
        try:
            for line in Path('/etc/os-release').read_text().splitlines():
                key, _, value = line.partition('=')
                if key in {'NAME', 'VERSION', 'ID', 'VERSION_ID'}:
                    os_info[key] = value.strip('"')
        except OSError:
            pass
        memory = re.search(r'^MemTotal:\s+(\d+) kB', Path('/proc/meminfo').read_text(), re.M)
        disk = shutil.disk_usage('/')
        return {'hostname': socket.gethostname(), 'user_id': os.getuid(), 'kernel': platform.release(),
                'architecture': platform.machine(), 'os': os_info, 'cpu_count': os.cpu_count(),
                'memory_kib': int(memory.group(1)) if memory else None,
                'disk_root_bytes': {'total': disk.total, 'used': disk.used, 'free': disk.free},
                'sources': ['/etc/os-release', '/proc/meminfo', 'uname', 'statvfs /']}
    if section == 'services':
        services = command(['systemctl', 'list-units', '--type=service', '--all', '--no-pager', '--no-legend', '--plain'])
        services['units'] = [dict(zip(['name', 'load', 'active', 'sub'], line.split()[:4]))
                             for line in services.pop('output').splitlines()[:160] if len(line.split()) >= 4]
        return {'services': services, 'process_names': command(['ps', '-eo', 'pid,user,comm']),
                'listening_sockets': command(['ss', '-lntu']),
                'containers': command(['docker', 'ps', '--format', '{{.Names}}\t{{.Image}}\t{{.Status}}'])}
    if section in {'projects', 'integrations'}:
        paths, truncated = project_files()
        rows = []
        for p in paths:
            row = {'path': str(p)}
            if section == 'integrations':
                try:
                    row.update(integration_names(p))
                except (OSError, ValueError, TypeError):
                    row['error'] = "Nelze prozkoumat manifest"
            rows.append(row)
        return {'roots': ROOTS, 'max_depth': 4, 'truncated': truncated, 'files': rows,
                'nginx_routes': nginx_routes() if section == 'projects' else [],
                'note': "Přítomnost souboru, názvu klíče nebo závislosti není důkazem funkční integrace."}
    raise ValueError("Nepodporovaná sekce seznamu")


if __name__ == '__main__':
    print(json.dumps({'version': 1, 'section': sys.argv[1], 'data': collect(sys.argv[1])}, ensure_ascii=False))
