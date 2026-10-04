"""World backups.

`mc backup` already does the part that has to be right: it asks the server to
save, waits for the world to stop being written, zips the world directory and
verifies the archive it produced. This module adds the two things a hosted
service needs on top of that and that a one-machine script has no reason to have:

* the RCON save-and-disable step, so the world on disk is consistent even when
  the console is unavailable, and
* retention, because a self-hosted agent has no cron on the user's behalf.

The agent never deletes anything it did not create: pruning only removes archives
matching this agent's own naming scheme, and it never removes the newest one.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import AgentError
from .server import Server

# Archive names the agent writes. Anchored and non-greedy over a fixed field
# set, so pruning cannot be pointed at some other file that happens to be a zip.
BACKUP_NAME = re.compile(r"^backup-(?P<stamp>\d{8}T\d{6}Z)-v(?P<version>[0-9A-Za-z.+_-]{1,40})\.zip$")

DEFAULT_KEEP = 5
MAX_KEEP = 20

# A world zip for a large server is hundreds of megabytes; hashing it is quick
# next to writing it, and it is what lets the panel show the archive is intact.
HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class BackupResult:
    filename: str
    size_bytes: int
    sha256: str | None


def take(server: Server, label: str = "manual") -> BackupResult:
    """Create a backup and return what the panel should record.

    `mc backup` is the only thing that touches the world directory. The label is
    not passed through: it is validated to a safe character set and appended to
    nothing, because it only ever becomes a log line.
    """
    server.require_installed()
    _safe_label(label)

    server.ensure_directories()
    # Ask the server to flush and hold still first. If RCON is unavailable this
    # raises and the caller falls back to letting `mc backup` do its own save.
    if server.pid_alive():
        _request_save(server)

    # --yes because there is no terminal here to answer the prompt, and the
    # dashboard has already asked a human to confirm.
    result = server.run_mc("backup", "--yes", timeout=1800)
    if not result.ok:
        raise AgentError(f"backup failed: {result.output.strip()[-400:]}")

    archive = newest_archive(server.backup_dir)
    if archive is None:
        raise AgentError("`mc backup` reported success but wrote no archive")

    return BackupResult(
        filename=archive.name,
        size_bytes=archive.stat().st_size,
        sha256=sha256_file(archive),
    )


def prune(server: Server, keep: int = DEFAULT_KEEP) -> list[str]:
    """Delete this agent's oldest backups, keeping the newest `keep`.

    Only names that match `BACKUP_NAME` are considered, the newest is never
    removed, and `keep` is clamped so a panel bug cannot empty the directory.
    """
    try:
        retained = max(1, min(int(keep), MAX_KEEP))
    except (TypeError, ValueError):
        retained = DEFAULT_KEEP

    archives = sorted(
        (p for p in server.backup_dir.glob("*.zip") if BACKUP_NAME.match(p.name)),
        key=_stamp_of,
    )
    doomed = archives[:-retained] if len(archives) > retained else []
    removed: list[str] = []
    for archive in doomed:
        try:
            archive.unlink()
            removed.append(archive.name)
        except OSError:
            # A file owned by another user, or one already gone. Either way the
            # next prune will see it again; nothing else depends on this.
            continue
    return removed


def list_backups(server: Server, limit: int = 20) -> list[dict[str, object]]:
    """What the panel shows: name, size, and when, newest first."""
    archives = sorted(
        (p for p in server.backup_dir.glob("*.zip") if BACKUP_NAME.match(p.name)),
        key=_stamp_of,
        reverse=True,
    )
    entries: list[dict[str, object]] = []
    for archive in archives[: max(1, min(int(limit), MAX_KEEP * 4))]:
        try:
            info = archive.stat()
        except OSError:
            continue
        entries.append(
            {"filename": archive.name, "size_bytes": info.st_size, "created_at": _stamp_of(archive)}
        )
    return entries


def newest_archive(backup_dir: Path) -> Path | None:
    archives = [p for p in backup_dir.glob("*.zip") if BACKUP_NAME.match(p.name)]
    return max(archives, key=_stamp_of) if archives else None


def _stamp_of(path: Path) -> str:
    match = BACKUP_NAME.match(path.name)
    return match.group("stamp") if match else ""


def _request_save(server: Server) -> None:
    """`save-all flush` then `save-off`, so nothing writes during the zip.

    `save-off` is undone in a finally block: leaving a server told not to save is
    a data-loss bug, and it must not depend on the zip succeeding.
    """
    from .rcon import for_server

    try:
        with for_server(server) as rcon:
            rcon.execute("save-all flush")
            rcon.execute("save-off")
    except Exception:
        # Not fatal. `mc backup` performs its own save-and-hold, so a missing or
        # wrong RCON password degrades safety margins, not correctness.
        return

    try:
        _settle()
    finally:
        try:
            with for_server(server) as rcon:
                rcon.execute("save-on")
        except Exception:
            pass


def _settle(seconds: float = 3.0) -> None:
    """Brief pause so the flush reaches disk before the world directory is read."""
    time.sleep(seconds)


def _safe_label(label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9 _.-]{1,40}", label or ""):
        from .errors import CommandRejected

        raise CommandRejected("a backup label may only contain letters, digits, spaces, . _ and -")
    return label


def sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(HASH_CHUNK), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None