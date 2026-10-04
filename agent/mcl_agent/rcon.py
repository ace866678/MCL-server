"""Per-server RCON, with a generated local-only password.

The bridge took its RCON password from `/etc/minecraft-bridge.env`. The agent
cannot do that: it manages up to eight servers for possibly different operators,
and it may be running as a user account rather than root. So each server gets its
own generated password, written to `server/rcon.json` with mode 600, and
`server.properties` is pointed at it.

That password never leaves the machine. It is not sent in a heartbeat, a status
report, or a log event -- `status.py` reports *that* RCON answered, never the
credential that answered it.
"""

from __future__ import annotations

import json
import secrets
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_REPO_LIB = Path(__file__).resolve().parent.parent.parent / "lib"
if str(_REPO_LIB) not in sys.path:
    sys.path.insert(0, str(_REPO_LIB))

from mcl_rcon import Rcon, RconError  # noqa: E402

from .errors import AgentError  # noqa: E402

# Bound to loopback. RCON has no authentication beyond its password and gives
# full console access, so binding it to 0.0.0.0 would be equivalent to leaving
# an unpassworded shell on the network.
RCON_HOST = "127.0.0.1"
DEFAULT_RCON_PORT = 25575

MIN_PASSWORD_LENGTH = 24


@contextmanager
def for_server(server) -> Iterator[Rcon]:
    """Yield a connected-ready RCON client for this server.

    Nothing connects until a command is issued, so a stopped server does not log
    a connection error every heartbeat.
    """
    credentials = load_credentials(server)
    if not credentials:
        raise AgentError("RCON is not configured for this server yet")
    yield Rcon(RCON_HOST, credentials["port"], credentials["password"])


def load_credentials(server) -> dict | None:
    try:
        raw = json.loads(server.rcon_credentials_path.read_text(encoding="utf8"))
    except (OSError, json.JSONDecodeError):
        return None
    password = raw.get("password") if isinstance(raw, dict) else None
    port = raw.get("port") if isinstance(raw, dict) else None
    if not isinstance(password, str) or not password:
        return None
    return {"password": password, "port": int(port) if isinstance(port, int) else DEFAULT_RCON_PORT}


def ensure_credentials(server, port: int = DEFAULT_RCON_PORT) -> dict[str, object]:
    """Return this server's RCON credentials, generating them on first use.

    Called when a server is installed or settings are applied. Generating here
    rather than asking the panel means the secret is created on the machine that
    needs it and is never transmitted.
    """
    existing = load_credentials(server)
    if existing:
        return {"port": existing["port"], "password_length": len(existing["password"])}

    credentials = {
        "port": int(port),
        # `token_urlsafe` over 32 bytes: 256 bits of entropy in a value with no
        # quoting hazards in a properties file.
        "password": secrets.token_urlsafe(32),
        "created_at": _now(),
    }
    if len(credentials["password"]) < MIN_PASSWORD_LENGTH:  # pragma: no cover - token_urlsafe(32) is always 43
        raise AgentError("generated RCON password is too short")

    path = server.rcon_credentials_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(credentials, indent=2) + "\n", encoding="utf8")
    _restrict(path)

    # server.properties is where `mc` and Paper both expect it. Writing it here
    # means RCON works the moment the server next starts, with no extra step for
    # the operator and no secret in the panel.
    from .serverconfig import write_properties

    write_properties(
        server.properties_path,
        {
            "enable-rcon": "true",
            "rcon.password": credentials["password"],
            "rcon.port": str(credentials["port"]),
        },
    )

    return {"port": credentials["port"], "password_length": len(password)}


def clear_credentials(server) -> None:
    try:
        server.rcon_credentials_path.unlink()
    except OSError:
        pass


def is_reachable(server) -> tuple[bool, str | None]:
    """Probe RCON without raising.

    Returns `(ok, detail)`. A status report has to render even when RCON is
    misconfigured, so this never propagates an error -- it reports why instead.
    """
    credentials = load_credentials(server)
    if not credentials:
        return False, "rcon not configured"
    try:
        with for_server(server) as rcon:
            rcon.server_info()
        return True, None
    except RconError as exc:
        return False, str(exc)
    except AgentError as exc:
        return False, str(exc)


def _restrict(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows and some network filesystems have no POSIX modes; the file is
        # still inside a per-user directory.
        pass


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "Rcon",
    "RconError",
    "for_server",
    "load_credentials",
    "ensure_credentials",
    "clear_credentials",
    "is_reachable",
    "RCON_HOST",
    "DEFAULT_RCON_PORT",
]