"""Reading `logs/latest.log`.

Paper rotates `latest.log` on restart, so a byte offset alone is not enough to
resume a tail: if the file shrank or was replaced, continuing from the old offset
would show nothing until the new file grew past it. `Tail` tracks the inode and
size together and resets when either says the file changed underneath it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Paper's own timestamp and logger prefix: `[12:00:00] [Server thread/INFO]: `.
# Note the colon after the logger name -- requiring whitespace there would stop
# the pattern from ever matching a real Paper line. Stripped before display
# because the dashboard renders lines in arrival order, and a duplicated
# timestamp reads as a bug.
_TIMESTAMP = re.compile(r"^\[\d{2}:\d{2}:\d{2}\][^\n]*?:\s?")

# Longest single line kept. A stack trace element or a modded log line can be
# enormous; the panel renders it in a fixed-width box.
MAX_LINE_LENGTH = 2_000

DEFAULT_TAIL_LINES = 200
MAX_TAIL_LINES = 1_000

# Sentinel offset meaning "the end of the file". Distinct from 0, which means
# "the beginning": conflating them made a follow-along tail either repeat the
# whole file or skip straight past the new lines.
TAIL = -1


@dataclass(frozen=True)
class LogPage:
    lines: list[str]
    offset: int
    more_available: bool

    def as_dict(self) -> dict[str, object]:
        return {"lines": self.lines, "offset": self.offset, "more_available": self.more_available}


def read(path: Path, offset: int = TAIL, max_lines: int = DEFAULT_TAIL_LINES) -> LogPage:
    """Return up to `max_lines` lines from `offset`, and the next offset.

    `offset=TAIL` (-1) means "the last N lines", which is what the dashboard asks
    for on first load. `offset=0` means "from the start of the file". Any other
    value is a byte position from a previous call, which is how the console
    follows along.

    Paging never skips or repeats: the returned offset is always the byte
    immediately after the last line handed out, so successive pages tile the file.
    """
    limit = max(1, min(int(max_lines), MAX_TAIL_LINES))

    if not path.is_file():
        return LogPage(lines=[], offset=max(0, offset), more_available=False)

    try:
        size = path.stat().st_size
    except OSError:
        return LogPage(lines=[], offset=max(0, offset), more_available=False)

    from_tail = offset == TAIL
    if from_tail:
        start = max(0, size - _tail_window(path, size, limit))
    elif offset > size:
        # The file was rotated or truncated under us, so the old offset points
        # past the end of a different file. Reading from 0 shows the new file
        # rather than going silent until it grows back past a meaningless spot.
        start = 0
    else:
        start = max(0, offset)

    try:
        with path.open("rb") as handle:
            handle.seek(start)
            chunk = handle.read(MAX_TAIL_LINES * MAX_LINE_LENGTH)
    except OSError:
        return LogPage(lines=[], offset=size, more_available=False)

    text = chunk.decode("utf8", "replace")

    # Cut on a line boundary: a partial trailing line is normal while a server
    # is writing, and showing half of one is worse than showing one fewer.
    parts = text.split("\n")
    trailing_partial = parts.pop() if not text.endswith("\n") else None
    lines = [_clean(part) for part in parts if part.strip()]
    more = len(lines) > limit or trailing_partial is not None

    if from_tail:
        # The window is sized to contain about `limit` lines, so anything over the
        # cap is trimmed off the front and the offset rewinds to where the
        # returned page begins.
        if len(lines) > limit:
            drop = len(lines) - limit
            lines = lines[drop:]
            start = _offset_after_lines(path, start, drop)
        consumed = min(start + len(chunk), size)
    else:
        # Following from an explicit offset: hand out the *first* `limit` lines
        # and stop at their boundary, so the next page continues exactly where
        # this one ended instead of jumping to the end of the file.
        if len(lines) > limit:
            lines = lines[:limit]
            consumed = _offset_after_lines(path, start, limit)
        else:
            consumed = min(start + len(chunk), size)

    return LogPage(lines=lines, offset=consumed, more_available=more)


def _tail_window(path: Path, size: int, limit: int) -> int:
    """How far back to read to get about `limit` lines, without loading the file."""
    window = 64 * 1024
    while window <= max(size, 1):
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, size - window))
                if handle.read().count(b"\n") >= limit:
                    return window
        except OSError:
            break
        window *= 4
    return size


def _offset_after_lines(path: Path, start: int, drop: int) -> int:
    """Byte offset of the `drop`-th newline at or after `start`.

    Used only when the line cap trims a page, so the returned offset lines up with
    a line boundary instead of landing mid-line.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(start)
            block = handle.read(min(size - start, MAX_TAIL_LINES * MAX_LINE_LENGTH))
    except OSError:
        return start

    seen = 0
    for index, byte in enumerate(block):
        if byte == 0x0A:
            seen += 1
            if seen == drop:
                return start + index + 1
    return start


def _clean(line: str) -> str:
    line = _TIMESTAMP.sub("", line)
    if len(line) > MAX_LINE_LENGTH:
        line = line[:MAX_LINE_LENGTH] + "...(truncated)"
    return line