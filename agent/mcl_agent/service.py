"""The poll loop: heartbeat, claim commands, report status, stay up.

## The failure that matters

A home agent's backend connection breaks all the time -- the router reboots, the
laptop sleeps, the network drops. The whole design here is that this is normal:

* Backoff is capped at a minute, so a long outage does not turn into an
  exponential stall that takes an hour to recover from.
* Servers are **never stopped on agent shutdown.** A panel reload, an agent
  upgrade or a crashed laptop must not take a world offline. The game server is
  spawned detached in `commands._spawn_start` and outlives the agent on purpose.
* `server.start` on an already-running server is a no-op that says so, so a
  replayed or retried command cannot start a second JVM on the same world.

## Ordering within a cycle

Status first, then commands. A user who clicks "start" and then opens the server
page sees the running state before the command finishes, rather than after. Every
command is reported back individually -- including the ones that were refused --
so nothing is left `running` in the panel forever.
"""

from __future__ import annotations

import platform
import signal
import threading
import time
from dataclasses import dataclass, field

from . import commands as commands_module
from . import status as status_module
from .backend import UUID_RE, Backend
from .config import Config, MAX_SERVERS
from .errors import AgentError, AuthError, BackendError, RateLimited
from .logging_setup import get_logger
from .server import Server

LOG = get_logger("service")

# Backoff between failed cycles. The ceiling is short on purpose: an agent that
# waits minutes to notice the backend is back looks broken to its operator.
BACKOFF_INITIAL = 2.0
BACKOFF_MAX = 60.0

# A command that takes longer than this is reported as failed even if it is still
# running locally, so the panel never shows a command stuck on "running". `mc
# install` and `mc backup` are given longer inside their own handlers.
COMMAND_DEADLINE = 1800.0


@dataclass
class ServerSlot:
    """One server assigned to this agent, and the state the last cycle saw."""

    id: str
    port: int = 25565
    last_status: str = "unavailable"
    last_reported: float = 0.0
    runtime: dict = field(default_factory=dict)

    def server(self, config: Config) -> Server:
        return Server(
            server_id=self.id,
            repo_dir=config.repo_dir,
            data_dir=config.data_dir,
            port=self.port,
        )


class AgentService:
    def __init__(self, config: Config, backend: Backend | None = None):
        self.config = config
        self.backend = backend or Backend(config.api_url, config.agent_id, config.token_digest)
        self.stop_event = threading.Event()
        self.slots: dict[str, ServerSlot] = {}
        self.connected = False
        self.cycles = 0
        self.last_error: str | None = None
        self._backoff = BACKOFF_INITIAL

    # ---- lifecycle ---------------------------------------------------------

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._request_stop)
            except (ValueError, OSError):
                # Not the main thread, or a platform without that signal. The
                # loop still checks stop_event, so shutdown still happens.
                pass

    def _request_stop(self, *_: object) -> None:
        LOG.info("shutdown requested; leaving any running servers up")
        self.stop_event.set()

    def run(self) -> None:
        self.install_signal_handlers()
        LOG.info(
            "agent %s starting on %s, talking to %s",
            self.config.agent_id,
            platform.system(),
            self.config.api_url,
        )
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.cycle()
                self._backoff = BACKOFF_INITIAL
            except AuthError as exc:
                # Retrying cannot fix a revoked or mismatched token, and looping
                # on it would hammer the backend and hide the real problem.
                LOG.error("authentication rejected: %s", exc)
                LOG.error("fix agent.json (or MCL_AGENT_TOKEN) and restart the agent")
                self.stop_event.wait(60)
                continue
            except RateLimited as exc:
                self.last_error = str(exc)
                LOG.warning("%s", exc)
            except BackendError as exc:
                self.last_error = str(exc)
                if self.connected:
                    LOG.warning("backend unreachable: %s", exc)
                    self.connected = False
            except Exception as exc:  # a bug here must not kill the agent
                self.last_error = str(exc)
                LOG.exception("unexpected error in a poll cycle: %s", exc)

            self.cycles += 1
            # Keep the cadence even when a cycle was slow, so a slow RCON probe
            # cannot turn a 15-second heartbeat into a 60-second one.
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.0, self.config.heartbeat_seconds - elapsed) or self._backoff)

    # ---- one cycle ---------------------------------------------------------

    def cycle(self) -> None:
        self._report_agent()
        self._sync_servers()

        for command in self.backend.claim_commands():
            self._run_command(command)

        for slot in list(self.slots.values()):
            self._report_server(slot)

        self.connected = True

    def _sync_servers(self) -> None:
        """Learn this agent's assigned servers, and forget any that were removed.

        Discovery runs every cycle, not once at startup, because a server can be
        created or deleted while the agent is running. A server deleted in the
        panel is dropped here so its directory stops being reported; the local
        world data is left alone, since deleting a world is the operator's call.
        """
        try:
            assigned = self.backend.list_servers()
        except BackendError as exc:
            # Discovery failing must not stop commands being claimed or statuses
            # being reported for servers already known from earlier cycles.
            LOG.debug("could not refresh the server list: %s", exc)
            return

        seen: set[str] = set()
        for row in assigned:
            server_id = row.get("id")
            if not isinstance(server_id, str) or not UUID_RE.fullmatch(server_id):
                continue
            seen.add(server_id)
            port = row.get("port")
            slot = self.slots.get(server_id)
            if slot is None:
                self.slots[server_id] = ServerSlot(id=server_id, port=port if isinstance(port, int) else 25565)
                LOG.info("now responsible for server %s", server_id)
            elif isinstance(port, int) and port != slot.port:
                slot.port = port
                LOG.info("server %s moved to port %d", server_id, port)

        for removed in set(self.slots) - seen:
            LOG.info("server %s is no longer assigned to this agent", removed)
            self.slots.pop(removed, None)

    def _report_agent(self) -> None:
        self.backend.heartbeat(
            status={
                **status_module.host_facts(self.config),
                "connected": True,
                "cycles": self.cycles,
                "servers": len(self.slots),
                "last_error": self.last_error,
            },
            version=_version(),
            capabilities={
                "commands": sorted(commands_module.ALLOWED),
                "tunnel_providers": _tunnel_providers(),
                "platforms": [platform.system().lower()],
            },
        )

    def _report_server(self, slot: ServerSlot) -> None:
        server = slot.server(self.config)
        try:
            collected = status_module.collect(server)
            slot.last_status = collected.status
            slot.runtime = collected.runtime
        except Exception as exc:
            # One broken server must not stop the others from reporting.
            LOG.warning("status for %s failed: %s", slot.id, exc)
            collected = status_module.ServerStatus(
                status="error", runtime={"error": str(exc)}, degraded=["status collection failed"]
            )
            slot.last_status = collected.status

        self.backend.report_server_status(slot.id, collected.status, collected.runtime)
        slot.last_reported = time.time()

    # ---- commands ----------------------------------------------------------

    def _run_command(self, command: dict) -> None:
        command_id = command.get("id")
        server_id = command.get("server_id")
        name = command.get("command")
        args = command.get("args") or {}

        if not command_id or not server_id:
            LOG.warning("discarding a command with no id or server_id")
            return

        if not isinstance(server_id, str) or not UUID_RE.fullmatch(server_id):
            LOG.warning("discarding a command with a malformed server_id")
            return

        slot = self.slots.get(server_id) or ServerSlot(id=server_id)
        self.slots[server_id] = slot
        if len(self.slots) > MAX_SERVERS:
            LOG.error("refusing server %s: this agent is already managing %d servers", server_id, MAX_SERVERS)
            self._complete(command_id, "failed", error="This agent is already at its server limit.", exit_code=1)
            return

        server = slot.server(self.config)
        server.ensure_directories()

        LOG.info("command %s for server %s", name, server_id)
        started = time.monotonic()
        try:
            outcome = commands_module.execute(server, name, args)
        except AgentError as exc:
            # Every refusal is reported. A command the agent declines to run has
            # to say so in the panel, otherwise the operator is left watching a
            # spinner with no explanation.
            LOG.warning("command %s refused: %s", name, exc)
            self._complete(command_id, "failed", error=str(exc), exit_code=1)
            return
        except Exception as exc:
            LOG.exception("command %s crashed: %s", name, exc)
            # The message is deliberately generic: a traceback can contain paths
            # and argument values that do not belong in a shared dashboard.
            self._complete(command_id, "failed", error=f"The agent could not run this command: {type(exc).__name__}")
            return

        elapsed = round(time.monotonic() - started, 1)
        result = {**outcome.result, "took_seconds": elapsed}
        status = "succeeded" if outcome.ok else "failed"
        self._complete(command_id, status, result=result, exit_code=outcome.exit_code)

        if name == "server.backup" and outcome.ok:
            self._record_backup(server_id, outcome.result)

        # A command can create a server that had no slot yet, so status is
        # refreshed immediately rather than waiting for the next cycle.
        self._report_server(slot)

    def _record_backup(self, server_id: str, result: dict) -> None:
        filename = result.get("filename")
        if not isinstance(filename, str) or not filename:
            return
        try:
            self.backend.record_backup(
                server_id,
                filename,
                int(result.get("size_bytes") or 0),
                result.get("sha256") if isinstance(result.get("sha256"), str) else None,
            )
        except BackendError as exc:
            # The archive exists locally whether or not the panel learns about it.
            LOG.warning("backup %s was created but not recorded: %s", filename, exc)

    def _complete(
        self,
        command_id: str,
        status: str,
        result: dict | None = None,
        error: str | None = None,
        exit_code: int | None = None,
    ) -> None:
        try:
            self.backend.complete_command(command_id, status, result, error, exit_code)
        except AuthError:
            raise
        except BackendError as exc:
            # The command already ran. Losing the completion only leaves a row
            # that `expire_stale_commands` cleans up, so this is not worth
            # retrying harder than the next cycle will.
            LOG.warning("could not report the result of %s: %s", command_id, exc)


def _version() -> str:
    from . import __version__

    return __version__


def _tunnel_providers() -> list[str]:
    from . import tunnel

    return sorted(tunnel.PROVIDERS)