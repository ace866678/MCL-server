#!/usr/bin/env python3
"""
Local Minecraft Server Agent.

The agent makes outbound HTTPS requests only. It never exposes shell execution.
Commands are a small explicit allowlist and are authorized per server by the API.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import secrets
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

LOG = logging.getLogger("mcl-agent")
ALLOWED_COMMANDS = {"start", "stop", "restart", "backup", "status", "logs"}

def required_env(name: str) -> str:
    """Fail with the variable to fix, not a bare KeyError traceback."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"mcl-agent: {name} is not set (see the agent setup steps)")
    return value

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def new_token() -> str:
    return secrets.token_urlsafe(32)

class ServerAgent:
    def __init__(self) -> None:
        self.agent_id = required_env("MCL_AGENT_ID")
        self.token = required_env("MCL_AGENT_TOKEN")
        self.api_url = required_env("MCL_API_URL").rstrip("/")
        self.server_dir = Path(os.environ.get("MCL_SERVER_DIR", "server")).resolve()
        self.mc_script = Path(os.environ.get("MCL_MC_SCRIPT", "mc")).resolve()
        self.stop_event = threading.Event()

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self.api_url}{path}", data=body, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())

    def _run(self, command: str) -> tuple[int, str]:
        if command not in ALLOWED_COMMANDS:
            raise ValueError("command is not allowed")
        args = [str(self.mc_script), command]
        if command == "logs": args += ["50"]
        completed = subprocess.run(args, cwd=self.server_dir.parent, capture_output=True, text=True, timeout=180)
        return completed.returncode, (completed.stdout + completed.stderr)[-12000:]

    def status(self) -> dict:
        code, output = self._run("status")
        return {"ok": code == 0, "output": output, "platform": platform.system().lower(), "agent_version": "1"}

    def poll(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._request("POST", f"/api/agents/{self.agent_id}/heartbeat", {"status": self.status()})
                for job in self._request("GET", f"/api/agents/{self.agent_id}/commands").get("commands", []):
                    command = job.get("command", "")
                    try:
                        code, output = self._run(command)
                        result = {"ok": code == 0, "output": output}
                    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                        result = {"ok": False, "output": str(exc)}
                    self._request("POST", f"/api/agents/{self.agent_id}/commands/{job['id']}/result", result)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                LOG.warning("backend unavailable: %s", exc)
            self.stop_event.wait(10)

    def run(self) -> None:
        LOG.info("agent %s running on %s", self.agent_id, platform.system())
        self.poll()

if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("MCL_LOG_LEVEL", "INFO"))
    agent = ServerAgent()
    signal.signal(signal.SIGTERM, lambda *_: agent.stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: agent.stop_event.set())
    agent.run()

__all__ = ["ServerAgent", "sha256_file", "new_token"]
