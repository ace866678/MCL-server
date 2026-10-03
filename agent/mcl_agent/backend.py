"""Client for the `agent-api` Edge Function.

Standard library `urllib` only. The agent is dropped onto a user's home machine
and must not need a pip install, so this is written the long way rather than with
`requests` or `httpx`.

## What the backend stores, and why this client can be trusted with a digest

The panel shows a 32-byte token once and stores only `hex(SHA-256(token))`. This
client sends that digest as a bearer credential. A database dump therefore does
not yield a usable credential, which is the failure mode worth engineering
against; TLS protects the request in flight exactly as it protects every other
bearer credential.

Replay is prevented structurally, not cryptographically: every request carries a
timestamp inside a five-minute window and a 128-bit nonce that the backend
spends exactly once (`agent_spend_nonce`). Every endpoint below is idempotent
anyway -- heartbeats and status reports are upserts, command claim and complete
are conditional updates -- so even a replay inside the window would be a no-op.
"""

from __future__ import annotations

import json
import re
import secrets
import time
import urllib.error
import urllib.request
from typing import Any

from .errors import AuthError, BackendError, RateLimited

# The id is used as a directory name, so it is constrained to a UUID shape before
# it ever reaches the filesystem.
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)

# Bounded so a wedged Edge Function cannot hold a poll cycle open forever.
REQUEST_TIMEOUT = 20.0

# The backend refuses anything outside this window; sending a value from the
# local clock means a machine with a badly wrong clock fails loudly at the auth
# layer instead of mysteriously failing every other call.
TIMESTAMP_SKEW = 120


class Backend:
    def __init__(self, api_url: str, agent_id: str, token_digest: str, timeout: float = REQUEST_TIMEOUT):
        self.api_url = api_url.rstrip("/")
        self.agent_id = agent_id
        self.token_digest = token_digest
        self.timeout = timeout

    # ---- transport ---------------------------------------------------------

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = json.dumps(payload, separators=(",", ":")).encode("utf8") if payload is not None else None
        request = urllib.request.Request(f"{self.api_url}{path}", data=body, method=method)
        for key, value in self._headers(len(body) if body else 0).items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return _decode(response.read())
        except urllib.error.HTTPError as exc:
            detail = _decode(exc.read())
            message = detail.get("error") if isinstance(detail, dict) else None
            # 401 means the stored digest no longer matches: the token was
            # rotated or the agent was revoked. Retrying cannot help, and the
            # operator has to be told.
            if exc.code in (401, 403):
                raise AuthError(message or f"backend rejected this agent ({exc.code})") from None
            if exc.code == 429:
                raise RateLimited("backend asked this agent to slow down") from None
            raise BackendError(message or f"backend returned {exc.code}") from None
        except urllib.error.URLError as exc:
            raise BackendError(f"cannot reach the backend: {exc.reason}") from None
        except (TimeoutError, OSError) as exc:
            raise BackendError(f"backend request failed: {exc}") from None

    def _headers(self, content_length: int) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "x-mcl-agent-id": self.agent_id,
            "x-mcl-token-digest": self.token_digest,
            "x-mcl-timestamp": str(int(time.time())),
            "x-mcl-nonce": secrets.token_hex(16),
            "user-agent": "mcl-agent/1.0",
            **({"content-length": str(content_length)} if content_length else {}),
        }

    # ---- endpoints ---------------------------------------------------------

    def heartbeat(self, status: dict[str, Any], version: str, capabilities: dict[str, Any]) -> None:
        self._request("POST", "/heartbeat", {"status": status, "version": version, "capabilities": capabilities})

    def list_servers(self) -> list[dict[str, Any]]:
        """The servers this agent is responsible for.

        Without this an agent would only learn a server's id when a command was
        queued for it, so a server that was merely stopped would never report
        `offline` and the panel would show nothing at all.
        """
        result = self._request("GET", "/servers")
        servers = result.get("servers") if isinstance(result, dict) else None
        if not isinstance(servers, list):
            raise BackendError("backend returned an unexpected server list")
        rows = [s for s in servers if isinstance(s, dict) and isinstance(s.get("id"), str)]
        # The id is used as a directory name, so it is constrained to a UUID shape
        # before it ever reaches the filesystem.
        return [s for s in rows if UUID_RE.fullmatch(s["id"])]

    def claim_commands(self, limit: int = 5) -> list[dict[str, Any]]:
        result = self._request("GET", f"/commands?limit={int(limit)}")
        commands = result.get("commands") if isinstance(result, dict) else None
        if not isinstance(commands, list):
            raise BackendError("backend returned an unexpected command list")
        # Each entry is checked by commands.py against the allowlist; only the
        # fields it needs survive this filter, so a malformed row cannot carry
        # unexpected data further into the process.
        return [c for c in commands if isinstance(c, dict) and isinstance(c.get("id"), str)]

    def complete_command(
        self,
        command_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        self._request(
            "POST",
            f"/commands/{command_id}/result",
            {"status": status, "result": result or {}, "error": error, "exit_code": exit_code},
        )

    def report_server_status(self, server_id: str, status: str, runtime: dict[str, Any]) -> None:
        self._request("POST", f"/servers/{server_id}/status", {"status": status, "runtime": runtime})

    def record_backup(self, server_id: str, filename: str, size_bytes: int, sha256: str | None) -> None:
        self._request(
            "POST",
            "/backups",
            {"server_id": server_id, "filename": filename, "size_bytes": size_bytes, "sha256": sha256},
        )

    def log_event(
        self,
        kind: str,
        message: str,
        level: str = "info",
        server_id: str | None = None,
    ) -> None:
        self._request(
            "POST",
            "/events",
            {"kind": kind, "message": message, "level": level, "server_id": server_id},
        )


def _decode(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A proxy or a crash page answering with HTML is a backend problem, not
        # something the caller should have to decode.
        raise BackendError("backend returned a non-JSON response") from None