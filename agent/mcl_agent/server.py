"""One managed server: where it lives, and the only way `mc` gets invoked.

Every filesystem path and every subprocess in the agent funnels through here.
That is deliberate. There is exactly one place that builds a command line, it
builds it from a fixed list of literals plus validated arguments, and it never
passes through a shell. A command name from the network is checked against an
allowlist in `commands.py` before it ever reaches this module.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import OperationTimeout, ServerNotInstalled

# Longest a single `mc` invocation may run before the agent gives up on it and
# reports failure. `mc stop` waits for the JVM to exit, so this has to be longer
# than a slow world save; `mc install` downloads three jars over the internet.
DEFAULT_OPERATION_TIMEOUT = 300.0

# Output kept from a command. Enough for a stack trace or a Java crash report,
# small enough that a runaway log line cannot fill a heartbeat.
MAX_CAPTURED_OUTPUT = 16_000


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class Server:
    """A single Minecraft server directory managed by this agent."""

    def __init__(
        self,
        server_id: str,
        repo_dir: Path,
        data_dir: Path,
        port: int = 25565,
        backup_dir: Path | None = None,
    ):
        self.id = server_id
        self.repo_dir = repo_dir
        self.port = port
        self.mc_script = repo_dir / "mc"
        # Per-server root. Everything runtime -- world data, logs, plugins,
        # generated properties -- lives under here and is never in git.
        self.dir = data_dir / "servers" / server_id
        self.backup_dir = backup_dir or (data_dir / "backups" / server_id)
        self.log_dir = self.dir / "logs"

    # ---- paths -------------------------------------------------------------

    @property
    def version_file(self) -> Path:
        return self.dir / "version.env"

    @property
    def env_file(self) -> Path:
        """Heap overrides, written by the dashboard's memory settings."""
        return self.dir / "server.env"

    @property
    def properties_path(self) -> Path:
        return self.dir / "server.properties"

    @property
    def console_fifo(self) -> Path:
        return self.dir / "console.fifo"

    @property
    def pid_file(self) -> Path:
        return self.dir / "minecraft.pid"

    @property
    def latest_log(self) -> Path:
        return self.log_dir / "latest.log"

    @property
    def rcon_credentials_path(self) -> Path:
        """Local-only secret. Mode 600, never reported to the backend."""
        return self.dir / "rcon.json"

    @property
    def boot_log(self) -> Path:
        """Where `mc start`'s own stdout/stderr goes when the agent spawns it."""
        return self.log_dir / "agent-start.log"

    def ensure_directories(self) -> None:
        for directory in (self.dir, self.backup_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)

    # ---- state -------------------------------------------------------------

    def is_installed(self) -> bool:
        """Installed means a Paper jar is present and the pin file is readable.

        Checked rather than tracked, because the panel can be reinstalled and
        the directory outlives it; the filesystem is the source of truth.
        """
        if not self.version_file.is_file():
            return False
        plugins = self.dir / "plugins"
        return any(plugins.glob("paper*.jar")) or any(self.dir.glob("paper*.jar"))

    def require_installed(self) -> None:
        if not self.is_installed():
            raise ServerNotInstalled(
                "This server has not been installed yet. Run Install from the dashboard first."
            )

    def pid(self) -> int | None:
        """The pid `mc start` recorded, only if that process is still alive."""
        try:
            raw = self.pid_file.read_text(encoding="utf8").strip()
        except OSError:
            return None
        try:
            pid = int(raw)
        except ValueError:
            return None
        if pid <= 0:
            return None
        return pid if _process_alive(pid) else None

    # ---- process control ---------------------------------------------------

    def environment(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The environment `mc` is invoked with.

        Pointing MC_* at this server's own directories is what lets one agent run
        several servers on one machine without them sharing a world, a pid file,
        or a console FIFO.
        """
        env = dict(os.environ)
        env.update(
            {
                "MC_SERVER_DIR": str(self.dir),
                "MC_VERSION_FILE": str(self.version_file),
                "MC_BACKUP_DIR": str(self.backup_dir),
                "MC_LOCAL_ENV": str(self.env_file),
            }
        )
        if extra:
            env.update(extra)
        return env

    def run_mc(
        self,
        *args: str,
        timeout: float = DEFAULT_OPERATION_TIMEOUT,
        check: bool = False,
    ) -> CommandResult:
        """Run `./mc <args>` for this server and capture its output."""
        if not self.mc_script.is_file():
            raise OperationTimeout(f"mc script not found at {self.mc_script}")
        if any(isinstance(a, (bytes, bytearray)) or "\x00" in str(a) for a in args):
            raise OperationTimeout("refusing to pass a NUL byte to mc")

        try:
            completed = subprocess.run(
                [str(self.mc_script), *[str(a) for a in args]],
                cwd=str(self.repo_dir),
                env=self.environment(),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise OperationTimeout(
                f"`mc {' '.join(args)}` did not finish within {int(timeout)}s."
            ) from None

        output = _truncate(completed.stdout + completed.stderr)
        result = CommandResult(completed.returncode, output)
        if check and not result.ok:
            raise OperationTimeout(f"`mc {' '.join(args)}` failed: {output.strip()[-500:]}")
        return result

    def pid_alive(self) -> bool:
        return self.pid() is not None


def _process_alive(pid: int) -> bool:
    """True if `pid` exists.

    `os.kill(pid, 0)` is the portable-enough check; on Windows it raises for a
    live process it merely lacks rights to signal, which still means alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _truncate(text: str, limit: int = MAX_CAPTURED_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    # Keep the tail: the cause of a failure is at the end, not the beginning.
    return "...(truncated)...\n" + text[-limit:]