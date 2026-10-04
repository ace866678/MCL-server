"""Minecraft RCON client, shared by the bridge and the agent.

This is the implementation that already shipped in `bridge/bridge.py`, moved
here unchanged in behaviour so the status page and the agent cannot drift apart.
Both consumers import from this file rather than keeping their own copy.

Vanilla RCON: length-prefixed, little-endian, null-terminated payloads.
Documented at minecraft.wiki/w/Java_Edition_protocol/RCon.

Standard library only. `mc` already needs python3, and an agent that can be
dropped onto a home machine should not need a pip install to report who is online.
"""

from __future__ import annotations

import re
import secrets
import socket
import struct

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_RESPONSE_VALUE = 0
SERVERDATA_SERVER_INFO = 0x04
SERVERDATA_SERVER_LIST = 0x0B

DEFAULT_TIMEOUT = 5.0


class RconError(Exception):
    """Any RCON failure: unreachable, wrong password, or a malformed reply."""


class Rcon:
    def __init__(self, host: str, port: int, password: str, timeout: float = DEFAULT_TIMEOUT):
        self.host = host
        self.port = int(port)
        self.password = password
        self.timeout = float(timeout)

    def _next_id(self) -> int:
        # 0 is reserved for "no reply"; a random int avoids a stale packet on a
        # reused socket being mistaken for this request's answer.
        return secrets.randbelow(2**31 - 1) + 1

    @staticmethod
    def _pack(packet_type: int, payload: str, request_id: int) -> bytes:
        body = struct.pack("<ii", request_id, packet_type) + payload.encode("utf8") + b"\x00"
        # The length field counts its own four bytes.
        return struct.pack("<i", len(body) + 4) + body

    @staticmethod
    def _read_exactly(sock: socket.socket, count: int) -> bytes:
        chunks = b""
        while len(chunks) < count:
            block = sock.recv(count - len(chunks))
            if not block:
                raise RconError("connection closed by the server")
            chunks += block
        return chunks

    def _read_packet(self, sock: socket.socket) -> bytes:
        header = self._read_exactly(sock, 4)
        (length,) = struct.unpack("<i", header)
        # The length field counts itself, so the body is 4 bytes shorter, and a
        # body has to hold at least a request id and a type.
        if length < 13 or length > 4096:
            raise RconError(f"implausible packet length {length}")
        body = self._read_exactly(sock, length - 4)
        if len(body) < 8:
            raise RconError("truncated packet from the server")
        return body

    def _connect(self) -> socket.socket:
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            raise RconError(f"cannot reach RCON on {self.host}:{self.port}: {exc}") from exc
        sock.settimeout(self.timeout)
        return sock

    def _send(self, sock: socket.socket, packet_type: int, payload: str = "") -> int:
        request_id = self._next_id()
        sock.sendall(self._pack(packet_type, payload, request_id))
        return request_id

    def _receive(self, sock: socket.socket) -> tuple[int, int, str]:
        body = self._read_packet(sock)
        request_id, packet_type = struct.unpack("<ii", body[:8])
        return request_id, packet_type, body[8:].decode("utf8", "replace").rstrip("\x00")

    def command(self, packet_type: int, payload: str = "") -> str:
        """Send one packet and return its reply.

        Servers answer a successful AUTH with an AUTH_RESPONSE *and* an empty
        RESPONSE_VALUE, and that empty packet can arrive after the command we
        sent next. So replies are matched on request id and anything else is
        discarded, rather than assuming the packet order.
        """
        with self._connect() as sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            self._send(sock, SERVERDATA_AUTH, self.password)
            while True:
                request_id, response_type, value = self._receive(sock)
                if response_type == SERVERDATA_AUTH_RESPONSE:
                    if request_id == -1:
                        raise RconError("RCON authentication failed (bad password)")
                    break
                if "wrong" in value.lower():
                    raise RconError("RCON authentication failed (bad password)")

            wanted = self._send(sock, packet_type, payload)
            while True:
                request_id, response_type, value = self._receive(sock)
                if request_id == wanted and response_type == SERVERDATA_RESPONSE_VALUE:
                    return value

    def execute(self, command: str) -> str:
        """Run a console command. Used for read-only queries; `mc console` is
        the path for anything a user typed, so it stays auditable in the log."""
        return self.command(SERVERDATA_RESPONSE_VALUE, command)

    def server_info(self) -> dict:
        return parse_server_info(self.command(SERVERDATA_SERVER_INFO))

    def player_list(self) -> list[dict]:
        return parse_player_list(self.command(SERVERDATA_SERVER_LIST))


def parse_server_info(raw: str | bytes) -> dict:
    """Pull the interesting fields out of SERVERDATA_SERVER_INFO.

    Field order is not consistent between implementations -- vanilla puts the
    protocol version first, others put the MOTD there -- so locate the numeric
    player pair rather than trusting an index.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf8", "replace")
    fields = [f for f in raw.split("\x00") if f != ""]

    numbers = []
    for index, field in enumerate(fields):
        if re.fullmatch(r"\d{1,6}", field):
            numbers.append((index, int(field)))

    online = maximum = None
    for position in range(len(numbers) - 1):
        (first_index, first), (_, second) = numbers[position], numbers[position + 1]
        # numplayers then maxplayers, adjacent in the field list
        if first_index + 1 == numbers[position + 1][0] and second >= first:
            online, maximum = first, second
            break

    # The MOTD is the first field that is not one of the numbers.
    motd = next((f for f in fields if not re.fullmatch(r"\d{1,6}", f)), None)

    return {"motd": motd, "playersOnline": online, "playersMax": maximum}


def parse_player_list(raw: str | bytes) -> list[dict]:
    """SERVERDATA_SERVER_LIST returns `name\\ip\\id` per line, empty when idle."""
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf8", "replace")

    # Each entry is name\ip\id, with entries separated by a newline -- though some
    # builds pack them into one string separated by NULs instead. Split on every
    # separator, then decide which shape we are looking at.
    fields = [f for f in re.split(r"[\\\n\x00]", raw) if f.strip()]

    if len(fields) >= 3 and len(fields) % 3 == 0:
        return [{"name": fields[index]} for index in range(0, len(fields), 3)]
    return [{"name": field} for field in fields]