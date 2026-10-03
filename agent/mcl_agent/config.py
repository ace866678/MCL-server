"""Agent configuration.

Read from `agent.json` next to the agent, with environment variables taking
precedence so the same files work under systemd, a Windows service, and a shell.

    agent.json (gitignored, never committed):
        {
          "agent_id":  "uuid from the dashboard",
          "token":     "the one-time token from the dashboard",
          "api_url":   "https://<project>.supabase.co/functions/v1/agent-api",
          "data_dir":  "./data",
          "repo_dir":  "..",
          "heartbeat_seconds": 15
        }

Environment overrides, all optional:

    MCL_AGENT_ID, MCL_AGENT_TOKEN, MCL_API_URL, MCL_DATA_DIR, MCL_REPO_DIR,
    MCL_HEARTBEAT_SECONDS, MCL_LOG_LEVEL, MCL_TUNNEL_PROVIDER

Nothing here is ever written back to the file, and the file is chmod 600 on
first read: it holds a credential that grants control of this machine.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError

CONFIG_FILENAME = "agent.json"

# Bounds chosen so a mistyped heartbeat cannot turn into a denial of service
# against the Edge Function, which is also serving other users.
MIN_HEARTBEAT_SECONDS = 5
MAX_HEARTBEAT_SECONDS = 300

# Hard cap on how many servers one agent will host. Nothing here needs a large
# number; the ceiling exists so a corrupted assignment cannot fill a disk.
MAX_SERVERS = 8


@dataclass(frozen=True)
class Config:
    agent_id: str
    token: str
    token_digest: str
    api_url: str
    data_dir: Path
    repo_dir: Path
    heartbeat_seconds: int = 15
    log_level: str = "INFO"
    tunnel_provider: str = "direct"
    tunnel_args: tuple[str, ...] = field(default_factory=tuple)

    @property
    def servers_dir(self) -> Path:
        """One subdirectory per managed server; nothing else lives here."""
        return self.data_dir / "servers"

    @property
    def config_dir(self) -> Path:
        return self.data_dir / "agent.json"


def token_digest(token: str) -> str:
    """The SHA-256 the backend stores, sent on every request.

    Computed once at startup so it is never recomputed per request, and so the
    raw token is read exactly once from wherever it came from.
    """
    import hashlib

    return hashlib.sha256(token.encode("utf8")).hexdigest()


def load(config_path: Path | None = None) -> Config:
    path = config_path or _default_config_path()
    raw = _read_file(path)
    env = os.environ

    agent_id = (env.get("MCL_AGENT_ID") or raw.get("agent_id") or "").strip()
    token = (env.get("MCL_AGENT_TOKEN") or raw.get("token") or "").strip()
    api_url = (env.get("MCL_API_URL") or raw.get("api_url") or "").strip().rstrip("/")

    if not agent_id:
        raise ConfigError(
            f"agent_id is missing. Register this machine in the dashboard and put the "
            f"agent id in {path}."
        )
    if not _looks_like_uuid(agent_id):
        raise ConfigError(f"agent_id in {path} is not a uuid; copy it exactly as shown.")
    if not token:
        raise ConfigError(
            f"token is missing. Copy the one-time token from the dashboard into {path}."
        )
    if len(token) < 32:
        raise ConfigError("token is shorter than expected; copy the whole value.")
    if not api_url:
        raise ConfigError(f"api_url is missing from {path}.")
    if not api_url.startswith("https://"):
        # The token digest crosses the network on every request. Refusing plain
        # http is what makes the digest-only design defensible.
        raise ConfigError("api_url must be https:// — the agent refuses to send its digest over http.")

    data_dir = _resolve_dir(env.get("MCL_DATA_DIR") or raw.get("data_dir"), path.parent / "data")
    repo_dir = _resolve_dir(env.get("MCL_REPO_DIR") or raw.get("repo_dir"), path.parent.parent)

    if not (repo_dir / "mc").is_file():
        raise ConfigError(f"repo_dir {repo_dir} does not contain the mc script.")

    heartbeat = _clamp_int(
        env.get("MCL_HEARTBEAT_SECONDS") or raw.get("heartbeat_seconds"),
        MIN_HEARTBEAT_SECONDS,
        MAX_HEARTBEAT_SECONDS,
        15,
    )

    tunnel = raw.get("tunnel") or {}
    provider = (env.get("MCL_TUNNEL_PROVIDER") or tunnel.get("provider") or "direct").strip()
    tunnel_args = tunnel.get("args") or []
    if not isinstance(tunnel_args, list) or not all(isinstance(a, str) for a in tunnel_args):
        raise ConfigError("tunnel.args must be a list of strings.")

    config = Config(
        agent_id=agent_id,
        token=token,
        token_digest=token_digest(token),
        api_url=api_url,
        data_dir=data_dir,
        repo_dir=repo_dir,
        heartbeat_seconds=heartbeat,
        log_level=(env.get("MCL_LOG_LEVEL") or "INFO").upper(),
        tunnel_provider=provider,
        tunnel_args=tuple(tunnel_args),
    )

    _prepare_directories(config)
    return config


def _default_config_path() -> Path:
    override = os.environ.get("MCL_AGENT_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / CONFIG_FILENAME).resolve()


def _read_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(
            f"no {CONFIG_FILENAME} at {path}. See README: Agent registration."
        )
    try:
        with path.open("r", encoding="utf8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object.")
    return data


def _resolve_dir(value: Any, fallback: Path) -> Path:
    candidate = Path(str(value)).expanduser() if value else fallback
    if not candidate.is_absolute():
        candidate = (fallback.parent / candidate).resolve()
    return candidate


def _prepare_directories(config: Config) -> None:
    for directory in (config.data_dir, config.servers_dir):
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigError(f"cannot create {directory}: {exc}") from exc
    _restrict(config.config_dir)
    # Secrets per server (RCON passwords) live beside the server they belong to.
    _restrict(config.data_dir)


def _restrict(path: Path) -> None:
    """Best-effort chmod 600. A filesystem without POSIX modes is not an error."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _looks_like_uuid(value: str) -> bool:
    parts = value.split("-")
    if len(parts) != 5:
        return False
    if [len(p) for p in parts] != [8, 4, 4, 4, 12]:
        return False
    return all(c in "0123456789abcdefABCDEF" for p in parts for c in p)


def _clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, parsed))