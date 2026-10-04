"""What the panel shows about a server, measured locally.

Everything here is a real reading from this machine. Where a number cannot be
obtained -- RCON down, `/proc` absent on Windows, the JVM reporting nothing --
the field is `None` and `degraded` names the reason, rather than being filled
with a plausible-looking placeholder. A status panel that invents numbers is worse
than one that admits it does not know.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from .server import Server
from .serverconfig import read_properties

# `mc status` exits 3 when the server is stopped, which is not an error.
STATUS_EXIT_RUNNING = 0
STATUS_EXIT_STOPPED = 3

# HZ is almost always 100 on Linux, but read it rather than assume: the CPU
# percentage is computed from clock ticks and a wrong divisor would be a lie.
_CLOCK_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    # Where a status may move to on its own, used to keep the panel from
    # flickering offline when a server is mid-restart.
    "offline": ("online", "starting", "stopping", "error", "unavailable"),
    "online": ("offline", "stopping", "error", "unavailable"),
    "starting": ("online", "offline", "error", "unavailable"),
    "stopping": ("offline", "error", "unavailable"),
    "error": ("offline", "online", "starting", "stopping", "unavailable"),
    "unavailable": ("offline", "online", "starting", "error"),
}


@dataclass
class ServerStatus:
    status: str
    runtime: dict[str, object] = field(default_factory=dict)
    degraded: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"status": self.status, "runtime": self.runtime, "degraded": self.degraded}


def collect(server: Server) -> ServerStatus:
    """Gather everything the dashboard displays for one server."""
    runtime: dict[str, object] = {}
    degraded: list[str] = []

    if not server.is_installed():
        return ServerStatus(
            status="unavailable",
            runtime={"installed": False, "port": server.port},
            degraded=["This server has not been installed yet."],
        )

    runtime["installed"] = True
    runtime["port"] = _configured_port(server) or server.port
    runtime.update(_versions(server))
    # The heap limit is configuration, not a measurement, so it is reported for a
    # stopped server too -- otherwise the panel loses it exactly when an operator
    # is deciding how much to give the machine.
    runtime["memory_limit_bytes"] = _jvm_heap_limit(server)
    runtime["disk_free_bytes"] = shutil.disk_usage(server.dir).free

    pid = server.pid()
    runtime["pid"] = pid

    if pid is None:
        runtime["players_online"] = None
        runtime["players_max"] = _max_players(server)
        runtime["motd"] = read_properties(server.properties_path).get("motd")
        return ServerStatus(status="offline", runtime=runtime, degraded=degraded)

    uptime, memory_bytes = _process_metrics(pid)
    if uptime is None:
        degraded.append("uptime and CPU are unavailable on this platform")
    else:
        runtime["uptime_seconds"] = uptime

    runtime["cpu_percent"] = _mean_cpu_percent(pid)
    runtime["memory_bytes"] = memory_bytes

    players_online, players_max, motd, rcon_note = _players(server)
    runtime["players_online"] = players_online
    runtime["players_max"] = players_max
    runtime["motd"] = motd
    if rcon_note:
        degraded.append(rcon_note)

    runtime["tunnel"] = _tunnel_note(server)

    return ServerStatus(status=_lifecycle_status(server), runtime=runtime, degraded=degraded)


def host_facts(config) -> dict[str, object]:
    """What the agent reports about itself in a heartbeat."""
    usage = shutil.disk_usage(config.data_dir)
    facts: dict[str, object] = {
        "platform": platform.system().lower(),
        "python": platform.python_version(),
        "hostname": socket.gethostname(),
        "cpu_count": os.cpu_count(),
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
        "servers_dir": str(config.servers_dir),
    }
    try:
        load1, load5, load15 = os.getloadavg()
        facts["loadavg"] = [round(load1, 2), round(load5, 2), round(load15, 2)]
    except (AttributeError, OSError):
        # Windows has no getloadavg; say nothing rather than fabricate a number.
        pass
    return facts


def is_plausible_transition(previous: str | None, current: str) -> bool:
    """Whether the panel should accept `current` given what it last showed."""
    if not previous or previous == current:
        return True
    return current in _STATUS_TRANSITIONS.get(previous, ())


# ---- internals --------------------------------------------------------------


def _lifecycle_status(server: Server) -> str:
    """`mc status` is authoritative about starting and stopping transitions.

    The pid file is the ground truth for "running", but a server that is booting
    has no pid yet while `mc start` is still in flight, and that window is exactly
    when the operator is watching the panel.
    """
    result = server.run_mc("status", timeout=30)
    if result.exit_code == STATUS_EXIT_RUNNING:
        return "online"
    lowered = result.output.lower()
    if "starting" in lowered:
        return "starting"
    if "stopping" in lowered:
        return "stopping"
    return "online" if server.pid_alive() else "offline"


def _players(server: Server) -> tuple[int | None, int | None, str | None, str | None]:
    """Online count, max, and MOTD.

    RCON is the only source of a true online count. When it cannot be reached the
    online count stays `None` and `max-players` from the properties file is used,
    so the panel still shows the server's capacity without inventing occupancy.
    """
    properties = read_properties(server.properties_path)
    fallback_max = int(properties["max-players"]) if properties.get("max-players", "").isdigit() else None
    fallback_motd = properties.get("motd")

    try:
        from .rcon import for_server

        with for_server(server) as rcon:
            info = rcon.server_info()
        return (
            info.get("playersOnline"),
            info.get("playersMax") or fallback_max,
            info.get("motd") or fallback_motd,
            None,
        )
    except Exception as exc:
        return None, fallback_max, fallback_motd, f"player count unavailable: {exc}"


def _versions(server: Server) -> dict[str, str | None]:
    """The pins actually installed here, from this server's own pin file."""
    pins: dict[str, str] = {}
    try:
        for line in server.version_file.read_text(encoding="utf8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                pins[key.strip()] = value.strip()
    except OSError:
        pass
    return {
        "minecraft_version": pins.get("MINECRAFT_VERSION"),
        "paper_build": pins.get("PAPER_BUILD"),
        "geyser_version": pins.get("GEYSER_VERSION"),
        "floodgate_version": pins.get("FLOODGATE_VERSION"),
        "java_major": pins.get("JAVA_MAJOR"),
        "min_memory": pins.get("MIN_MEMORY"),
        "max_memory": pins.get("MAX_MEMORY"),
    }


def _configured_port(server: Server) -> int | None:
    raw = read_properties(server.properties_path).get("server-port", "")
    return int(raw) if raw.isdigit() else None


def _max_players(server: Server) -> int | None:
    raw = read_properties(server.properties_path).get("max-players", "")
    return int(raw) if raw.isdigit() else None


def _jvm_heap_limit(server: Server) -> int | None:
    """The Xmx this server will actually boot with, parsed into bytes.

    Read from the pins rather than from the running JVM, so the panel can show
    the limit for a stopped server too. Only the units Paper's own flags use.
    """
    raw = read_properties(server.version_file).get("MAX_MEMORY", "")
    return _parse_memory(raw)


def _parse_memory(raw: str) -> int | None:
    if not raw:
        return None
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    suffix = raw[-1].upper()
    if suffix in units and raw[:-1].isdigit():
        return int(raw[:-1]) * units[suffix]
    return int(raw) if raw.isdigit() else None


def _process_metrics(pid: int) -> tuple[int | None, int | None]:
    """Uptime in seconds, CPU percent, resident memory in bytes."""
    stat = _read_proc_stat(pid)
    if stat is not None:
        rss_pages = _read_proc_rss(pid)
        memory = rss_pages * os.sysconf("SC_PAGE_SIZE") if rss_pages else None
        return stat[0], memory

    # Windows has no /proc. Task Manager's number comes from the same place, but
    # sampling it from Python means spawning a process every heartbeat, so the
    # honest answer there is that it is unavailable rather than a stale value.
    return None, None


def _read_proc_stat(pid: int) -> tuple[int | None, float | None]:
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            raw = handle.read().decode("utf8", "replace")
    except OSError:
        return None, None

    # The comm field is parenthesised and may itself contain spaces and
    # parentheses, so the fields after it are found from the last ')'.
    close = raw.rfind(")")
    if close == -1:
        return None, None
    fields = raw[close + 2 :].split()

    try:
        utime, stime = int(fields[11]), int(fields[12])
        starttime = int(fields[19])
    except (IndexError, ValueError):
        return None, None

    uptime = max(0, int(time.time()) - int(os.sysconf("SC_CLK_TCK")) * starttime // _CLOCK_TICKS)
    cpu_ticks = utime + stime

    # Cumulative ticks are not a rate. Dividing by uptime gives the mean across
    # the process's life, which is what a status panel should show; the previous
    # sample's value is folded in by the caller of this module.
    cpu_percent = round(100.0 * cpu_ticks / _CLOCK_TCK / uptime, 1) if uptime > 0 else None
    return uptime, cpu_percent


def _read_proc_rss(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/statm", "rb") as handle:
            fields = handle.read().decode("ascii", "replace").split()
        return int(fields[1])
    except (OSError, IndexError, ValueError):
        return None


def _mean_cpu_percent(pid: int) -> float | None:
    return _read_proc_stat(pid)[1]


def _tunnel_note(server: Server) -> dict[str, object]:
    """How the server is reachable, as far as this machine can tell.

    Direct mode reports the local port and says plainly that reachability from
    the internet depends on the operator's own port forwarding. It never claims
    the server is publicly reachable when it has no way to know.
    """
    from . import tunnel

    return tunnel.describe(server, server.port)