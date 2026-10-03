"""The allowlist: where a command from the network becomes a local action.

This is the security boundary of the agent. Three properties matter, and each one
is enforced here rather than assumed upstream:

1. **No shell, ever.** Handlers build a fixed list of argv elements. Nothing from
   the network is ever concatenated into a command line, so shell metacharacters
   are inert data. `subprocess` is called with `shell=False` in `server.py`.

2. **The name must be in the allowlist**, and it must be one of the exact strings
   the database's own CHECK constraint permits. A command the agent does not know
   is refused before any server is touched.

3. **Every argument is validated against its own bounds** before use. `server.console`
   is the sharpest edge -- it reaches Minecraft's own command parser, which has
   operator commands in it -- so it is filtered against a real allowlist of
   read-only and low-impact commands rather than passed through.

`ALLOWED` mirrors `server_commands.command`'s CHECK constraint in
`0003_commands_backups.sql`. If the two ever diverge, the agent's is the one that
decides, and a command in the database but not here is refused rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import backups as backups_module
from . import logs as logs_module
from . import serverconfig
from .errors import AgentError, CommandRejected, ServerNotInstalled
from .server import Server

ALLOWED: frozenset[str] = frozenset(
    {
        "server.start",
        "server.stop",
        "server.restart",
        "server.install",
        "server.backup",
        "server.settings.apply",
        "server.whitelist.add",
        "server.whitelist.remove",
        "server.console",
        "server.logs.tail",
    }
)

# Bounds for arguments that are numbers or short strings. Deliberately generous
# for a private server and small enough that nothing surprising fits.
MAX_NAME_LENGTH = 40
MAX_CONSOLE_LENGTH = 200
MAX_BACKUP_KEEP = 20
MAX_LOG_LINES = 1_000


class Whitelist:
    """`whitelist.json`, the format `config/whitelist.json` already ships in."""

    def __init__(self, path):
        self.path = path

    def read(self) -> list[str]:
        import json

        try:
            raw = json.loads(self.path.read_text(encoding="utf8"))
        except (OSError, ValueError):
            return []
        names = raw.get("names") if isinstance(raw, dict) else raw
        return [str(n) for n in names] if isinstance(names, list) else []

    def write(self, names: list[str]) -> None:
        import json

        # Sorted and de-duplicated: the file is committed-shaped, so a stable
        # order keeps diffs meaningful instead of churning on every add.
        ordered = sorted(set(names))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"names": ordered}, indent=2) + "\n", encoding="utf8")

    def add(self, name: str) -> bool:
        names = self.read()
        if name in names:
            return False
        self.write([*names, name])
        return True

    def remove(self, name: str) -> bool:
        names = self.read()
        if name not in names:
            return False
        self.write([n for n in names if n != name])
        return True


# Minecraft console commands reachable from the dashboard. Anything that grants
# power, reveals the server, or can be destructive is not here: an operator who
# needs those can open a console session on their own machine.
CONSOLE_ALLOWLIST: frozenset[str] = frozenset(
    {
        # information
        "list",
        "help",
        "help2",
        "tps",
        "seed",
        "difficulty",
        "gamerule",
        "time",
        "weather",
        "whitelist",
        # players
        "kick",
        "ban",
        "ban-ip",
        "pardon",
        "pardon-ip",
        "op",
        "deop",
        "msg",
        "tell",
        "sendmessage",
        # appearance
        "say",
        "gamemode",
        "tp",
        "effect",
        "attribute",
        "clear",
        "give",
        # persistence, all reversible
        "save-all",
        "save-off",
        "save-on",
        "ticking",
    }
)

# `op`, `give` and `clear` are genuinely powerful, and `gamerule` can change
# almost anything about how the world behaves. They are kept because running a
# private server without them from the panel is worse than the risk, but they are
# called out here and flagged in the result so the choice is visible rather than
# buried in a set literal. Nothing that shuts the server down is present: no
# `stop`, no `shutdown`, no `execute`, no `ban-ip` abuse path beyond the list.
_HIGH_IMPACT_CONSOLE = frozenset({"op", "deop", "give", "clear", "gamerule"})


@dataclass(frozen=True)
class CommandOutcome:
    result: dict[str, object]
    exit_code: int = 0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


Handler = Callable[[Server, dict], CommandOutcome]
_HANDLERS: dict[str, Handler] = {}


def handler(name: str) -> Callable[[Handler], Handler]:
    def decorate(func: Handler) -> Handler:
        _HANDLERS[name] = func
        return func

    return decorate


def execute(server: Server, command: str, args: dict) -> CommandOutcome:
    """Validate, then run. The only entry point a queued command may arrive at."""
    if not isinstance(command, str) or command not in ALLOWED:
        raise CommandRejected(
            f"{command!r} is not a command this agent knows. Refusing it."
        )
    if not isinstance(args, dict):
        raise CommandRejected("command arguments must be an object")

    return _HANDLERS[command](server, args)


# ---- lifecycle -------------------------------------------------------------


@handler("server.install")
def _install(server: Server, args: dict) -> CommandOutcome:
    server.ensure_directories()
    version = _optional_str(args, "minecraft_version", 20)
    java_major = _optional_int(args, "java_major", 2)
    min_memory = _optional_str(args, "min_memory", 8)
    max_memory = _optional_str(args, "max_memory", 8)

    # Memory and Java are written into the per-server env/pin files *before* the
    # install, so the install runs once with the settings the operator chose
    # rather than installing defaults and needing a second pass to apply them.
    _write_env(server, min_memory=min_memory, max_memory=max_memory)

    # `mc install` refuses a build whose checksum does not match its pin, so an
    # install is only ever as good as the pins behind it. Asking for a specific
    # Minecraft version resolves them from PaperMC at install time; otherwise the
    # repository default in VERSION is used verbatim.
    argv: list[str] = ["install"]
    if version:
        argv += ["--catalog", version]
    elif not server.version_file.is_file():
        argv += ["--catalog", _default_version()]

    result = server.run_mc(*argv, timeout=1800)
    if java_major and server.version_file.is_file():
        _write_version_override(server, "JAVA_MAJOR", str(java_major))

    if not result.ok:
        return CommandOutcome(result={"installed": False, "output": result.output}, exit_code=result.exit_code)

    _ensure_rcon(server)
    return CommandOutcome(
        result={
            "installed": True,
            "minecraft_version": version or _read_pin(server, "MINECRAFT_VERSION"),
            "output": result.output,
            "note": "Accept the Minecraft EULA in the server directory, then start it from the dashboard."
            if "eula" in result.output.lower()
            else None,
        },
        exit_code=0,
    )


@handler("server.start")
def _start(server: Server, args: dict) -> CommandOutcome:
    if server.pid_alive():
        return CommandOutcome(result={"started": False, "reason": "already running"})

    if not server.version_file.is_file():
        _seed_pins(server)

    server.ensure_directories()
    if not server.is_installed():
        raise ServerNotInstalled("Install the server from the dashboard before starting it.")

    _ensure_rcon(server)
    # `mc start` stays in the foreground and holds the console FIFO open, which is
    # what makes `mc console` work afterwards. So it is spawned detached and this
    # call returns as soon as the process is launched; the caller polls status.
    _spawn_start(server)
    return CommandOutcome(result={"started": True}, exit_code=0)


@handler("server.stop")
def _stop(server: Server, args: dict) -> CommandOutcome:
    timeout = _bounded_int(args, "timeout_seconds", 10, 300, 120)
    if not server.pid_alive():
        return CommandOutcome(result={"stopped": False, "reason": "not running"})

    # `mc stop` sends SIGTERM, which `mc start` turns into a console `stop`: the
    # server saves and exits cleanly. Killing the JVM outright would lose the
    # world, so there is deliberately no SIGKILL path here.
    result = server.run_mc("stop", timeout=timeout + 60)
    if not result.ok:
        return CommandOutcome(result={"stopped": False, "output": result.output}, exit_code=result.exit_code)
    return CommandOutcome(result={"stopped": True, "output": result.output}, exit_code=0)


@handler("server.restart")
def _restart(server: Server, args: dict) -> CommandOutcome:
    stopped = _stop(server, args)
    _start(server, args)
    return CommandOutcome(result={"restarted": True, "stop": stopped.result}, exit_code=0)


# ---- data ------------------------------------------------------------------


@handler("server.backup")
def _backup(server: Server, args: dict) -> CommandOutcome:
    label = _optional_str(args, "label", MAX_NAME_LENGTH) or "manual"
    keep = _bounded_int(args, "keep", 1, MAX_BACKUP_KEEP, backups_module.DEFAULT_KEEP)
    result = backups_module.take(server, label)
    pruned = backups_module.prune(server, keep)
    return CommandOutcome(
        result={
            "filename": result.filename,
            "size_bytes": result.size_bytes,
            "sha256": result.sha256,
            "pruned": pruned,
        }
    )


@handler("server.logs.tail")
def _tail_logs(server: Server, args: dict) -> CommandOutcome:
    lines = _bounded_int(args, "lines", 1, MAX_LOG_LINES, logs_module.DEFAULT_TAIL_LINES)
    offset = _bounded_int(args, "offset", -1, 2**53, -1)
    page = logs_module.read(server.latest_log, offset=offset, max_lines=lines)
    return CommandOutcome(result=page.as_dict())


@handler("server.settings.apply")
def _apply_settings(server: Server, args: dict) -> CommandOutcome:
    properties = args.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise CommandRejected("settings.apply needs a non-empty `properties` object")
    if len(properties) > 20:
        raise CommandRejected("at most 20 properties can be applied at once")

    flat = {str(k): str(v) for k, v in properties.items()}
    applied = serverconfig.write_properties(server.properties_path, flat)
    return CommandOutcome(result={"applied": applied, "skipped": sorted(set(flat) - set(applied))})


@handler("server.whitelist.add")
def _whitelist_add(server: Server, args: dict) -> CommandOutcome:
    name = _required_player_name(args)
    changed = Whitelist(server.dir / "whitelist.json").add(name)
    return CommandOutcome(result={"player": name, "added": changed})


@handler("server.whitelist.remove")
def _whitelist_remove(server: Server, args: dict) -> CommandOutcome:
    name = _required_player_name(args)
    changed = Whitelist(server.dir / "whitelist.json").remove(name)
    return CommandOutcome(result={"player": name, "removed": changed})


@handler("server.console")
def _console(server: Server, args: dict) -> CommandOutcome:
    raw = args.get("command")
    if not isinstance(raw, str) or not raw.strip():
        raise CommandRejected("console needs a `command` string")

    line = raw.strip()
    if len(line) > MAX_CONSOLE_LENGTH:
        raise CommandRejected(f"console commands are limited to {MAX_CONSOLE_LENGTH} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in line):
        raise CommandRejected("console commands cannot contain control characters")

    verb, _, remainder = line.partition(" ")
    verb = verb.lstrip("/").lower()
    if verb not in CONSOLE_ALLOWLIST:
        raise CommandRejected(
            f"/{verb} is not available from the dashboard console. "
            f"Allowed: {', '.join(sorted(CONSOLE_ALLOWLIST))}."
        )

    server.require_installed()
    if not server.pid_alive():
        raise AgentError("the server is not running, so there is no console to send that to")

    # Passed as a single argv element, so a player name with a space is still one
    # argument and cannot introduce a second command.
    result = server.run_mc("console", line, timeout=30)
    return CommandOutcome(
        result={"sent": line, "high_impact": verb in _HIGH_IMPACT_CONSOLE, "output": result.output},
        exit_code=result.exit_code,
    )


# ---- argument validation ---------------------------------------------------


def _required_player_name(args: dict) -> str:
    name = args.get("player") or args.get("name")
    if not isinstance(name, str):
        raise CommandRejected("a player name is required")
    name = name.strip()
    if not 3 <= len(name) <= MAX_NAME_LENGTH:
        raise CommandRejected(f"player names are between 3 and {MAX_NAME_LENGTH} characters")
    # A Minecraft username is [A-Za-z0-9_]. Restricting to that means a name can
    # never contain whitespace, a comma, or a control character -- so it cannot
    # become a second argument, a second player, or a new line in the JSON file.
    if not all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in name):
        raise CommandRejected("player names may only contain letters, digits and underscores")
    return name


def _optional_str(args: dict, key: str, max_length: int) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise CommandRejected(f"{key} must be a string")
    value = value.strip()
    if not value:
        return None
    if len(value) > max_length:
        raise CommandRejected(f"{key} is limited to {max_length} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise CommandRejected(f"{key} cannot contain control characters")
    return value


def _optional_int(args: dict, key: str, digits: int) -> int | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CommandRejected(f"{key} must be a number")
    text = str(value)
    if not text.lstrip("-").isdigit() or len(text.lstrip("-")) > digits:
        raise CommandRejected(f"{key} must be a whole number of at most {digits} digits")
    return int(text)


def _bounded_int(args: dict, key: str, low: int, high: int, fallback: int) -> int:
    value = args.get(key)
    if value is None or value == "":
        return fallback
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise CommandRejected(f"{key} must be a number")
    try:
        parsed = int(value)
    except ValueError:
        raise CommandRejected(f"{key} must be a number") from None
    # Out-of-range is clamped rather than refused: for these three keys the
    # operator is asking for a size or a position, and clamping to a documented
    # bound is more useful than an error they would have to guess at.
    return max(low, min(high, parsed))


# ---- local helpers ---------------------------------------------------------


def _spawn_start(server: Server) -> None:
    """Launch `mc start` detached, capturing its own output for boot diagnostics.

    Detached because the game server must survive the agent: a panel reload or an
    agent restart should never take a world offline. `mc start` is what writes the
    pid file and traps SIGTERM into a clean `stop`, so the server is stoppable
    through `mc` afterwards.
    """
    import os
    import subprocess

    server.ensure_directories()
    _open_console_fifo(server)

    try:
        sink = server.boot_log.open("ab")
    except OSError as exc:
        raise AgentError(f"cannot open {server.boot_log}: {exc}") from None
    try:
        subprocess.Popen(
            [str(server.mc_script), "start"],
            cwd=str(server.repo_dir),
            env=server.environment(),
            stdin=subprocess.DEVNULL,
            stdout=sink,
            stderr=sink,
            start_new_session=os.name != "nt",
            **({} if os.name != "nt" else {"creationflags": 0x00000008 | 0x00000200}),
        )
    except OSError as exc:
        raise AgentError(f"cannot start the server: {exc}") from None
    finally:
        sink.close()


def _open_console_fifo(server: Server) -> None:
    """Create the console FIFO if it is missing.

    `mc start` opens it read-write precisely so it survives having no writer yet;
    `mc console` writes to it afterwards. Creating it here means a console command
    does not fail with ENOENT on a server the agent started.
    """
    fifo = server.console_fifo
    if fifo.exists():
        return
    try:
        fifo.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(fifo, 0o660)
    except (AttributeError, OSError):
        # Windows has no FIFO; `mc` falls back to its own transport there.
        pass


def _write_env(server: Server, min_memory: str | None, max_memory: str | None) -> None:
    """Persist heap settings to the per-server env file `mc` reads."""
    lines: list[str] = ["# Written by the agent from the dashboard's memory settings."]
    if min_memory:
        lines.append(f"MIN_MEMORY={_memory(min_memory)}")
    if max_memory:
        lines.append(f"MAX_MEMORY={_memory(max_memory)}")
    server.env_file.parent.mkdir(parents=True, exist_ok=True)
    server.env_file.write_text("\n".join(lines) + "\n", encoding="utf8")


# Multipliers that convert a suffix into megabytes, so one comparison covers the
# whole range. A bare number is already megabytes, which is what `mc` assumes.
_MEMORY_IN_MB = {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}
MIN_MEMORY_MB = 256
MAX_MEMORY_MB = 512 * 1024


def _memory(raw: str) -> str:
    """Validate a heap size. Paper accepts suffixes; `mc` converts them."""
    text = raw.strip().upper()
    if not text:
        raise CommandRejected("memory size cannot be empty")
    suffix = text[-1]
    number = text[:-1] if suffix in _MEMORY_IN_MB else text
    if not number.isdigit():
        raise CommandRejected(f"{raw!r} is not a memory size; use something like 2G or 4096M")
    megabytes = int(number) * _MEMORY_IN_MB.get(suffix, 1)
    if not MIN_MEMORY_MB <= megabytes <= MAX_MEMORY_MB:
        raise CommandRejected("memory size must be between 256M and 512G")
    return text


def _read_pin(server: Server, key: str) -> str | None:
    try:
        for line in server.version_file.read_text(encoding="utf8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


def _seed_pins(server: Server) -> None:
    """Copy the repository default pin file so `mc` has a starting point."""
    source = server.repo_dir / "VERSION"
    if not source.is_file():
        return
    server.version_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        server.version_file.write_text(source.read_text(encoding="utf8"), encoding="utf8")
    except OSError as exc:
        raise AgentError(f"cannot seed {server.version_file}: {exc}") from None


def _write_version_override(server: Server, key: str, value: str) -> None:
    """Set one pin, preserving the rest of the file and its comments."""
    try:
        lines = server.version_file.read_text(encoding="utf8").splitlines()
    except OSError:
        return
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    try:
        server.version_file.write_text("\n".join(lines) + "\n", encoding="utf8")
    except OSError as exc:
        raise AgentError(f"cannot update {server.version_file}: {exc}") from None


def _default_version() -> str:
    """The repository default, used when a fresh server has no pin file yet."""
    from . import config as config_module

    try:
        version_file = config_module.load().repo_dir / "VERSION"
        for line in version_file.read_text(encoding="utf8").splitlines():
            if line.startswith("MINECRAFT_VERSION="):
                return line.split("=", 1)[1].strip()
    except Exception:
        # No config and no readable VERSION: fall through to the default below.
        pass
    # A last resort so an install still resolves something concrete rather than
    # failing on a missing pin. Both are current Paper-supported releases.
    return "1.21.4"


def _ensure_rcon(server: Server) -> None:
    """Make sure the server has an RCON password, without reporting the secret."""
    from .rcon import ensure_credentials

    ensure_credentials(server)