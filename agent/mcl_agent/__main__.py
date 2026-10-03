"""`python -m mcl_agent` — the agent entry point.

Configuration problems are reported as a single actionable line and exit 2. That
matters because this is what a first-time operator runs: a stack trace out of
`config.py` would send them looking for a bug rather than for the one field they
need to fill in.
"""

from __future__ import annotations

import sys

from . import config as config_module
from .errors import ConfigError
from .logging_setup import configure

EXIT_OK = 0
EXIT_CONFIG = 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] in ("-h", "--help", "help"):
        print(_usage())
        return EXIT_OK

    # A logger exists before the config is read so a config failure is itself
    # logged rather than printed into the void.
    logger = configure()

    try:
        config = config_module.load()
    except ConfigError as exc:
        logger.error("%s", exc)
        return EXIT_CONFIG

    # Reconfigure now that the log directory is known.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    configure(config.log_level, config.data_dir / "logs")

    if argv and argv[0] == "check":
        return _check(config)

    from .backend import Backend
    from .service import AgentService

    AgentService(config, Backend(config.api_url, config.agent_id, config.token_digest)).run()
    return EXIT_OK


def _check(config) -> int:
    """Validate the configuration and report reachability, then exit.

    Useful before wiring the agent into a service manager: it catches a wrong
    token or an unreachable backend immediately, instead of on the first silent
    poll cycle.
    """
    from .backend import Backend
    from .errors import AuthError, BackendError

    print(f"agent id   {config.agent_id}")
    print(f"backend    {config.api_url}")
    print(f"data dir   {config.data_dir}")
    print(f"repo dir   {config.repo_dir}")
    print(f"heartbeat  every {config.heartbeat_seconds}s")
    print(f"tunnel     {config.tunnel_provider}")

    try:
        Backend(config.api_url, config.agent_id, config.token_digest).heartbeat(
            status={"probe": True}, version="check", capabilities={}
        )
    except AuthError as exc:
        print(f"\nrejected by the backend: {exc}")
        return EXIT_CONFIG
    except BackendError as exc:
        print(f"\ncannot reach the backend: {exc}")
        return EXIT_CONFIG

    print("\nbackend accepted this agent.")
    return EXIT_OK


def _usage() -> str:
    return """mcl-agent — runs your Minecraft servers and reports to the cloud panel.

usage:
  python -m mcl_agent           run the agent (Ctrl-C or SIGTERM to stop)
  python -m mcl_agent check     verify the configuration and the backend, then exit

configuration:
  agent/agent.json, gitignored, holding the agent id and the one-time token
  shown by the dashboard. Environment variables MCL_AGENT_ID, MCL_AGENT_TOKEN,
  MCL_API_URL, MCL_DATA_DIR, MCL_REPO_DIR and MCL_HEARTBEAT_SECONDS override it,
  which is what the systemd unit does.

Stopping the agent does not stop your servers. They are started detached and
survive an agent restart, so a panel reload never takes a world offline.
"""


if __name__ == "__main__":
    raise SystemExit(main())