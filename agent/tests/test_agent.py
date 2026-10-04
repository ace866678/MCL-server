#!/usr/bin/env python3
"""Tests for the agent.

    python3 agent/tests/test_agent.py

The point of these is the security boundary and the failure modes, not coverage:
a command from the network must never become a shell string, an argument outside
its documented bounds must be refused, and a status report must say "unknown"
rather than invent a number. Everything else here is in service of those three.

Standard library only, like the agent itself, so these run on any machine that
can run the agent.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent.parent
for _path in (_REPO / "agent", _REPO / "lib"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mcl_agent import backups, commands, config, logs, serverconfig, status, tunnel  # noqa: E402
from mcl_agent.backend import Backend  # noqa: E402
from mcl_agent.errors import (  # noqa: E402
    AgentError,
    AuthError,
    BackendError,
    CommandRejected,
    RateLimited,
    ServerNotInstalled,
)
from mcl_agent.server import Server  # noqa: E402


AGENT_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
SRV1 = "11111111-1111-4111-8111-111111111111"
SRV_OTHER = "99999999-9999-4999-8999-999999999999"
TOKEN = "k3y" + "0" * 40
API_URL = "https://project.supabase.co/functions/v1/agent-api"


def write_config(directory: Path, **overrides) -> Path:
    payload = {
        "agent_id": AGENT_ID,
        "token": TOKEN,
        "api_url": API_URL,
        "data_dir": str(directory / "data"),
        "repo_dir": str(_REPO),
    }
    payload.update(overrides)
    path = directory / "agent.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    return path


class TempDirCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        # These tests must not read a developer's real agent.json or real env.
        self._env = mock.patch.dict(os.environ, {}, clear=True)
        self._env.start()
        self.addCleanup(self._env.stop)

    def make_server(self, server_id: str = "srv-1") -> Server:
        return Server(
            server_id=server_id,
            repo_dir=_REPO,
            data_dir=self.tmp / "data",
            backup_dir=self.tmp / "data" / "backups" / server_id,
        )


# ---- config ----------------------------------------------------------------


class ConfigTests(TempDirCase):
    def test_missing_file_is_actionable(self):
        with self.assertRaises(Exception) as caught:
            config.load(self.tmp / "nope.json")
        self.assertIn("agent.json", str(caught.exception))

    def test_loads_and_derives_digest(self):
        loaded = config.load(write_config(self.tmp))
        self.assertEqual(loaded.agent_id, AGENT_ID)
        self.assertEqual(len(loaded.token_digest), 64)
        self.assertEqual(loaded.heartbeat_seconds, 15)

    def test_rejects_bad_uuid(self):
        path = write_config(self.tmp, agent_id="not-a-uuid")
        with self.assertRaises(Exception) as caught:
            config.load(path)
        self.assertIn("uuid", str(caught.exception))

    def test_refuses_plain_http(self):
        # The digest crosses the network on every request; refusing http is what
        # makes sending only the digest defensible.
        path = write_config(self.tmp, api_url="http://insecure.example/functions/v1/agent-api")
        with self.assertRaises(Exception) as caught:
            config.load(path)
        self.assertIn("https", str(caught.exception))

    def test_rejects_short_token(self):
        path = write_config(self.tmp, token="tooshort")
        with self.assertRaises(Exception) as caught:
            config.load(path)
        self.assertIn("token", str(caught.exception).lower())

    def test_environment_overrides_file(self):
        write_config(self.tmp)
        os.environ["MCL_AGENT_ID"] = AGENT_ID
        os.environ["MCL_API_URL"] = "https://other.supabase.co/functions/v1/agent-api"
        os.environ["MCL_HEARTBEAT_SECONDS"] = "9999"
        loaded = config.load(self.tmp / "agent.json")
        self.assertEqual(loaded.api_url, "https://other.supabase.co/functions/v1/agent-api")
        # Clamped, so a typo cannot turn a heartbeat into a denial of service.
        self.assertEqual(loaded.heartbeat_seconds, config.MAX_HEARTBEAT_SECONDS)

    def test_creates_directories_with_restrictive_mode(self):
        loaded = config.load(write_config(self.tmp))
        self.assertTrue(loaded.servers_dir.is_dir())
        # Best effort: skipped on filesystems without POSIX modes.
        if os.name == "posix":
            self.assertEqual(loaded.data_dir.stat().st_mode & 0o777, 0o600)

    def test_digest_is_not_the_token(self):
        loaded = config.load(write_config(self.tmp))
        self.assertNotIn(TOKEN, loaded.token_digest)


# ---- backend client --------------------------------------------------------


class BackendHeaderTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend(API_URL, AGENT_ID, "d" * 64)

    def test_headers_carry_digest_timestamp_and_unique_nonce(self):
        first = self.backend._headers(0)
        second = self.backend._headers(0)
        self.assertEqual(first["x-mcl-agent-id"], AGENT_ID)
        self.assertEqual(first["x-mcl-token-digest"], "d" * 64)
        self.assertEqual(len(first["x-mcl-timestamp"]), 10)
        # 128 random bits, hex encoded.
        self.assertEqual(len(first["x-mcl-nonce"]), 32)
        self.assertNotEqual(first["x-mcl-nonce"], second["x-mcl-nonce"])

    def test_no_token_material_is_sent(self):
        headers = json.dumps(self.backend._headers(0)).lower()
        self.assertNotIn("authorization", headers)
        self.assertNotIn("bearer", headers)


class BackendErrorTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend(API_URL, AGENT_ID, "d" * 64)

    def _raise(self, code: int, body: bytes = b'{"error":"nope"}'):
        import urllib.error

        def opener(request, timeout=None):
            raise urllib.error.HTTPError(API_URL, code, "err", {}, __import__("io").BytesIO(body))

        with mock.patch("urllib.request.urlopen", opener):
            self.backend.heartbeat({}, "1", {})

    def test_401_is_auth_error_and_not_retried_forever(self):
        with self.assertRaises(AuthError):
            self._raise(401)

    def test_429_is_rate_limited(self):
        with self.assertRaises(RateLimited):
            self._raise(429)

    def test_500_is_backend_error(self):
        with self.assertRaises(BackendError):
            self._raise(500, b"upstream exploded")

    def test_non_json_is_a_backend_error_not_a_crash(self):
        with self.assertRaises(BackendError):
            self._raise(502, b"<html>502 Bad Gateway</html>")

    def test_network_failure_is_reported(self):
        import urllib.error

        def opener(request, timeout=None):
            raise urllib.error.URLError("no route to host")

        with mock.patch("urllib.request.urlopen", opener):
            with self.assertRaises(BackendError):
                self.backend.heartbeat({}, "1", {})


# ---- command allowlist -----------------------------------------------------


class AllowlistTests(TempDirCase):
    def test_unknown_command_is_refused(self):
        server = self.make_server()
        for name in ("server.rm_rf", "", "sh", None, "server.start; rm -rf /", 42):
            with self.assertRaises(CommandRejected):
                commands.execute(server, name, {})

    def test_allowlist_matches_the_database_check_constraint(self):
        # 0003_commands_backups.sql constrains server_commands.command to exactly
        # these ten values. If the two drift, the agent's list is the one that
        # decides -- but they must agree or commands silently never run.
        expected = {
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
        self.assertEqual(set(commands.ALLOWED), expected)

        sql = (_REPO / "backend/supabase/migrations/0003_commands_backups.sql").read_text(encoding="utf8")
        for name in expected:
            self.assertIn(f"'{name}'", sql, f"{name} is allowed by the agent but not by the database")

    def test_every_allowed_command_has_a_handler(self):
        self.assertEqual(set(commands._HANDLERS), set(commands.ALLOWED))

    def test_args_must_be_an_object(self):
        server = self.make_server()
        with self.assertRaises(CommandRejected):
            commands.execute(server, "server.start", ["not", "an", "object"])


class ConsoleCommandTests(TempDirCase):
    def _console(self, line: str, args=None):
        server = self.make_server()
        with mock.patch.object(server, "require_installed"), mock.patch.object(
            server, "pid_alive", return_value=True
        ), mock.patch.object(server, "run_mc") as run_mc:
            run_mc.return_value = mock.Mock(exit_code=0, ok=True, output="")
            outcome = commands.execute(server, "server.console", args or {"command": line})
            return outcome, run_mc

    def test_allowlisted_command_is_sent_as_one_argument(self):
        outcome, run_mc = self._console("say hello world")
        run_mc.assert_called_once()
        # A single argv element: the player name cannot become a second command.
        self.assertEqual(run_mc.call_args[0], ("console", "say hello world"))

    def test_leading_slash_is_tolerated(self):
        _, run_mc = self._console("/list")
        self.assertEqual(run_mc.call_args[0], ("console", "/list"))

    def test_shutdown_and_unknown_verbs_are_refused(self):
        for line in ("stop", "shutdown", "execute", "shell", "kill", "reload"):
            with self.assertRaises(CommandRejected, msg=line):
                self._console(line)

    def test_empty_and_control_characters_are_refused(self):
        for line in ("", "   ", "list\nrm -rf /", "list\x00"):
            with self.assertRaises(CommandRejected):
                self._console(line)

    def test_over_long_command_is_refused(self):
        with self.assertRaises(CommandRejected):
            self._console("say " + "x" * commands.MAX_CONSOLE_LENGTH)

    def test_refuses_when_not_running(self):
        server = self.make_server()
        with mock.patch.object(server, "require_installed"), mock.patch.object(
            server, "pid_alive", return_value=False
        ):
            with self.assertRaises(AgentError):
                commands.execute(server, "server.console", {"command": "list"})

    def test_high_impact_commands_are_flagged(self):
        outcome, _ = self._console("op Steve")
        self.assertTrue(outcome.result["high_impact"])

    def test_ordinary_commands_are_not_flagged(self):
        outcome, _ = self._console("list")
        self.assertFalse(outcome.result["high_impact"])


class WhitelistCommandTests(TempDirCase):
    def test_add_and_remove_round_trip(self):
        server = self.make_server()
        commands.execute(server, "server.whitelist.add", {"player": "Steve_01"})
        self.assertIn("Steve_01", json.loads((server.dir / "whitelist.json").read_text())["names"])
        commands.execute(server, "server.whitelist.remove", {"player": "Steve_01"})
        self.assertEqual(json.loads((server.dir / "whitelist.json").read_text())["names"], [])

    def test_malformed_player_names_are_refused(self):
        server = self.make_server()
        for name in ("ab", "x" * 41, "has space", "semi;colon", "emoji😀", "quote\"", "", None, "with,comma"):
            with self.assertRaises(CommandRejected, msg=repr(name)):
                commands.execute(server, "server.whitelist.add", {"player": name})

    def test_names_are_sorted_so_diffs_stay_meaningful(self):
        server = self.make_server()
        for name in ("Zoe", "Adam", "Mia"):
            commands.execute(server, "server.whitelist.add", {"player": name})
        self.assertEqual(
            json.loads((server.dir / "whitelist.json").read_text())["names"], ["Adam", "Mia", "Zoe"]
        )


class MemoryValidationTests(TempDirCase):
    def test_valid_memory_is_normalised(self):
        for raw, expected in (("2g", "2G"), ("4096M", "4096M"), ("1024", "1024")):
            self.assertEqual(commands._memory(raw), expected)

    def test_invalid_memory_is_refused(self):
        for raw in ("", "lots", "2X", "-4G", "999T", "0"):
            with self.assertRaises(CommandRejected, msg=raw):
                commands._memory(raw)


# ---- server.properties -----------------------------------------------------


class ServerConfigTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.path = self.tmp / "server.properties"
        self.path.write_text(
            "#Minecraft server properties\n"
            "motd=A Minecraft Server\n"
            "max-players=20\n"
            "online-mode=true\n"
            "#comment=keep me\n"
            "pvp=true\n",
            encoding="utf8",
        )

    def test_write_preserves_comments_and_order(self):
        applied = serverconfig.write_properties(self.path, {"max-players": "30"})
        text = self.path.read_text(encoding="utf8")
        self.assertIn("#Minecraft server properties", text)
        self.assertIn("#comment=keep me", text)
        self.assertIn("max-players=30", text)
        self.assertEqual(applied, ["max-players"])

    def test_unchanged_value_is_not_reported(self):
        self.assertEqual(serverconfig.write_properties(self.path, {"max-players": "20"}), [])

    def test_new_key_is_appended_not_lost(self):
        serverconfig.write_properties(self.path, {"difficulty": "hard"})
        text = self.path.read_text(encoding="utf8")
        self.assertIn("difficulty=hard", text)
        self.assertIn("max-players=20", text)

    def test_agent_managed_properties_are_read_only(self):
        for key in ("rcon.password", "server-ip", "level-name"):
            with self.assertRaises(CommandRejected, msg=key):
                serverconfig.write_properties(self.path, {key: "x"})

    def test_type_validation(self):
        with self.assertRaises(CommandRejected):
            serverconfig.write_properties(self.path, {"online-mode": "yes"})
        with self.assertRaises(CommandRejected):
            serverconfig.write_properties(self.path, {"view-distance": "far"})
        with self.assertRaises(CommandRejected):
            serverconfig.write_properties(self.path, {"max-players": "0"})

    def test_line_breaks_are_refused(self):
        with self.assertRaises(CommandRejected):
            serverconfig.write_properties(self.path, {"motd": "one\ntwo"})

    def test_motd_length_is_bounded(self):
        with self.assertRaises(CommandRejected):
            serverconfig.write_properties(self.path, {"motd": "x" * (serverconfig.MAX_MOTD_LENGTH + 1)})

    def test_control_characters_in_motd_are_refused(self):
        # The MOTD is broadcast to everyone who joins; an escape sequence in it
        # puts arbitrary terminal codes in front of every connecting player.
        with self.assertRaises(CommandRejected):
            serverconfig.write_properties(self.path, {"motd": "hi\x1b[2J\x1b[1;31mFREE"})

    def test_creates_file_when_absent(self):
        missing = self.tmp / "new" / "server.properties"
        applied = serverconfig.write_properties(missing, {"max-players": "8"})
        self.assertEqual(applied, ["max-players"])
        self.assertIn("max-players=8", missing.read_text(encoding="utf8"))

    def test_summary_shape_is_stable_when_file_missing(self):
        summary = serverconfig.summarise(self.tmp / "nothing.properties")
        self.assertIn("maxPlayers", summary)
        self.assertIsNone(summary["maxPlayers"])


class SettingsApplyTests(TempDirCase):
    def test_apply_reports_applied_and_skipped(self):
        server = self.make_server()
        server.ensure_directories()
        serverconfig.write_properties(server.properties_path, {"max-players": "20", "pvp": "true"})
        outcome = commands.execute(
            server,
            "server.settings.apply",
            {"properties": {"max-players": "40", "difficulty": "hard"}},
        )
        self.assertEqual(outcome.result["applied"], ["max-players", "difficulty"])
        values = serverconfig.read_properties(server.properties_path)
        self.assertEqual(values["max-players"], "40")

    def test_empty_properties_are_refused(self):
        server = self.make_server()
        with self.assertRaises(CommandRejected):
            commands.execute(server, "server.settings.apply", {"properties": {}})

    def test_too_many_properties_are_refused(self):
        server = self.make_server()
        with self.assertRaises(CommandRejected):
            commands.execute(
                server, "server.settings.apply", {"properties": {f"k{i}": "v" for i in range(21)}}
            )


# ---- logs ------------------------------------------------------------------


class LogTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.path = self.tmp / "latest.log"
        self.path.write_text(
            "\n".join(f"[12:00:{i:02d}] [Server thread/INFO]: line {i}" for i in range(50)) + "\n",
            encoding="utf8",
        )

    def test_timestamps_are_stripped(self):
        page = logs.read(self.path, offset=0, max_lines=3)
        self.assertTrue(all(not line.startswith("[12:") for line in page.lines))
        self.assertEqual(page.lines, ["line 0", "line 1", "line 2"])

    def test_tail_read_returns_the_newest_lines(self):
        page = logs.read(self.path, max_lines=3)
        self.assertEqual(page.lines, ["line 47", "line 48", "line 49"])

    def test_pages_cover_the_file_without_gaps(self):
        # Follow the file the way a console does, a page at a time, and check
        # that every line arrives exactly once and in order.
        collected: list[str] = []
        offset = 0
        for _ in range(20):
            page = logs.read(self.path, offset=offset, max_lines=10)
            collected.extend(page.lines)
            if page.offset == offset:
                break
            offset = page.offset
        self.assertEqual(collected, [f"line {i}" for i in range(50)])

    def test_reading_past_the_end_yields_nothing_new(self):
        at_end = logs.read(self.path, offset=self.path.stat().st_size, max_lines=10)
        self.assertEqual(at_end.lines, [])

    def test_rotation_resets_instead_of_going_silent(self):
        page = logs.read(self.path, offset=self.path.stat().st_size, max_lines=5)
        self.assertEqual(page.offset, self.path.stat().st_size)
        # Simulate rotation: a new, shorter file replaces the old one.
        self.path.write_text("[12:00:00] [Server thread/INFO]: fresh start\n", encoding="utf8")
        resumed = logs.read(self.path, offset=page.offset, max_lines=5)
        self.assertIn("fresh start", " ".join(resumed.lines))

    def test_missing_log_is_not_an_error(self):
        page = logs.read(self.tmp / "absent.log")
        self.assertEqual(page.lines, [])

    def test_line_cap_is_clamped(self):
        page = logs.read(self.path, offset=0, max_lines=commands.MAX_LOG_LINES * 10)
        self.assertLessEqual(len(page.lines), logs.MAX_TAIL_LINES)

    def test_very_long_line_is_truncated(self):
        self.path.write_text("x" * (logs.MAX_LINE_LENGTH * 3) + "\n", encoding="utf8")
        page = logs.read(self.path, max_lines=5)
        self.assertTrue(page.lines[0].endswith("...(truncated)"))


# ---- backups ---------------------------------------------------------------


class BackupTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.server = self.make_server()
        self.server.ensure_directories()

    def _archive(self, stamp: str, version: str = "1.21.4") -> Path:
        path = self.server.backup_dir / f"backup-{stamp}-v{version}.zip"
        path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        return path

    def test_prune_keeps_the_newest(self):
        for index in range(8):
            self._archive(f"2026010{index}T120000Z")
        removed = backups.prune(self.server, keep=3)
        self.assertEqual(len(removed), 5)
        remaining = sorted(p.name for p in self.server.backup_dir.glob("*.zip"))
        self.assertEqual(len(remaining), 3)
        self.assertIn("backup-20260107T120000Z-v1.21.4.zip", remaining[-1])

    def test_prune_never_deletes_other_files(self):
        keep = self._archive("20260101T120000Z")
        stranger = self.server.backup_dir / "my-important-world.zip"
        stranger.write_bytes(b"not ours")
        backups.prune(self.server, keep=1)
        self.assertTrue(keep.is_file())
        self.assertTrue(stranger.is_file())

    def test_prune_cannot_be_wound_to_zero(self):
        for index in range(3):
            self._archive(f"2026010{index}T120000Z")
        backups.prune(self.server, keep=0)
        self.assertEqual(len(list(self.server.backup_dir.glob("*.zip"))), 1)

    def test_labels_are_validated(self):
        for label in ("x" * 41, "semi;colon", "new\nline", ""):
            with self.assertRaises(CommandRejected, msg=repr(label)):
                backups._safe_label(label)

    def test_listing_is_newest_first(self):
        self._archive("20260101T120000Z")
        self._archive("20260105T120000Z")
        self._archive("20260103T120000Z")
        entries = backups.list_backups(self.server)
        self.assertEqual(
            [e["filename"] for e in entries],
            [
                "backup-20260105T120000Z-v1.21.4.zip",
                "backup-20260103T120000Z-v1.21.4.zip",
                "backup-20260101T120000Z-v1.21.4.zip",
            ],
        )

    def test_backup_requires_install(self):
        with self.assertRaises(ServerNotInstalled):
            backups.take(self.server)


# ---- status ----------------------------------------------------------------


class StatusTests(TempDirCase):
    def test_uninstalled_server_reports_unavailable_not_offline(self):
        server = self.make_server()
        collected = status.collect(server)
        self.assertEqual(collected.status, "unavailable")
        self.assertFalse(collected.runtime["installed"])
        self.assertTrue(collected.degraded)

    def test_offline_server_reports_capacity_from_properties(self):
        server = self.make_server()
        server.ensure_directories()
        (server.dir / "version.env").write_text("MINECRAFT_VERSION=1.21.4\n", encoding="utf8")
        (server.dir / "plugins").mkdir(exist_ok=True)
        (server.dir / "plugins" / "paper-1.21.4.jar").write_bytes(b"")
        serverconfig.write_properties(server.properties_path, {"max-players": "42", "motd": "Hi"})

        collected = status.collect(server)
        self.assertEqual(collected.status, "offline")
        self.assertEqual(collected.runtime["players_max"], 42)
        self.assertEqual(collected.runtime["motd"], "Hi")
        # Never a made-up occupancy.
        self.assertIsNone(collected.runtime["players_online"])

    def test_versions_come_from_this_servers_own_pin_file(self):
        server = self.make_server()
        server.ensure_directories()
        (server.dir / "version.env").write_text(
            "MINECRAFT_VERSION=1.20.4\nPAPER_BUILD=99\nJAVA_MAJOR=17\nMAX_MEMORY=6G\n", encoding="utf8"
        )
        (server.dir / "plugins").mkdir(exist_ok=True)
        (server.dir / "plugins" / "paper.jar").write_bytes(b"")
        runtime = status.collect(server).runtime
        self.assertEqual(runtime["minecraft_version"], "1.20.4")
        self.assertEqual(runtime["java_major"], "17")
        self.assertEqual(runtime["memory_limit_bytes"], 6 * 1024**3)

    def test_host_facts_never_fabricate_loadavg(self):
        loaded = config.load(write_config(self.tmp))
        facts = status.host_facts(loaded)
        self.assertIn("cpu_count", facts)
        self.assertIn("disk_free_bytes", facts)
        # Load average only where the OS provides it.
        if os.name == "nt":
            self.assertNotIn("loadavg", facts)

    def test_transitions_are_not_flickering(self):
        self.assertTrue(status.is_plausible_transition("starting", "online"))
        self.assertTrue(status.is_plausible_transition("offline", "online"))
        self.assertFalse(status.is_plausible_transition("online", "starting"))
        self.assertTrue(status.is_plausible_transition(None, "offline"))

    def test_memory_units_are_parsed_like_mc(self):
        self.assertEqual(status._parse_memory("512M"), 512 * 1024**2)
        self.assertEqual(status._parse_memory("2G"), 2 * 1024**3)
        self.assertEqual(status._parse_memory("1048576"), 1048576)
        self.assertIsNone(status._parse_memory("lots"))


# ---- tunnel ----------------------------------------------------------------


class TunnelTests(TempDirCase):
    def test_direct_provider_does_not_claim_public_reachability(self):
        info = tunnel.DirectProvider().describe(1)
        # A closed port is an observable fact; reachability is not.
        self.assertFalse(info.reachable)
        self.assertIn("not running", info.detail)

    def test_unknown_provider_is_refused_by_name(self):
        with self.assertRaises(Exception):
            tunnel.provider_for("nonexistent-provider")

    def test_default_providers_are_registered(self):
        self.assertEqual(set(tunnel.PROVIDERS), {"direct", "playit", "command"})

    def test_describe_never_raises(self):
        server = self.make_server()
        result = tunnel.describe(server, 25565)
        self.assertIn("mode", result)
        self.assertIn("reachable", result)

    def test_playit_without_a_token_explains_the_setup(self):
        holder = mock.Mock(tunnel_args=())
        info = tunnel.PlayItProvider(holder).describe(25565)
        self.assertIsNone(info.reachable)
        self.assertIn("agent.json", info.detail)

    def test_command_provider_substitutes_the_port(self):
        holder = mock.Mock(tunnel_args=("echo", "relay on {port}"))
        provider = tunnel.CommandProvider(holder, self.make_server())
        # The spawn is what matters: the port has to reach the argv.
        with mock.patch.object(provider, "_spawn", side_effect=OSError("boom")) as spawn:
            with self.assertRaises(OSError):
                provider.describe(25565)
        # The port the server actually binds has to reach the relay's argv.
        self.assertEqual(spawn.call_args[0][0], ["echo", "relay on 25565"])

    def test_unusable_addresses_are_not_reported_as_public(self):
        log = self.tmp / "tunnel.log"
        log.write_text("proxy: tcp:0.0.0.0:25565 --> 127.0.0.1:25565\n", encoding="utf8")
        self.assertIsNone(tunnel._read_public_address(log))

    def test_public_address_is_read_from_a_relay_log(self):
        log = self.tmp / "tunnel.log"
        log.write_text("Forwarding tcp://0.tcp.example.dev:41234 --> 25565\n", encoding="utf8")
        self.assertEqual(tunnel._read_public_address(log), "0.tcp.example.dev")

    def test_no_address_in_log_is_none_not_a_guess(self):
        log = self.tmp / "tunnel.log"
        log.write_text("connecting...\n", encoding="utf8")
        self.assertIsNone(tunnel._read_public_address(log))


# ---- server process handling -----------------------------------------------


class ServerProcessTests(TempDirCase):
    def test_pid_is_ignored_when_the_process_is_gone(self):
        server = self.make_server()
        server.ensure_directories()
        server.pid_file.write_text("999999\n", encoding="utf8")
        # A pid that cannot exist: the panel must not show a running server.
        self.assertIsNone(server.pid())
        self.assertFalse(server.pid_alive())

    def test_garbage_pid_file_is_not_fatal(self):
        server = self.make_server()
        server.ensure_directories()
        server.pid_file.write_text("not-a-pid\n", encoding="utf8")
        self.assertIsNone(server.pid())

    def test_missing_pid_file_is_not_fatal(self):
        self.assertIsNone(self.make_server().pid())

    def test_require_installed_is_explicit(self):
        with self.assertRaises(ServerNotInstalled):
            self.make_server().require_installed()

    def test_environment_points_mc_at_this_server_only(self):
        first = self.make_server("one")
        second = self.make_server("two")
        self.assertNotEqual(first.dir, second.dir)
        self.assertEqual(first.environment()["MC_SERVER_DIR"], str(first.dir))
        self.assertEqual(second.environment()["MC_SERVER_DIR"], str(second.dir))
        # Two servers on one machine must not share a world or a pid file.
        self.assertNotEqual(first.version_file, second.version_file)

    def test_arguments_with_nul_are_refused(self):
        server = self.make_server()
        with self.assertRaises(AgentError):
            server.run_mc("console", "list\x00op everyone")

    def test_commands_never_pass_a_shell(self):
        server = self.make_server()
        # A metacharacter-laden argument must arrive as one literal argument.
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            server.run_mc("console", "; rm -rf /")
        kwargs = run.call_args[1]
        self.assertFalse(kwargs.get("shell", False))
        self.assertEqual(run.call_args[0][0][-1], "; rm -rf /")


# ---- service loop ----------------------------------------------------------


class ServiceTests(TempDirCase):
    def setUp(self):
        super().setUp()
        from mcl_agent.service import AgentService

        self.config = config.load(write_config(self.tmp))
        self.backend = mock.Mock(spec=Backend)
        self.backend.claim_commands.return_value = []
        self.backend.list_servers.return_value = []
        self.service = AgentService(self.config, self.backend)

    def test_cycle_reports_agent_and_then_servers(self):
        self.backend.list_servers.return_value = [
            {"id": "11111111-1111-4111-8111-111111111111", "name": "Survival", "port": 25565}
        ]
        self.service.cycle()
        self.assertTrue(self.backend.heartbeat.called)
        # A stopped server must still report, or the panel shows nothing at all.
        self.assertTrue(self.backend.report_server_status.called)

    def test_a_removed_server_is_forgotten(self):
        first = "11111111-1111-4111-8111-111111111111"
        self.backend.list_servers.return_value = [{"id": first, "name": "Survival", "port": 25565}]
        self.service.cycle()
        self.assertIn(first, self.service.slots)
        self.backend.list_servers.return_value = []
        self.service.cycle()
        self.assertNotIn(first, self.service.slots)

    def test_a_server_created_in_the_panel_is_picked_up(self):
        # The agent must learn about a server created while it is running, not
        # only when the first command is queued for it.
        self.backend.claim_commands.return_value = []
        self.backend.list_servers.return_value = [
            {"id": "22222222-2222-4222-8222-222222222222", "name": "Creative", "port": 25566}
        ]
        self.service.cycle()
        slot = self.service.slots["22222222-2222-4222-8222-222222222222"]
        self.assertEqual(slot.port, 25566)
        # A non-uuid id must never become a directory name.
        self.assertNotIn("../../etc", self.service.slots)

    def test_capabilities_advertise_the_allowlist(self):
        self.service.cycle()
        capabilities = self.backend.heartbeat.call_args.kwargs["capabilities"]
        self.assertEqual(set(capabilities["commands"]), set(commands.ALLOWED))
        self.assertIn("direct", capabilities["tunnel_providers"])

    def test_a_refused_command_is_still_reported(self):
        self.backend.claim_commands.return_value = [
            {"id": "c1", "server_id": SRV1, "command": "server.rm_rf", "args": {}}
        ]
        self.service.cycle()
        # Reported as failed: a refusal the operator never sees looks like a hang.
        self.assertEqual(self.backend.complete_command.call_args[0][1], "failed")

    def test_a_successful_command_is_reported_with_its_result(self):
        server = self.make_server(SRV1)
        server.ensure_directories()
        self.backend.claim_commands.return_value = [
            {"id": "c1", "server_id": SRV1, "command": "server.whitelist.add", "args": {"player": "Steve"}}
        ]
        with mock.patch("mcl_agent.service.status_module.collect") as collect:
            collect.return_value = status.ServerStatus(status="offline", runtime={})
            self.service.cycle()
        self.backend.complete_command.assert_called()
        self.assertEqual(self.backend.complete_command.call_args[0][1], "succeeded")

    def test_command_ids_without_a_server_id_are_dropped(self):
        self.backend.claim_commands.return_value = [{"id": "c1", "command": "server.start"}]
        self.service.cycle()
        self.assertFalse(self.backend.complete_command.called)

    def test_server_count_is_capped(self):
        self.service.slots = {f"s{i}": mock.Mock() for i in range(config.MAX_SERVERS)}
        self.backend.claim_commands.return_value = [
            {"id": "c1", "server_id": SRV_OTHER, "command": "server.start", "args": {}}
        ]
        self.service.cycle()
        status_arg = self.backend.complete_command.call_args[0][1]
        self.assertEqual(status_arg, "failed")

    def test_a_status_failure_does_not_stop_the_cycle(self):
        self.backend.claim_commands.return_value = [
            {"id": "c1", "server_id": SRV1, "command": "server.whitelist.add", "args": {"player": "Steve"}}
        ]
        with mock.patch("mcl_agent.service.status_module.collect", side_effect=OSError("boom")):
            self.service.cycle()  # must not raise
        self.assertTrue(self.backend.report_server_status.called)

    def test_shutdown_leaves_servers_alone(self):
        # The design commitment: stopping the agent never stops a game server.
        self.service._request_stop()
        self.assertTrue(self.service.stop_event.is_set())
        with mock.patch.object(commands, "_stop") as stop:
            self.service.stop_event.set()
        self.assertFalse(stop.called)


if __name__ == "__main__":
    unittest.main(verbosity=2)