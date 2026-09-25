"""Owner authentication; bootstrap key never leaves the private runtime directory."""
import hashlib
import hmac
import json
import os
import secrets
import stat
import threading
import time
from http.cookies import SimpleCookie, CookieError
from pathlib import Path


def private_file(path, data):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def secure_runtime(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Runtime directory must not be a symlink')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_uid != os.getuid():
        raise ValueError('Runtime directory must belong to the current user')
    root.chmod(0o700)
    # Do not rewrite product/workspace permissions. The private parent protects old data.


class Access:
    def __init__(self, root):
        secure_runtime(root)
        path = Path(root) / 'access-key'
        try:
            private_file(path, secrets.token_urlsafe(32) + '\n')
        except FileExistsError:
            pass
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('Invalid owner access key file')
            os.fchmod(stream.fileno(), 0o600)
            key = stream.read(256).strip()
        if len(key) < 40:
            raise ValueError('Owner access key is too short')
        self.digest = hashlib.sha256(key.encode()).digest()
        self.sessions, self.attempts = {}, {}
        self.lock = threading.Lock()

    def valid_key(self, key):
        return isinstance(key, str) and len(key) <= 256 and hmac.compare_digest(self.digest, hashlib.sha256(key.encode()).digest())

    def authenticated(self, headers):
        bearer = headers.get('Authorization', '')
        if bearer.startswith('Bearer ') and self.valid_key(bearer[7:]):
            return True
        try:
            cookie = SimpleCookie(); cookie.load(headers.get('Cookie', ''))
            session = cookie.get('miner_session')
            with self.lock:
                return bool(session and self.sessions.get(session.value, 0) > time.time())
        except CookieError:
            return False

    def login(self, key, address):
        now = time.time()
        with self.lock:
            self.attempts = {k:v for k,v in self.attempts.items() if v[0] > now-60}
            since, count = self.attempts.get(address, (now, 0))
            if count >= 5 or len(self.attempts) >= 4096:
                return None, 429
            if not self.valid_key(key):
                self.attempts[address] = (since, count+1)
                return None, 401
            self.attempts.pop(address, None)
            self.sessions = {k:v for k,v in self.sessions.items() if v > now}
            if len(self.sessions) >= 128:
                return None, 429
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + 43200
            return token, 200
