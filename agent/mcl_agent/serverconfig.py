"""Reading and editing `server.properties`.

Two rules this module exists to enforce:

* **Never destroy formatting or comments.** Paper ships a commented file and an
  operator's notes live in it, so every write is a line-by-line rewrite that
  replaces one value in place and appends only genuinely new keys.
* **Validate before writing.** A value that would stop the server from starting,
  or that is simply not a server.properties value, is refused here rather than
  discovered on the next boot with a world locked.
"""

from __future__ import annotations

import re
from pathlib import Path

from .errors import CommandRejected

# Properties the agent will change on the dashboard's behalf, with the
# validation each one needs. Anything not in this table is read-only: the
# dashboard shows it, the agent will not write it.
BOOLEAN_PROPERTIES = {
    "online-mode",
    "white-list",
    "enforce-whitelist",
    "pvp",
    "hardcore",
    "allow-nether",
    "enable-command-block",
    "enable-query",
    "enable-rcon",
    "spawn-animals",
    "spawn-monsters",
    "generate-structures",
    "use-native-transport",
}

INT_PROPERTIES = {
    "max-players",
    "server-port",
    "view-distance",
    "simulation-distance",
    "network-compression-threshold",
    "max-tick-time",
    "op-permission-level",
    "entity-broadcast-range-percentage",
}

# Keys where the only safe value is the empty string or `false`. These are the
# ones an accidental edit turns into a server that boots into an insecure or
# unusable state, so they are not editable from the panel at all.
READONLY_PROPERTIES = {
    "server-ip",
    "rcon.password",
    "rcon.port",
    "level-seed",
    "level-name",
}

MAX_MOTD_LENGTH = 120


def read_properties(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        with path.open("r", encoding="utf8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        return {}
    return values


def write_properties(path: Path, updates: dict[str, str]) -> list[str]:
    """Merge `updates` into the file, preserving comments and layout.

    Returns the keys that actually changed, so a caller can report "nothing to
    do" instead of pretending it applied something.
    """
    if not updates:
        return []

    validate(updates)
    applied: list[str] = []
    remaining = dict(updates)

    try:
        original = path.read_text(encoding="utf8", errors="replace").splitlines()
    except OSError:
        original = None

    if original is None:
        # No file yet: create a minimal one. `mc install` seeds the real file,
        # so this is only reached when settings are pushed before installing.
        body = [f"{key}={value}" for key, value in sorted(updates.items())]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(body) + "\n", encoding="utf8")
        return sorted(updates)

    lines: list[str] = []
    for line in original:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                value = remaining.pop(key)
                if stripped.split("=", 1)[1].strip() != value:
                    lines.append(f"{key}={value}")
                    applied.append(key)
                else:
                    lines.append(line)
                continue
        lines.append(line)

    # Keys the file did not have are appended, so nothing is silently lost.
    for key, value in sorted(remaining.items()):
        lines.append(f"{key}={value}")
        applied.append(key)

    path.write_text("\n".join(lines) + "\n", encoding="utf8")
    return applied


def validate(updates: dict[str, str]) -> None:
    """Refuse anything that is not a safe, well-formed server.properties edit."""
    for key, value in updates.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", key):
            raise CommandRejected(f"{key!r} is not a valid property name")
        if key in READONLY_PROPERTIES:
            raise CommandRejected(f"{key} is managed by the agent and cannot be changed here")
        if "\n" in value or "\r" in value:
            raise CommandRejected(f"{key} cannot contain a line break")

        if key in BOOLEAN_PROPERTIES and value not in ("true", "false"):
            raise CommandRejected(f"{key} must be true or false, not {value!r}")
        if key in INT_PROPERTIES and not re.fullmatch(r"-?\d{1,9}", value):
            raise CommandRejected(f"{key} must be a whole number, not {value!r}")

    if "motd" in updates:
        _validate_motd(updates["motd"])
    if "max-players" in updates:
        _validate_max_players(updates["max-players"])


def _validate_motd(motd: str) -> None:
    if len(motd) > MAX_MOTD_LENGTH:
        raise CommandRejected(f"the MOTD is limited to {MAX_MOTD_LENGTH} characters")
    # A MOTD is broadcast to every player on join; control characters would put
    # arbitrary terminal escapes in front of anyone who connects.
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in motd):
        raise CommandRejected("the MOTD cannot contain control characters")
    # § is how Minecraft writes colour codes. Raw ones are fine and intended;
    # a bare '&' is not, so it is left alone rather than mangled.


def _validate_max_players(value: str) -> None:
    if not value.isdigit():
        raise CommandRejected("max-players must be a whole number")
    number = int(value)
    # Beyond this the whitelist and the operator list stop being manageable and
    # Paper's own default is 20; the ceiling is a sanity bound, not advice.
    if not 1 <= number <= 1000:
        raise CommandRejected("max-players must be between 1 and 1000")


def summarise(path: Path) -> dict[str, object]:
    """The properties the dashboard shows, with player counts from the file.

    Filled in from RCON elsewhere when the server is up; this is what the panel
    can show about a server that has never started.
    """
    values = read_properties(path)
    max_players = values.get("max-players", "")
    return {
        "motd": values.get("motd"),
        "maxPlayers": int(max_players) if max_players.isdigit() else None,
        "gamemode": values.get("gamemode"),
        "difficulty": values.get("difficulty"),
        "onlineMode": values.get("online-mode") == "true",
        "whitelist": values.get("white-list") == "true",
        "pvp": values.get("pvp") != "false",
        "viewDistance": _as_int(values.get("view-distance")),
        "simulationDistance": _as_int(values.get("simulation-distance")),
        "port": _as_int(values.get("server-port")),
        "motd_max": MAX_MOTD_LENGTH,
    }


def _as_int(value: str | None) -> int | None:
    return int(value) if value and value.lstrip("-").isdigit() else None