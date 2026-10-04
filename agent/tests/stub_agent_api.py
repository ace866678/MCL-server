#!/usr/bin/env python3
"""A stand-in for the `agent-api` Edge Function, for end-to-end testing.

    python3 agent/tests/stub_agent_api.py --port 8788

Implements the same routes, headers and response shapes as
`backend/supabase/functions/agent-api/index.ts`, with the database calls replaced
by an in-memory dict. Its purpose is to prove the agent's client side of the
protocol works: the headers it sends, the paths it calls, the payloads it parses,
and the errors it handles.

It is a test fixture, not a server. It stores the token digest in plaintext memory,
binds loopback only, and says so on startup.

Also runnable as a self-check:

    python3 agent/tests/stub_agent_api.py --self-test
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError as _HTTPError  # noqa: F401
from urllib.parse import parse_qs, urlparse

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
REPLAY_WINDOW = 300
MAX_BODY = 64 * 1024

SERVER_1 = "11111111-1111-4111-8111-111111111111"
SERVER_2 = "22222222-2222-4222-8222-222222222222"


class Store:
    """Everything the real implementation keeps in Postgres, in memory."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        # agent_id -> (token_hash, owner_id)
        self.agents: dict[str, tuple[str, str]] = {}
        # agent_id -> [{id, name, port, status}]
        self.servers: dict[str, list[dict]] = {}
        # id -> {server_id, agent_id, command, args, status, result, error}
        self.commands: dict[str, dict] = {}
        self.nonces: dict[tuple[str, str], float] = {}
        self.status_reports: list[dict] = []
        self.backups: list[dict] = []
        self.events: list[dict] = []
        self.heartbeats: list[dict] = []
        self.rate: dict[str, list[float]] = {}

    def rate_ok(self, agent_id: str, limit: int = 60) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        with self.lock:
            hits = [t for t in self.rate.get(agent_id, []) if now - t < 60]
            allowed = len(hits) < limit
            if allowed:
                hits.append(now)
            self.rate[agent_id] = hits
        return allowed

    def spend_nonce(self, agent_id: str, nonce: str) -> bool:
        with self.lock:
            if (agent_id, nonce) in self.nonces:
                return False
            self.nonces[(agent_id, nonce)] = datetime.now(timezone.utc).timestamp()
            # Expire old nonces so the dict does not grow without bound.
            cutoff = datetime.now(timezone.utc).timestamp() - REPLAY_WINDOW
            self.nonces = {k: v for k, v in self.nonces.items() if v > cutoff}
            return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:  # quieter output
        if getattr(self.server, "verbose", False):
            sys.stderr.write("stub: " + fmt % args + "\n")

    # ---- plumbing ----------------------------------------------------------

    def _reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _authenticate(self) -> str | None:
        """Returns the agent id, or sends the refusal itself."""
        agent_id = (self.headers.get("x-mcl-agent-id") or "").lower()
        digest = (self.headers.get("x-mcl-token-digest") or "").lower()
        timestamp = self.headers.get("x-mcl-timestamp") or ""
        nonce = self.headers.get("x-mcl-nonce") or ""

        if not UUID.fullmatch(agent_id) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
            self._reply(401, {"error": "missing or malformed agent credentials"})
            return None

        sent = int(timestamp or 0)
        if abs(int(datetime.now(timezone.utc).timestamp()) - sent) > REPLAY_WINDOW:
            self._reply(401, {"error": "timestamp outside the replay window"})
            return None

        # Nonce is spent before the credential is checked, exactly as in the real
        # function, so an unknown agent cannot be enumerated by watching for a
        # difference in response.
        if not self.store.spend_nonce(agent_id, nonce):
            self._reply(401, {"error": "request already used"})
            return None

        record = self.store.agents.get(agent_id)
        if not record:
            self._reply(401, {"error": "unknown or revoked agent"})
            return None
        expected, _owner = record
        # Constant-time comparison, as the Edge Function uses.
        if not _constant_time(expected, digest):
            self._reply(401, {"error": "unknown or revoked agent"})
            return None
        return agent_id

    def _body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if length > MAX_BODY:
            return {}
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # ---- routes ------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("content-length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        agent_id = self._authenticate()
        if not agent_id:
            return
        url = urlparse(self.path)
        resource = url.path.strip("/").split("/")[-1]

        if not self.store.rate_ok(agent_id):
            self._reply(429, {"error": "rate limited"})
            return

        if resource == "commands":
            limit = int((parse_qs(url.query).get("limit") or ["5"])[0])
            self._reply(200, {"commands": self._claim(agent_id, limit)})
            return
        if resource == "servers":
            # Mirrors the Edge Function's reshaping exactly. The SQL function
            # returns the column under its own name (`server_id`), and the
            # function renames it to `id` while narrowing the row. Getting this
            # wrong is silent -- the agent would simply find no servers -- so the
            # stub does the same two steps rather than passing rows through.
            rows = [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "port": row["port"],
                    "status": row["status"],
                }
                for row in self.store.servers.get(agent_id, [])
            ]
            self._reply(200, {"servers": rows})
            return

        self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        agent_id = self._authenticate()
        if not agent_id:
            return
        body = self._body()
        segments = [s for s in urlparse(self.path).path.strip("/").split("/") if s]
        resource = segments[0] if segments else ""
        action = segments[2] if len(segments) > 2 else ""

        if not self.store.rate_ok(agent_id):
            self._reply(429, {"error": "rate limited"})
            return

        if resource == "heartbeat":
            self.store.heartbeats.append(body)
            self._reply(200, {"ok": True, "server_time": _now()})
            return

        if resource == "commands" and action == "result":
            command = self.store.commands.get(body.get("id") or segments[1])
            if not command or command["agent_id"] != agent_id:
                self._reply(403, {"error": "command not found"})
                return
            command.update(
                status=body.get("status", "failed"),
                result=body.get("result") or {},
                error=body.get("error"),
                exit_code=body.get("exit_code"),
            )
            self._reply(200, {"ok": True})
            return

        if resource == "servers" and action == "status":
            server_id = segments[1]
            owned = any(s["id"] == server_id for s in self.store.servers.get(agent_id, []))
            if not owned:
                self._reply(403, {"error": "server not found for this agent"})
                return
            self.store.status_reports.append(
                {"server_id": server_id, "status": body.get("status"), "runtime": body.get("runtime")}
            )
            for row in self.store.servers.get(agent_id, []):
                if row["id"] == server_id:
                    row["status"] = body.get("status", row["status"])
            self._reply(200, {"ok": True})
            return

        if resource == "backups":
            record = {
                "server_id": body.get("server_id"),
                "filename": body.get("filename"),
                "size_bytes": body.get("size_bytes"),
                "sha256": body.get("sha256"),
            }
            self.store.backups.append(record)
            self._reply(200, {"ok": True, "id": f"backup-{len(self.store.backups)}"})
            return

        if resource == "events":
            self.store.events.append({"kind": body.get("kind"), "message": body.get("message")})
            self._reply(200, {"ok": True})
            return

        if resource == "maintenance":
            self._reply(200, {"ok": True})
            return

        self._reply(404, {"error": "not found"})

    def _claim(self, agent_id: str, limit: int) -> list[dict]:
        with self.store.lock:
            queued = [
                c
                for c in self.store.commands.values()
                if c["agent_id"] == agent_id and c["status"] == "queued"
            ][: max(1, min(limit, 20))]
            for command in queued:
                command["status"] = "running"
            return [dict(c) for c in queued]


def _constant_time(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- the scenario ----------------------------------------------------------

TOKEN = "test-token-" + "a" * 40


def build_store() -> Store:
    """A store with one agent, two servers, and two queued commands."""
    store = Store()
    store.agents[AGENT_ID] = (hashlib.sha256(TOKEN.encode()).hexdigest(), "owner-1")
    store.servers[AGENT_ID] = [
        {"id": SERVER_1, "name": "Survival", "port": 25565, "status": "unavailable"},
        {"id": SERVER_2, "name": "Creative", "port": 25566, "status": "unavailable"},
    ]
    store.commands = {
        "cmd-1": {
            "id": "cmd-1",
            "server_id": SERVER_1,
            "agent_id": AGENT_ID,
            "command": "server.console",
            "args": {"command": "say hello from the panel"},
            "status": "queued",
        },
        "cmd-2": {
            "id": "cmd-2",
            "server_id": SERVER_1,
            "agent_id": AGENT_ID,
            # A command the agent does not know: it must be refused and reported,
            # not silently dropped.
            "command": "server.exec_shell",
            "args": {"cmd": "rm -rf /"},
            "status": "queued",
        },
        "cmd-3": {
            "id": "cmd-3",
            "server_id": SERVER_2,
            "agent_id": AGENT_ID,
            "command": "server.whitelist.add",
            "args": {"player": "Steve_01"},
            "status": "queued",
        },
    }
    return store


AGENT_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def self_test() -> int:
    """Run the agent's Backend and AgentService against this stub in-process."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

    from unittest import mock

    from mcl_agent import commands, status
    from mcl_agent.backend import Backend
    from mcl_agent.config import Config
    from mcl_agent.service import AgentService

    store = build_store()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.store = store
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"stub listening on {url}")

    import tempfile

    tmp = Path(tempfile.mkdtemp())
    repo = Path(__file__).resolve().parent.parent.parent
    config = Config(
        agent_id=AGENT_ID,
        token=TOKEN,
        token_digest=hashlib.sha256(TOKEN.encode()).hexdigest(),
        # The stub is plain http on loopback; the https requirement is enforced
        # by config.load(), which this hand-built Config bypasses on purpose.
        api_url=url,
        data_dir=tmp / "data",
        repo_dir=repo,
        heartbeat_seconds=5,
    )
    (tmp / "data" / "servers").mkdir(parents=True, exist_ok=True)

    backend = Backend(url, config.agent_id, config.token_digest)
    service = AgentService(config, backend)

    with mock.patch.object(status, "collect", return_value=status.ServerStatus(
        status="offline", runtime={"installed": False, "port": 25565}
    )):
        service.cycle()

    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if condition else 'FAIL'}  {label}{f' -- {detail}' if detail else ''}")
        if not condition:
            failures.append(label)

    try:
        print("\nagent -> stub")
        check("heartbeat received", len(store.heartbeats) == 1)
        hb = store.heartbeats[0] if store.heartbeats else {}
        check("heartbeat reports the allowlist", set(hb.get("capabilities", {}).get("commands", [])) == set(commands.ALLOWED))
        check("both servers discovered", len(service.slots) == 2, f"got {len(service.slots)}")
        check("second server's port kept", service.slots[SERVER_2].port == 25566)

        print("\ncommands")
        check("all three commands claimed", all(c["status"] != "queued" for c in store.commands.values()))
        check("unknown command refused", store.commands["cmd-2"]["status"] == "failed",
              str(store.commands["cmd-2"].get("error")))
        check("refusal explains itself", "not a command this agent knows" in str(store.commands["cmd-2"].get("error")))
        check("whitelist command succeeded", store.commands["cmd-3"]["status"] == "succeeded",
              str(store.commands["cmd-3"].get("error")))
        check("whitelist file written",
              (tmp / "data" / "servers" / SERVER_2 / "whitelist.json").is_file())
        check("console command failed cleanly (no server running)",
              store.commands["cmd-1"]["status"] == "failed")

        print("\nstatus reporting")
        reported = {r["server_id"] for r in store.status_reports}
        check("status sent for both servers", reported == {SERVER_1, SERVER_2}, str(reported))

        print("\nreplay protection")
        # A request replayed verbatim must be refused the second time: the nonce
        # is spent on the first, so the replay cannot act twice.
        url_servers = f"{url}/servers"
        request = urllib.request.Request(url_servers)
        for key, value in backend._headers(0).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                first = response.status
        except Exception as exc:
            first = f"error: {exc}"
        check("first request accepted", first == 200, str(first))

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                second = response.status
        except urllib.error.HTTPError as exc:
            second = exc.code
        except Exception as exc:
            second = f"error: {exc}"
        check("replayed nonce refused", second == 401, str(second))

        print("\nbad credentials")
        wrong = Backend(url, AGENT_ID, "0" * 64)
        try:
            wrong.heartbeat({}, "test", {})
            check("wrong digest refused", False, "accepted")
        except Exception as exc:
            check("wrong digest refused", "rejected" in str(exc) or "revoked" in str(exc), str(exc))

        unknown = Backend(url, "99999999-9999-4999-8999-999999999999", "0" * 64)
        try:
            unknown.heartbeat({}, "test", {})
            check("unknown agent refused", False, "accepted")
        except Exception as exc:
            check("unknown agent refused", "revoked" in str(exc), str(exc))
    finally:
        server.shutdown()
        server.server_close()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--self-test", action="store_true", help="run the in-process protocol check")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    Handler.store = build_store()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.verbose = True
    print(
        f"stub agent-api on http://127.0.0.1:{args.port} -- test fixture only, loopback only\n"
        f"agent_id {AGENT_ID}\ntoken    {TOKEN}\n"
        f"servers  {SERVER_1}, {SERVER_2}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())