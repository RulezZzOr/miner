"""Explicitly provisioned, mission-scoped SSH inventory; no arbitrary commands."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import signal
import uuid
from datetime import datetime, timezone
from pathlib import Path

SECTIONS = ('system', 'services', 'projects', 'integrations')


def targets_for(config_dir, mission):
    path = Path(config_dir) / 'ssh-targets.json'
    if not path.exists() or not mission or mission.get('phase') != 'build':
        return []
    data = json.loads(path.read_text())
    result = []
    for t in data.get('targets', []):
        if mission['id'] not in t.get('missions', []):
            continue
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}', t.get('id', '')):
            raise ValueError('Invalid SSH target id')
        ipaddress.ip_address(t['host'])
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,63}', t['user']):
            raise ValueError('Invalid SSH user')
        if type(t['port']) is not int or not 1 <= t['port'] <= 65535:
            raise ValueError('Invalid SSH port')
        identity = Path(t['identity_file']).expanduser()
        if not identity.is_absolute():
            raise ValueError('SSH identity must have an absolute path')
        result.append({k: t[k] for k in ('id', 'host', 'user', 'port')} | {'identity_file': str(identity)})
    return result


def ssh_argv(target, section):
    if section not in SECTIONS:
        raise ValueError('Unsupported inventory section')
    return ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'IdentitiesOnly=yes', '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'PreferredAuthentications=publickey', '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ControlMaster=no', '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5',
            '-o', 'ServerAliveCountMax=2', '-i', target['identity_file'], '-p', str(target['port']),
            '-l', target['user'], target['host'], 'python3 -I -B - ' + section]


async def execute(target, section):
    script = Path(__file__).with_name('ssh_probe.py').read_bytes()
    process = await asyncio.create_subprocess_exec(*ssh_argv(target, section), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)

    async def read_bounded(stream):
        chunks, size = [], 0
        while chunk := await stream.read(8192):
            size += len(chunk)
            if size > 256000:
                raise ValueError('SSH output exceeded the inventory limit')
            chunks.append(chunk)
        return b''.join(chunks)

    async def exchange():
        async def send():
            try:
                process.stdin.write(script)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()
        _, stdout, stderr = await asyncio.gather(send(), read_bounded(process.stdout), read_bounded(process.stderr))
        code = await process.wait()
        if code:
            # Never send uncontrolled login banners or remote stderr to the model.
            message = 'SSH inventory failed'
            for marker in ('Permission denied', 'Host key verification failed', 'Connection refused', 'No route to host', 'Connection timed out'):
                if marker.encode() in stderr:
                    message = marker
                    break
            raise RuntimeError(f'{message} (exit {code}); no authentication or host-key checks were disabled.')
        result = json.loads(stdout)
        if not isinstance(result, dict) or result.get('version') != 1 or result.get('section') != section or not isinstance(result.get('data'), dict):
            raise ValueError('Invalid SSH inventory response')
        return result

    try:
        return await asyncio.wait_for(exchange(), timeout=40)
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


class SSHInventory:
    def __init__(self, request):
        self.targets = {t['id']: t for t in request.get('ssh_targets', [])}
        self.workspace = Path(request['cwd']).resolve()
        self.broker = request.get('inventory_broker')

    async def invoke(self, *, target, section):
        if target not in self.targets or section not in SECTIONS:
            raise ValueError('Use one configured target and a supported read-only section')
        t = self.targets[target]
        # Validate the evidence directory before any remote action, including symlinks.
        folder = self.workspace / 'company' / 'ssh-evidence'
        if not folder.resolve().is_relative_to(self.workspace):
            raise ValueError('Evidence directory escapes the workspace')
        folder.mkdir(parents=True, exist_ok=True)
        if self.broker:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(self.broker, limit=300000), 5)
            try:
                writer.write(json.dumps({'target':target,'section':section}).encode()+b'\n')
                await writer.drain()
                reply = json.loads(await asyncio.wait_for(reader.readline(), 45))
                if not reply.get('ok'): raise RuntimeError('Scoped SSH inventory failed')
                result = reply['result']
            finally:
                writer.close(); await writer.wait_closed()
        else:
            result = await execute(t, section)
        result.update(target=target, host=t['host'], port=t['port'], user=t['user'],
                      observed_at=datetime.now(timezone.utc).isoformat(), mode='read-only')
        path = folder / f'{target}-{section}-{uuid.uuid4().hex[:12]}.json'
        with path.open('x') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        return json.dumps({'ok': True, 'evidence_file': str(path.relative_to(self.workspace)), **result}, ensure_ascii=False)

    def tool(self):
        from frontier_agent.core.tool import Tool
        destinations = ', '.join(f"{t['id']} = {t['user']}@{t['host']}:{t['port']}" for t in self.targets.values())
        return Tool(name='ssh_inventory', description=(
            'Read-only SSH inventory of configured servers using existing keys. Available targets: ' + destinations +
            '. Select system (OS/hardware), services (service states/process names/ports/containers), '
            'projects (project paths/nginx routes), or integrations (dependency names and integration variable NAMES only). '
            'No command or arbitrary file parameter. Saves timestamped JSON evidence in company/ssh-evidence. '
            'Use this tool for the server inventory; generic bash SSH and web_fetch are not the SSH connector. '
            'Configuration presence does not prove an integration works.'),
            parameters={'type': 'object', 'properties': {'target': {'type': 'string', 'enum': list(self.targets)},
                'section': {'type': 'string', 'enum': list(SECTIONS)}},
                'required': ['target', 'section'], 'additionalProperties': False}, func=self.invoke)


class InventoryBroker:
    """Private per-run Unix capability. Keys and SSH subprocesses stay in the controller."""
    def __init__(self, targets, path):
        import socketserver
        import threading
        self.path = Path(path).resolve()
        self.targets = {t['id']:t for t in targets}
        owner = self
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(45)
                try:
                    raw = self.rfile.readline(2049)
                    if len(raw)>2048: raise ValueError('Request too large')
                    data = json.loads(raw)
                    if set(data)!={'target','section'} or data['target'] not in owner.targets or data['section'] not in SECTIONS:
                        raise ValueError('Inventory capability denied')
                    result = asyncio.run(execute(owner.targets[data['target']], data['section']))
                    payload = {'ok':True,'result':result}
                except Exception:
                    payload = {'ok':False,'error':'Scoped SSH inventory failed or request denied'}
                self.wfile.write(json.dumps(payload).encode()+b'\n')
        self.server = socketserver.UnixStreamServer(str(self.path), Handler)
        os.chmod(self.path, 0o600)
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.1},daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(46)
        self.path.unlink(missing_ok=True)
