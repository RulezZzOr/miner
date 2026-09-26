"""Owner authentication; bootstrap key never leaves the private runtime directory."""
import hashlib
import hmac
import os
import secrets
import stat
import threading
import time
from http.cookies import SimpleCookie, CookieError
from pathlib import Path

SESSION_SECONDS = 43200
MAX_SESSIONS = 128


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

    @staticmethod
    def session_cookie(headers):
        try:
            cookie = SimpleCookie(); cookie.load(headers.get('Cookie', ''))
        except CookieError:
            return None
        session = cookie.get('miner_session')
        return session.value if session else None

    def authenticated(self, headers):
        bearer = headers.get('Authorization', '')
        if bearer.startswith('Bearer ') and self.valid_key(bearer[7:]):
            return True
        session = self.session_cookie(headers)
        with self.lock:
            return bool(session and self.sessions.get(session, 0) > time.time())

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
            # A full table evicts the oldest session: the owner key is never locked out.
            while len(self.sessions) >= MAX_SESSIONS:
                self.sessions.pop(min(self.sessions, key=self.sessions.get))
            token = secrets.token_urlsafe(32)
            self.sessions[token] = now + SESSION_SECONDS
            return token, 200

    def logout(self, headers, everywhere=False):
        """Revoke the caller's cookie session, or every session when asked explicitly."""
        session = self.session_cookie(headers)
        with self.lock:
            if everywhere:
                self.sessions.clear()
            elif session:
                self.sessions.pop(session, None)
