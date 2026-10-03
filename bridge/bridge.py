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
import pathlib
import struct
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---- RCON ------------------------------------------------------------------
# The protocol implementation is shared with the agent in lib/mcl_rcon.py, so the
# status page and the dashboard read the player list through exactly the same
# code. Names are re-exported here because the bridge's own tests and its callers
# already refer to them unqualified.

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

from mcl_rcon import (  # noqa: E402
    SERVERDATA_AUTH,
    SERVERDATA_AUTH_RESPONSE,
    SERVERDATA_RESPONSE_VALUE,
    SERVERDATA_SERVER_INFO,
    SERVERDATA_SERVER_LIST,
    Rcon,
    RconError,
    parse_player_list,
    parse_server_info,
)

# Kept under its original name: the bridge hard-codes this in its own timeouts.
RCON_TIMEOUT = 5.0


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