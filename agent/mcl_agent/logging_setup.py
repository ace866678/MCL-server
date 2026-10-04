"""Logging to stderr and to a rotating file, plus the operational event log.

Two sinks with different jobs:

* stderr — what a systemd journal or a terminal shows. The agent is started by a
  service manager, so nothing here is written to stdout where a caller might
  mistake it for protocol output.
* a rotating file — the only record of what happened while the agent was down.
  Console and server logs are separate; this is the agent's own history.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOGGER_NAME = "mcl-agent"

_CONFIGURED = False


def configure(log_level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    """Idempotent: safe to call from tests and from `__main__` alike."""
    global _CONFIGURED

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    if _CONFIGURED:
        return logger

    level = getattr(logging, str(log_level).upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")

    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(level)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            rotating = logging.handlers.RotatingFileHandler(
                log_dir / "agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf8"
            )
            rotating.setLevel(level)
            rotating.setFormatter(fmt)
            logger.addHandler(rotating)
        except OSError as exc:
            # A read-only log directory must not stop the agent from running.
            logger.warning("cannot open agent.log in %s: %s", log_dir, exc)

    # Nothing in this agent is worth a stack trace twice over, but a traceback in
    # the local log is the difference between debuggable and not.
    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger(suffix: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if not suffix else f"{LOGGER_NAME}.{suffix}")