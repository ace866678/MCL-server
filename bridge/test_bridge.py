#!/usr/bin/env python3
"""
Tests for bridge.py: the RCON wire protocol, the field parsing, and the HTTP
layer including auth. Runs against a mock RCON server, so no Minecraft needed.

    python3 bridge/test_bridge.py
"""

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bridge  # noqa: E402

PASSWORD = "hunter2"


class MockRcon(threading.Thread):
    """Speaks just enough RCON to satisfy the client."""

    daemon = True
    stopped = False

    def __init__(self, password=PASSWORD, info=None, players=None):
        super().__init__()
        self.password = password
        self.info = info if info is not None else (
            b"A Minecraft Server\x00minecraft:overworld\x0020\x00"
            b"A Minecraft Server\x0019\x00survival\x0020\x0020\x00\x00127.0.0.1\x0025565"
        )
        self.players = players if players is not None else b"Steve\\127.0.0.1\\0\\Alex\\10.0.0.2\\1"
        self.received = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]

    @staticmethod
    def _pack(request_id, packet_type, payload):
        # The length field counts itself and the payload is single-null
        # terminated, which is what vanilla Minecraft does.
        body = struct.pack("<ii", request_id, packet_type) + payload + b"\x00"
        return struct.pack("<i", len(body) + 4) + body

    def run(self):
        # The client opens a fresh connection per command, so keep accepting.
        self.sock.settimeout(0.5)
        while True:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                if self.stopped:
                    return
                continue
            except OSError:
                return
            self._serve(conn)

    def _serve(self, conn):
        conn.settimeout(10)
        try:
            while True:
                header = self._recv(conn, 4)
                if not header:
                    return
                (length,) = struct.unpack("<i", header)
                body = self._recv(conn, length - 4)
                request_id, packet_type = struct.unpack("<ii", body[:8])
                payload = body[8:].decode("utf8", "replace").rstrip("\x00")
                self.received.append((packet_type, payload))

                if packet_type == bridge.SERVERDATA_AUTH:
                    if payload == self.password:
                        conn.sendall(self._pack(request_id, bridge.SERVERDATA_AUTH_RESPONSE, b""))
                        conn.sendall(self._pack(request_id, bridge.SERVERDATA_RESPONSE_VALUE, b""))
                    else:
                        conn.sendall(self._pack(-1, bridge.SERVERDATA_AUTH_RESPONSE, b""))
                elif packet_type == bridge.SERVERDATA_SERVER_INFO:
                    conn.sendall(self._pack(request_id, bridge.SERVERDATA_RESPONSE_VALUE, self.info))
                elif packet_type == bridge.SERVERDATA_SERVER_LIST:
                    conn.sendall(self._pack(request_id, bridge.SERVERDATA_RESPONSE_VALUE, self.players))
                else:
                    conn.sendall(self._pack(request_id, bridge.SERVERDATA_RESPONSE_VALUE, b""))
        except (OSError, struct.error):
            pass
        finally:
            conn.close()

    @staticmethod
    def _recv(conn, count):
        chunks = b""
        while len(chunks) < count:
            block = conn.recv(count - len(chunks))
            if not block:
                return b""
            chunks += block
        return chunks


class ProtocolTests(unittest.TestCase):
    def test_auth_and_server_info(self):
        mock = MockRcon()
        mock.start()
        client = bridge.Rcon("127.0.0.1", mock.port, PASSWORD)
        parsed = bridge.parse_server_info(client.command(bridge.SERVERDATA_SERVER_INFO))
        self.assertEqual(parsed["playersOnline"], 20)
        self.assertEqual(parsed["playersMax"], 20)

    def test_command_is_sent_exactly_once(self):
        """
        Regression guard: an earlier version re-sent the command while draining
        the post-auth packet, so every console command ran three times.
        """
        mock = MockRcon()
        mock.start()
        client = bridge.Rcon("127.0.0.1", mock.port, PASSWORD)
        client.command(bridge.SERVERDATA_SERVER_INFO)
        info_packets = [p for p in mock.received if p[0] == bridge.SERVERDATA_SERVER_INFO]
        self.assertEqual(len(info_packets), 1)

    def test_wrong_password_raises(self):
        mock = MockRcon()
        mock.start()
        client = bridge.Rcon("127.0.0.1", mock.port, "wrong")
        with self.assertRaises(bridge.RconError):
            client.command(bridge.SERVERDATA_SERVER_INFO)

    def test_nothing_listening_raises(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        client = bridge.Rcon("127.0.0.1", port, PASSWORD)
        with self.assertRaises(bridge.RconError):
            client.command(bridge.SERVERDATA_SERVER_INFO)

    def test_parse_player_list(self):
        players = bridge.parse_player_list(b"Steve\\127.0.0.1\\0\\Alex\\10.0.0.2\\1")
        self.assertEqual([p["name"] for p in players], ["Steve", "Alex"])

    def test_parse_player_list_newline_separated(self):
        players = bridge.parse_player_list(b"Steve\\127.0.0.1\\0\nAlex\\10.0.0.2\\1")
        self.assertEqual([p["name"] for p in players], ["Steve", "Alex"])

    def test_parse_player_list_single_player(self):
        players = bridge.parse_player_list(b"Steve\\127.0.0.1\\0")
        self.assertEqual([p["name"] for p in players], ["Steve"])

    def test_parse_empty_player_list(self):
        self.assertEqual(bridge.parse_player_list(b""), [])

    def test_parse_info_field_order_swapped(self):
        """Vanilla puts the protocol version first; others put the MOTD there."""
        raw = b"19\x00A Minecraft Server\x00survival\x0020\x00bedrock:the_end\x0020\x0020"
        parsed = bridge.parse_server_info(raw)
        self.assertEqual(parsed["playersOnline"], 20)
        self.assertEqual(parsed["playersMax"], 20)
        self.assertEqual(parsed["motd"], "A Minecraft Server")

    def test_parse_info_with_no_players(self):
        parsed = bridge.parse_server_info(b"A Server\x00survival\x000\x0010")
        self.assertEqual(parsed["playersOnline"], 0)
        self.assertEqual(parsed["playersMax"], 10)


class HttpTests(unittest.TestCase):
    """Boots the real bridge as a subprocess and talks HTTP to it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = "/tmp/bridge-test"
        subprocess.run(["rm", "-rf", cls.tmp], check=True)
        os.makedirs(cls.tmp + "/server", exist_ok=True)
        with open(cls.tmp + "/server/server.properties", "w") as handle:
            handle.write("motd=Test Server\nmax-players=20\nrcon.port=25575\n")
        with open(cls.tmp + "/VERSION", "w") as handle:
            handle.write("MINECRAFT_VERSION=26.2\nPAPER_BUILD=129\n")
        with open(cls.tmp + "/fake-mc", "w") as handle:
            handle.write("#!/usr/bin/env bash\necho \"mc $@\"\nexit 0\n")
        os.chmod(cls.tmp + "/fake-mc", 0o755)

        cls.mock = MockRcon()
        cls.mock.start()
        with open(cls.tmp + "/server/server.properties", "a") as handle:
            handle.write(f"rcon.port={cls.mock.port}\n")

        cls.port = 18787
        env = dict(os.environ)
        env.update(
            BRIDGE_HOST="127.0.0.1",
            BRIDGE_PORT=str(cls.port),
            BRIDGE_TOKEN="test-token",
            MC_SERVER_DIR=cls.tmp + "/server",
            MC_REPO_ROOT=cls.tmp,
            MC_BIN=cls.tmp + "/fake-mc",
            MC_VERSION_FILE=cls.tmp + "/VERSION",
            RCON_PASSWORD=PASSWORD,
        )
        cls.proc = subprocess.Popen(
            [sys.executable, HERE + "/bridge.py"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(50):
            try:
                cls.call("/health")
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("bridge did not start: " + cls.proc.stderr.read().decode())

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)

    @classmethod
    def call(cls, path, method="GET", body=None, token="test-token"):
        import urllib.error
        import urllib.request

        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}{path}", data=data, method=method
        )
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        if data:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_health(self):
        status, body = self.call("/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_rejects_missing_token(self):
        status, _ = self.call("/health", token=None)
        self.assertEqual(status, 401)

    def test_rejects_wrong_token(self):
        status, _ = self.call("/status", token="nope")
        self.assertEqual(status, 401)

    def test_status_includes_rcon_data(self):
        status, body = self.call("/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["rcon"], "ok")
        self.assertEqual(body["playersOnline"], 20)
        self.assertEqual(body["minecraftVersion"], "26.2")
        self.assertEqual(body["playersMax"], 20)

    def test_players(self):
        status, body = self.call("/players")
        self.assertEqual(status, 200)
        self.assertEqual([p["name"] for p in body["players"]], ["Steve", "Alex"])

    def test_console_allowlisted(self):
        status, body = self.call("/console", "POST", {"command": "list"})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_console_rejects_unlisted(self):
        status, body = self.call("/console", "POST", {"command": "op Notch"})
        self.assertEqual(status, 400)
        self.assertIn("allowlist", body["detail"])

    def test_console_rejects_injected_second_command(self):
        status, body = self.call("/console", "POST", {"command": "list\nstop"})
        self.assertEqual(status, 400)

    def test_console_requires_command(self):
        status, _ = self.call("/console", "POST", {})
        self.assertEqual(status, 400)

    def test_backup_invokes_mc(self):
        status, body = self.call("/backup", "POST", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_unknown_path(self):
        status, _ = self.call("/nope")
        self.assertEqual(status, 404)

    def test_invalid_json(self):
        import urllib.request

        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/console", data=b"{not json", method="POST"
        )
        request.add_header("Authorization", "Bearer test-token")
        try:
            urllib.request.urlopen(request, timeout=10)
            self.fail("expected 400")
        except Exception as exc:  # HTTPError
            self.assertEqual(getattr(exc, "code", None), 400)


class MockServer:
    """
    `python3 bridge/test_bridge.py --mock-server` runs a bridge in front of a mock
    RCON server, so the web app can be developed without a Minecraft server.
    """

    def __init__(self, port=18790):
        import tempfile

        self.tmp = tempfile.mkdtemp(prefix="mc-mock-")
        os.makedirs(self.tmp + "/server", exist_ok=True)
        mock = MockRcon()
        mock.start()
        self.mock = mock

        with open(self.tmp + "/VERSION", "w") as handle:
            handle.write(
                "MINECRAFT_VERSION=26.2\nPAPER_BUILD=129\n"
                "GEYSER_VERSION=2.11.3\nFLOODGATE_VERSION=2.2.5\n"
            )
        with open(self.tmp + "/fake-mc", "w") as handle:
            handle.write('#!/usr/bin/env bash\necho "ran mc $@"\nexit 0\n')
        os.chmod(self.tmp + "/fake-mc", 0o755)
        # A live pid so the bridge reports the server as running.
        with open(self.tmp + "/server/server.pid", "w") as handle:
            handle.write(str(os.getpid()))
        with open(self.tmp + "/server/server.properties", "w") as handle:
            handle.write(f"motd=Local development\nmax-players=32\nrcon.port={mock.port}\n")

        env = dict(os.environ)
        env.update(
            BRIDGE_HOST="127.0.0.1",
            BRIDGE_PORT=str(port),
            BRIDGE_TOKEN="dev-token",
            MC_SERVER_DIR=self.tmp + "/server",
            MC_REPO_ROOT=self.tmp,
            MC_BIN=self.tmp + "/fake-mc",
            MC_VERSION_FILE=self.tmp + "/VERSION",
            RCON_PASSWORD=PASSWORD,
        )
        self.proc = subprocess.Popen(
            [sys.executable, HERE + "/bridge.py"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def instructions(self):
        return (
            "Mock bridge listening on http://127.0.0.1:18790\n"
            "\nIn web/:\n"
            "  MINECRAFT_BRIDGE_URL=http://127.0.0.1:18790 \\\n"
            "  MINECRAFT_BRIDGE_TOKEN=dev-token \\\n"
            "  ADMIN_PASSWORD=dev \\\n"
            "  npm run dev\n"
            "\nIt reports two players online (Steve and Alex). Ctrl-C to stop."
        )


if __name__ == "__main__":
    if "--mock-server" in sys.argv:
        server = MockServer()
        print(server.instructions())
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.proc.terminate()
        sys.exit(0)
    unittest.main(verbosity=2)
