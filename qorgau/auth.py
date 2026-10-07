import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path


class Auth:
    def __init__(self, folder, reset=False, initial_pin=None):
        path = Path(folder) / "instructor.json"
        self.path = path
        self.sessions = {}
        self.attempts = []
        self.initial_pin = None
        if reset or not path.exists():
            pin = initial_pin or str(secrets.randbelow(900000) + 100000)
            salt = secrets.token_hex(16)
            digest = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
            path.write_text(json.dumps({"salt": salt, "hash": digest}), encoding="utf-8")
            try:
                path.chmod(0o600)
            except OSError:
                pass
            self.initial_pin = pin
        self.secret = json.loads(path.read_text(encoding="utf-8"))

    def login(self, pin):
        now = time.monotonic()
        self.attempts = [t for t in self.attempts if now - t < 60]
        if len(self.attempts) >= 5:
            raise ValueError("Слишком много попыток. Подождите минуту.")
        self.attempts.append(now)
        digest = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(self.secret["salt"]), n=16384, r=8, p=1).hex()
        if not hmac.compare_digest(digest, self.secret["hash"]):
            return None
        self.attempts.clear()
        token = secrets.token_urlsafe(32)
        self.sessions[token] = now + 3600
        return token

    def valid(self, token):
        return self.sessions.get(token, 0) > time.monotonic()

    def logout(self, token):
        """Revoke this teacher session before handing the workstation over."""
        self.sessions.pop(token, None)
