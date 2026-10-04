#!/usr/bin/env python3
"""Local Minecraft Server Agent — entry point.

Kept as a file so the documented `python3 agent/agent.py` works, and so the
systemd unit has something stable to point `ExecStart` at. All the behaviour is in
`mcl_agent/`; this only adds the package directory to `sys.path` so the agent runs
from a plain checkout with no install step and no PYTHONPATH in the unit file.

The agent makes outbound HTTPS requests only. It never opens a listening socket,
so hosting a server here needs no port forwarding and no inbound firewall rule
for management.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The repository's lib/ holds the RCON implementation shared with bridge/.
# Both directories go on the path here so `python3 agent/agent.py` works from a
# fresh clone.
_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parent / "lib"):
    if _path.is_dir() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mcl_agent.__main__ import main  # noqa: E402

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())