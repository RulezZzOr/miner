"Výslovně vyhrazený, omezený na misi, seznam SSH zařízení; žádné libovolné příkazy."
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
            raise ValueError("Neplatné ID cíle SSH")
        ipaddress.ip_address(t['host'])
        if not re.fullmatch(r'[a-z_][a-z0-9_-]{0,63}', t['user']):
            raise ValueError("Neplatný uživatel SSH")
        if type(t['port']) is not int or not 1 <= t['port'] <= 65535:
            raise ValueError("Neplatný port SSH")
        identity = Path(t['identity_file']).expanduser()
        if not identity.is_absolute():
            raise ValueError("Identita SSH musí mít absolutní cestu")
        result.append({k: t[k] for k in ('id', 'host', 'user', 'port')} | {'identity_file': str(identity)})
    return result


def ssh_argv(target, section):
    if section not in SECTIONS:
        raise ValueError("Nepodporovaná sekce seznamu")
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
                raise ValueError("Výstup SSH překročil limit seznamu")
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
            message = "Seznam SSH selhal"
            for marker in ("Přístup odepřen", "Ověření klíče hostitele selhalo", "Připojení odmítnuto", 'No route to host', "Čas pro připojení vypršel"):
                if marker.encode() in stderr:
                    message = marker
                    break
            raise RuntimeError(f'{message} (návratový kód {code}); ověřování přístupu ani kontrola klíče serveru nebyly vypnuty.')
        result = json.loads(stdout)
        if not isinstance(result, dict) or result.get('version') != 1 or result.get('section') != section or not isinstance(result.get('data'), dict):
            raise ValueError("Neplatná odpověď ze seznamu SSH")
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

    async def invoke(self, *, target, section):
        if target not in self.targets or section not in SECTIONS:
            raise ValueError("Použijte jeden nakonfigurovaný cíl a podporovanou sekci jen pro čtení")
        t = self.targets[target]
        # Validate the evidence directory before any remote action, including symlinks.
        folder = self.workspace / 'company' / 'ssh-evidence'
        if not folder.resolve().is_relative_to(self.workspace):
            raise ValueError("Adresář s důkazy uniká mimo pracovní prostor")
        folder.mkdir(parents=True, exist_ok=True)
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
            "Seznam SSH jen pro čtení nakonfigurovaných serverů pomocí existujících klíčů. Dostupné cíle: " + destinations +
            ". Vyberte systém (OS/hardware), služby (stavy služeb/názvy procesů/porty/kontejnery), "
            "projekty (cesty k projektům/routy nginx) nebo integrace (názvy závislostí a názvy proměnných integrace pouze). "
            "Žádný příkaz ani libovolný parametr souboru. Uloží časově označený JSON důkaz do složky company/ssh-evidence. "
            "Tento nástroj použijte pro inventarizaci serverů; obecný bash SSH a web_fetch nejsou SSH konektorem. "
            "Přítomnost konfigurace neprokazuje, že integrace funguje."),
            parameters={'type': 'object', 'properties': {'target': {'type': 'string', 'enum': list(self.targets)},
                'section': {'type': 'string', 'enum': list(SECTIONS)}},
                'required': ['target', 'section'], 'additionalProperties': False}, func=self.invoke)
