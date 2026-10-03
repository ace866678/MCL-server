#!/usr/bin/env python3
"""
HTTP bridge for the Minecraft server.

The web app deployed to Vercel cannot speak the Minecraft protocol, and the
server cannot serve HTTP. This sits in between: a small JSON API over stdio's
HTTP server, reading status and the player list over RCON and forwarding the two
write operations (console, backup) to `mc`.

Python 3 standard library only, because `mc` already needs python3 and adding a
pip install to a server deploy is a failure mode nobody enjoys.

Endpoints (all require `Authorization: Bearer $BRIDGE_TOKEN`):

    GET  /health    liveness, for the tunnel or systemd
    GET  /status    running?, MOTD, player counts, pinned versions
    GET  /players   who is online
    POST /console   run one allowlisted server command
    POST /backup    trigger `mc backup --yes`

Bind to 127.0.0.1 (the default) and reach it through a tunnel; see README.
"""

import argparse
import hmac
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- RCON ------------------------------------------------------------------
# Vanilla Minecraft RCON: length-prefixed, little-endian, null-terminated
# payloads. Documented at minecraft.wiki/w/Java_Edition_protocol/RCon.

SERVERDATA_AUTH = 3
SERVERDATA_AUTH_RESPONSE = 2
SERVERDATA_RESPONSE_VALUE = 0
SERVERDATA_SERVER_INFO = 0x04
SERVERDATA_SERVER_LIST = 0x0B

RCON_TIMEOUT = 5.0


class RconError(Exception):
    pass


class Rcon:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.request_id = 0

    def _next_id(self):
        # 0 is reserved for "no reply"; a random int avoids a stale packet on a
        # reused socket being mistaken for this request's answer.
        self.request_id = secrets.randbelow(2**31 - 1) + 1
        return self.request_id

    def _pack(self, packet_type, payload, request_id):
        body = struct.pack("<ii", request_id, packet_type) + payload.encode("utf8") + b"\x00"
        # The length field counts its own four bytes.
        return struct.pack("<i", len(body) + 4) + body

    def _read_packet(self, sock):
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

    @staticmethod
    def _read_exactly(sock, count):
        chunks = b""
        while len(chunks) < count:
            block = sock.recv(count - len(chunks))
            if not block:
                raise RconError("connection closed by the server")
            chunks += block
        return chunks

    def _connect(self):
        try:
            sock = socket.create_connection((self.host, self.port), timeout=RCON_TIMEOUT)
        except OSError as exc:
            raise RconError(f"cannot reach RCON on {self.host}:{self.port}: {exc}") from exc
        sock.settimeout(RCON_TIMEOUT)
        return sock

    def _send(self, sock, packet_type, payload=""):
        request_id = self._next_id()
        sock.sendall(self._pack(packet_type, payload, request_id))
        return request_id

    def _receive(self, sock):
        body = self._read_packet(sock)
        request_id, packet_type = struct.unpack("<ii", body[:8])
        value = body[8:].decode("utf8", "replace").rstrip("\x00")
        return request_id, packet_type, value

    def command(self, packet_type, payload=""):
        """
        Send one packet and return its reply.

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


def parse_server_info(raw):
    """
    Pull the interesting fields out of SERVERDATA_SERVER_INFO.

    Field order is not consistent between implementations -- vanilla puts the
    protocol version first, others put the MOTD there -- so locate the numeric
    player pair rather than trusting an index.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf8", "replace")
    fields = [f for f in raw.split("\x00")]
    fields = [f for f in fields if f != ""]

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


def parse_player_list(raw):
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


# ---- config ----------------------------------------------------------------


def load_pins(path):
    pins = {}
    try:
        with open(path, "r", encoding="utf8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                pins[key.strip()] = value.strip()
    except OSError:
        pass
    return pins


def read_properties(path):
    values = {}
    try:
        with open(path, "r", encoding="utf8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def env_or(path, key, default=""):
    value = os.environ.get(key)
    if value:
        return value
    try:
        with open(path, "r", encoding="utf8") as handle:
            for line in handle:
                line = line.strip()
                if not line.startswith(key + "="):
                    continue
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


# ---- server queries --------------------------------------------------------


def process_running(pid_file):
    try:
        with open(pid_file, "r", encoding="utf8") as handle:
            pid = int(handle.read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


class Bridge:
    def __init__(self, args):
        self.args = args
        self.pins = load_pins(args.version_file)
        self.properties_path = os.path.join(args.server_dir, "server.properties")
        self.pid_file = os.path.join(args.server_dir, "server.pid")
        self.password = args.rcon_password or ""
        self.lock = threading.Lock()

    def rcon(self):
        props = read_properties(self.properties_path)
        port = int(props.get("rcon.port", "25575") or 25575)
        return Rcon(props.get("server-ip", "127.0.0.1") or "127.0.0.1", port, self.password)

    def status(self):
        props = read_properties(self.properties_path)
        pid = process_running(self.pid_file)
        max_players = props.get("max-players", "")
        payload = {
            "running": pid is not None,
            "pid": pid,
            "minecraftVersion": self.pins.get("MINECRAFT_VERSION"),
            "paperBuild": self.pins.get("PAPER_BUILD"),
            "geyserVersion": self.pins.get("GEYSER_VERSION"),
            "floodgateVersion": self.pins.get("FLOODGATE_VERSION"),
            "motd": props.get("motd"),
            # Filled in from RCON below; the properties file is the fallback when
            # RCON is unreachable so the page still shows a sane max.
            "playersOnline": None,
            "playersMax": int(max_players) if max_players.isdigit() else None,
            "crossplay": True,
            "rcon": "unknown",
        }

        if not self.password:
            payload["rcon"] = "no password configured"
            return payload

        try:
            raw = self.rcon().command(SERVERDATA_SERVER_INFO)
        except (RconError, OSError, struct.error) as exc:
            payload["rcon"] = f"error: {exc}"
            return payload

        info = parse_server_info(raw)
        payload["rcon"] = "ok"
        for key in ("motd", "playersOnline", "playersMax"):
            if info[key] is not None:
                payload[key] = info[key]
        return payload

    def players(self):
        if not self.password:
            return {"players": [], "rcon": "no password configured"}
        try:
            raw = self.rcon().command(SERVERDATA_SERVER_LIST)
        except (RconError, OSError, struct.error) as exc:
            return {"players": [], "rcon": f"error: {exc}"}
        return {"players": parse_player_list(raw), "rcon": "ok"}

    def console(self, command):
        allowed = self.args.allow
        head = command.strip().split(" ")[0].lower()
        if head not in allowed:
            return False, f"command '{head}' is not allowlisted"
        # One command per request; the allowlist plus this check stops a caller
        # smuggling a second command after a newline.
        if "\n" in command or "\r" in command:
            return False, "commands must be a single line"
        result = subprocess.run(
            [self.args.mc, "console", command],
            cwd=self.args.repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()

    def backup(self):
        result = subprocess.run(
            [self.args.mc, "backup", "--yes"],
            cwd=self.args.repo_root,
            capture_output=True,
            text=True,
            timeout=self.args.backup_timeout,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()


# ---- HTTP ------------------------------------------------------------------


def make_handler(bridge):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mc-bridge/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quieter than the default
            if bridge.args.verbose:
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def authorised(self):
            expected = bridge.args.token
            header = self.headers.get("Authorization", "")
            presented = header[7:] if header.startswith("Bearer ") else ""
            # compare_digest: a byte-by-byte compare leaks the token one char at
            # a time to anyone willing to measure.
            return bool(expected) and hmac.compare_digest(presented, expected)

        def reply(self, status, body):
            payload = json.dumps(body, indent=2).encode("utf8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def deny(self):
            self.reply(401, {"error": "unauthorised"})

        def do_GET(self):
            if not self.authorised():
                return self.deny()
            if self.path.rstrip("/") == "/health":
                return self.reply(200, {"ok": True})
            if self.path.rstrip("/") == "/status":
                return self.reply(200, bridge.status())
            if self.path.rstrip("/") == "/players":
                return self.reply(200, bridge.players())
            return self.reply(404, {"error": "not found"})

        def do_POST(self):
            if not self.authorised():
                return self.deny()
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length > 8192:
                return self.reply(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self.reply(400, {"error": "invalid JSON"})

            path = self.path.rstrip("/")
            # One caller at a time: a backup stops the server, and a console
            # command landing at the same moment would be lost.
            with bridge.lock:
                if path == "/console":
                    command = str(body.get("command", ""))
                    if not command:
                        return self.reply(400, {"error": "command required"})
                    ok, detail = bridge.console(command)
                    return self.reply(200 if ok else 400, {"ok": ok, "detail": detail})
                if path == "/backup":
                    ok, detail = bridge.backup()
                    return self.reply(200 if ok else 500, {"ok": ok, "detail": detail})
            return self.reply(404, {"error": "not found"})

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "8787")))
    parser.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN", ""))
    parser.add_argument("--server-dir", default=os.environ.get("MC_SERVER_DIR", ""))
    parser.add_argument("--repo-root", default=os.environ.get("MC_REPO_ROOT", ""))
    parser.add_argument("--mc", default=os.environ.get("MC_BIN", ""))
    parser.add_argument("--version-file", default=os.environ.get("MC_VERSION_FILE", ""))
    parser.add_argument("--rcon-password", default=os.environ.get("RCON_PASSWORD", ""))
    parser.add_argument("--backup-timeout", type=int, default=1800)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--allow",
        default="list,tps,save-all,whitelist,save-off,save-on,seed,tps,list,help,time query,difficulty,gamemode,weather,clear,effect,kick",
        help="comma-separated server commands /console will run",
    )
    args = parser.parse_args()

    if not all([args.token, args.server_dir, args.repo_root, args.mc, args.version_file]):
        parser.error(
            "BRIDGE_TOKEN, MC_SERVER_DIR, MC_REPO_ROOT, MC_BIN and MC_VERSION_FILE are required"
        )
    if not args.rcon_password:
        # Not fatal: status degrades to process-level information and the
        # web page says so rather than failing to render.
        sys.stderr.write(" warn no RCON password: player counts will be unavailable\n")

    args.allow = [item.strip() for item in args.allow.split(",") if item.strip()]
    bridge = Bridge(args)

    server = ThreadingHTTPServer((args.host, args.port), make_handler(bridge))
    server.daemon_threads = True
    sys.stderr.write(f"mc-bridge listening on http://{args.host}:{args.port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()