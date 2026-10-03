"""How a player reaches this server.

A Minecraft server is a raw TCP protocol. Nothing about hosting it can make that
reachable from the internet on its own: either the operator forwards a port on
their router, or a relay outside their network accepts the connection and forwards
it. This module is the seam for the second option.

## What it deliberately does not do

There is no built-in tunnel that "just works", and pretending otherwise would be
the worst outcome: an operator who believes their server is reachable, and is
not. `cloudflared`'s quick tunnel, the obvious free choice, terminates HTTP and
WebSocket and will not carry a game client's TCP connection. So no provider is
enabled by default, `direct` is the default, and `direct` reports reachability as
*unknown* unless the operator tells the agent what they have set up.

## Adding a provider

Subclass `TunnelProvider`, implement `describe`, and register it with
`@register("name")`. `describe` must return only what it can actually observe --
never a hard-coded `reachable: true`. The panel renders whatever comes back,
including `reachable: null`, and null is the honest answer when a provider cannot
tell.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import TunnelNotConfigured

PROVIDERS: dict[str, Callable[..., "TunnelProvider"]] = {}


def register(name: str) -> Callable[[Callable[..., "TunnelProvider"]], Callable[..., "TunnelProvider"]]:
    def decorate(factory: Callable[..., "TunnelProvider"]) -> Callable[..., "TunnelProvider"]:
        PROVIDERS[name] = factory
        return factory

    return decorate


@dataclass(frozen=True)
class TunnelInfo:
    mode: str
    address: str | None = None
    port: int | None = None
    reachable: bool | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "address": self.address,
            "port": self.port,
            "reachable": self.reachable,
            "detail": self.detail,
        }


class TunnelProvider:
    """Base class. `describe` is called from status collection and must not raise."""

    name = "none"

    def __init__(self, config=None, server=None):
        self.config = config
        self.server = server

    def describe(self, port: int) -> TunnelInfo:  # pragma: no cover - overridden
        raise NotImplementedError

    def _spawn(self, argv: list[str], log_name: str) -> subprocess.Popen:
        """Start a long-running helper, detached, with its output captured.

        Detached on purpose: the Minecraft server must survive the agent being
        restarted or the user's terminal closing, and a relay that dies with the
        agent would look exactly like the server being unreachable.
        """
        log = Path(getattr(self.server, "log_dir", ".")) / log_name
        log.parent.mkdir(parents=True, exist_ok=True)

        # Windows needs DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP to outlive
        # this process; POSIX uses start_new_session.
        windows_flags = 0x00000008 | 0x00000200
        try:
            sink = log.open("ab")
        except OSError as exc:
            raise TunnelNotConfigured(f"cannot open {log}: {exc}") from None
        try:
            return subprocess.Popen(
                argv,
                stdout=sink,
                stderr=sink,
                start_new_session=os.name != "nt",
                **({} if os.name != "nt" else {"creationflags": windows_flags}),
            )
        except OSError as exc:
            raise TunnelNotConfigured(f"cannot start {argv[0]!r}: {exc}") from None
        finally:
            sink.close()


@register("direct")
class DirectProvider(TunnelProvider):
    """The server is published the way the operator's own network publishes it.

    The agent can see its own addresses and can see whether something is listening
    locally. It cannot see the operator's router, so it does not guess: the panel
    shows the local port and tells the operator what is still needed.
    """

    name = "direct"

    def describe(self, port: int) -> TunnelInfo:
        if not _is_listening("127.0.0.1", port):
            return TunnelInfo(
                mode=self.name,
                port=port,
                reachable=False,
                detail="Nothing is listening on this port yet, so the server is not running.",
            )

        addresses = _local_addresses()
        local = f"{addresses[0]}:{port}" if addresses else f"127.0.0.1:{port}"
        return TunnelInfo(
            mode=self.name,
            address=local,
            port=port,
            reachable=None,
            detail=(
                f"Running on this machine; players on your local network can use {local}. "
                "Players outside it need port forwarding on your router, or a relay "
                "provider configured in agent.json."
            ),
        )


@register("playit")
class PlayItProvider(TunnelProvider):
    """playit.gg, which does forward raw TCP and is the usual free answer.

    The claim token belongs to the operator and is read from `agent.json`; it is
    never generated here and never sent to the backend.
    """

    name = "playit"
    BINARY_CANDIDATES = ("playit", "playit.exe", "playit-agent")

    def __init__(self, config=None, server=None):
        super().__init__(config, server)
        self._address: str | None = None
        self._process: subprocess.Popen | None = None

    def describe(self, port: int) -> TunnelInfo:
        token = _flag_value(getattr(self.config, "tunnel_args", ()), "--token")
        if not token:
            return TunnelInfo(
                mode=self.name,
                port=port,
                reachable=None,
                detail=(
                    "Not configured. Put your playit claim token in agent.json as "
                    'tunnel: {"provider": "playit", "args": ["--token", "<token>"]}.'
                ),
            )

        binary = _find_binary(self.BINARY_CANDIDATES)
        if binary is None:
            return TunnelInfo(
                mode=self.name,
                port=port,
                reachable=None,
                detail="The playit agent is not installed on this machine. See playit.gg.",
            )

        if self._process is None or self._process.poll() is not None:
            log = Path(getattr(self.server, "log_dir", ".")) / "playit.log"
            self._process = self._spawn([binary, "--ports", str(port), "--token", token, "--wait"], "playit.log")
            self._address = _read_public_address(log)

        return TunnelInfo(
            mode=self.name,
            address=self._address,
            port=port,
            reachable=self._address is not None,
            detail=(
                f"Public address: {self._address}"
                if self._address
                else "Starting the playit agent; the public address appears once it connects."
            ),
        )


@register("command")
class CommandProvider(TunnelProvider):
    """Any relay the operator already runs: frp, ngrok, a reverse SSH tunnel.

    A generic escape hatch, because every operator's network is different and this
    project should not have to ship a client for each one. The operator supplies
    the argv; the agent supervises it and watches its output for a public address.

    That argv comes from `agent.json`, a local file the operator controls. It is
    never fetched from the backend, so the backend cannot choose what this machine
    executes.
    """

    name = "command"

    def __init__(self, config=None, server=None):
        super().__init__(config, server)
        self._address: str | None = None
        self._process: subprocess.Popen | None = None

    def describe(self, port: int) -> TunnelInfo:
        argv = [part.replace("{port}", str(port)) for part in getattr(self.config, "tunnel_args", ()) or ()]
        if not argv:
            return TunnelInfo(
                mode=self.name,
                port=port,
                reachable=None,
                detail=(
                    "No relay command configured. Set tunnel.args in agent.json, for example "
                    '["frpc", "-c", "/path/to/frpc.ini", "--server_port", "{port}"].'
                ),
            )

        if self._process is None or self._process.poll() is not None:
            log = Path(getattr(self.server, "log_dir", ".")) / "tunnel.log"
            self._process = self._spawn(argv, "tunnel.log")
            self._address = _read_public_address(log)
            return TunnelInfo(
                mode=self.name, port=port, reachable=None, detail="Starting the relay command."
            )

        return TunnelInfo(
            mode=self.name,
            address=self._address,
            port=port,
            reachable=self._address is not None,
            detail=self._address or "The relay is running, but no public address was found in its output.",
        )


# ---- selection -------------------------------------------------------------


def provider_for(name: str, config=None, server=None) -> TunnelProvider:
    factory = PROVIDERS.get((name or "direct").strip().lower())
    if factory is None:
        raise TunnelNotConfigured(
            f"unknown tunnel provider {name!r}; available: {', '.join(sorted(PROVIDERS))}"
        )
    return factory(config, server)


def describe(server, port: int) -> dict[str, object]:
    """Never raises. Status has to render whatever the network happens to be doing."""
    from . import config as config_module

    try:
        config = config_module.load()
        provider = provider_for(config.tunnel_provider, config, server)
    except Exception as exc:
        return TunnelInfo(
            mode="unknown", port=port, reachable=None, detail=f"tunnel status unavailable: {exc}"
        ).as_dict()

    try:
        return provider.describe(port).as_dict()
    except Exception as exc:
        return TunnelInfo(
            mode=provider.name, port=port, reachable=None, detail=f"tunnel status failed: {exc}"
        ).as_dict()


# ---- helpers ---------------------------------------------------------------


def _flag_value(args, flag: str) -> str | None:
    items = list(args or ())
    for index, part in enumerate(items):
        if part == flag and index + 1 < len(items):
            return items[index + 1]
        if part.startswith(f"{flag}="):
            return part.split("=", 1)[1]
    return None


def _find_binary(candidates) -> str | None:
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    return None


def _is_listening(host: str, port: int) -> bool:
    """Is something accepting connections on loopback right now?"""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _local_addresses() -> list[str]:
    """Best-effort list of this host's routable IPv4 addresses.

    A connected UDP socket asks the routing table which source address it would
    use for a non-local destination. Nothing is sent; no packet leaves the host.
    """
    found: list[str] = []
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: routable, never reachable.
        found.append(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if address not in found:
                found.append(address)
    except OSError:
        pass

    routable = [a for a in found if not a.startswith("127.")]
    return routable or found


# Relay log shapes, tried in order of how specific they are.
_ADDRESS_PATTERNS = (
    # frp: "start proxy success: name [tcp] --> 127.0.0.1:25565"
    re.compile(r"-->\s*(?:\[([0-9a-fA-F:]+)\]|([0-9.]+)):\d+"),
    # ngrok: "Forwarding tcp://0.tcp.ngrok.io:12345 --> 25565"
    re.compile(r"(?:tcp://|https?://)([A-Za-z0-9.\-]+\.[A-Za-z0-9.\-]+)(?::\d+)?"),
    # playit and frp's own banner: "connect to 1.2.3.4:25565"
    re.compile(r"(?:connect|access)[^\n]{0,80}?((?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9.\-]+\.[A-Za-z]{2,}):\d{2,5}"),
)

_UNUSABLE = ("127.", "0.0.0.0", "192.0.2.", "localhost")


def _read_public_address(log: Path) -> str | None:
    """Pull a public address out of a relay's log, if it has written one yet.

    Returns `None` rather than guessing. A relay that has not connected yet is
    indistinguishable from one whose banner this cannot parse, and reporting an
    address either way would be a guess presented as a fact.
    """
    try:
        size = log.stat().st_size
        with log.open("rb") as handle:
            handle.seek(max(0, size - 16_384))
            text = handle.read().decode("utf8", "replace")
    except OSError:
        return None

    for pattern in _ADDRESS_PATTERNS:
        for match in pattern.finditer(text):
            candidate = next((group for group in match.groups() if group), None)
            if candidate and not candidate.startswith(_UNUSABLE):
                return candidate
    return None