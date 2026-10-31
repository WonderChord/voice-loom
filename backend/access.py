"""Current-Windows-user local access, with separate read-only subtitle capability."""
from __future__ import annotations

import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
from pathlib import Path
import re
import secrets

from .store import SecretVault, atomic_json

COOKIE = "voice_loom_session"
TOKEN_FILE = ".local-access.json"


class LocalAccess:
    def __init__(self, root: Path, vault=None):
        self.vault = vault or SecretVault()
        path = root / TOKEN_FILE
        if path.exists():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self._secret = self.vault.open(saved["protected_secret"])
                if saved.get("version") != 1 or not re.fullmatch(r"[0-9a-f]{64}", self._secret):
                    raise ValueError
            except (ValueError, KeyError, TypeError, OSError) as exc:
                raise ValueError("本机授权无法读取，请使用创建数据的 Windows 账户打开声织") from exc
        else:
            self._secret = secrets.token_hex(32)
            atomic_json(path, {"version": 1, "protected_secret": self.vault.seal(self._secret)})

    def sign(self, context: str) -> str:
        return hmac.new(self._secret.encode("ascii"), context.encode("ascii"), hashlib.sha256).hexdigest()

    @property
    def browser_session(self) -> str:
        return self.sign("browser-session-v1")

    @property
    def subtitle_key(self) -> str:
        return self.sign("read-only-subtitles-v1")

    def health_proof(self, challenge: str) -> str | None:
        return self.sign("health-proof:" + challenge) if re.fullmatch(r"[0-9a-f]{64}", challenge or "") else None

    @staticmethod
    def same(expected: str, supplied: str | None) -> bool:
        return isinstance(supplied, str) and supplied.isascii() and hmac.compare_digest(expected, supplied)

    def authorized(self, headers: dict[str, str]) -> bool:
        bearer = headers.get("authorization", "")
        if bearer.startswith("Bearer ") and self.same(self.browser_session, bearer[7:]):
            return True
        try:
            cookie = SimpleCookie()
            cookie.load(headers.get("cookie", ""))
            value = cookie[COOKIE].value if COOKIE in cookie else None
            return self.same(self.browser_session, value)
        except (ValueError, KeyError, CookieError):
            return False

    def public_caption(self, method: str, path: str, query: bytes) -> bool:
        from urllib.parse import parse_qs
        if method != "GET" or path not in {"/subtitles", "/api/subtitles/current", "/api/subtitles/events"}:
            return False
        value = parse_qs(query.decode("ascii", errors="replace")).get("access", [None])
        return len(value) == 1 and self.same(self.subtitle_key, value[0])
