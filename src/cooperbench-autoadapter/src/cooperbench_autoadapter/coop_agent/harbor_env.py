"""Synchronous mini-swe-agent environment that runs commands in a Harbor compose service.

The original CooperBench harness runs each agent loop on the host and executes every bash
action with ``docker exec -w /workspace/repo -e K=V <container> bash -lc <command>``
(``mini_swe_agent_v2/environments/docker.py``). This class keeps that contract — same
config fields, same ``execute()`` result shape, same completion sentinel — and only replaces
the transport: the command goes through Harbor's ``environment.exec(service=...)`` into the
agent's own compose service (``agent1`` / ``agent2``).

The agent loop is synchronous and runs in a worker thread; ``execute()`` schedules the
coroutine on the trial's event loop and waits for it.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import threading
from typing import Any

from pydantic import BaseModel

from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.exceptions import Submitted
from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.utils.serialize import (
    recursive_merge,
)


class AgentStopped(RuntimeError):
    """Raised inside the agent thread once the trial asked the agents to stop."""


class HarborServiceEnvironmentConfig(BaseModel):
    """Field-for-field the original ``DockerEnvironmentConfig`` (rendered into templates and the
    saved trajectory), plus the compose service that replaces the container id."""

    image: str
    cwd: str = "/"
    env: dict[str, str] = {}
    forward_env: list[str] = []
    timeout: int = 30
    executable: str = "docker"
    run_args: list[str] = ["--rm"]
    network: str | None = None
    container_timeout: str = "2h"
    pull_timeout: int = 120
    interpreter: list[str] = ["bash", "-lc"]
    service: str = "agent1"


class HarborServiceEnvironment:
    def __init__(
        self,
        *,
        harbor_env: Any,
        loop: asyncio.AbstractEventLoop,
        stop_event: threading.Event,
        uname: dict[str, str],
        logger: logging.Logger | None = None,
        **kwargs,
    ):
        self.logger = logger or logging.getLogger("mini_swe_agent_v2.environment")
        self.config = HarborServiceEnvironmentConfig(**kwargs)
        self._harbor_env = harbor_env
        self._loop = loop
        self._stop = stop_event
        # ``platform.uname()`` in the original is the machine that runs the agent's containers
        # (the docker host). Here that machine is the one behind the compose service, so its
        # kernel identity is read from inside the service once at setup.
        self._uname = uname

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return recursive_merge(self.config.model_dump(), self._uname, kwargs)

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "environment": self.config.model_dump(mode="json"),
                    "environment_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                }
            }
        }

    def _run(self, command: str, cwd: str, timeout: int) -> tuple[str, int]:
        if self._stop.is_set():
            raise AgentStopped("the trial stopped this agent")
        # Same shell as the original (``bash -lc``), stdin closed (the original passes
        # stdin=DEVNULL), and stderr merged into stdout in order (stderr=STDOUT).
        shell = " ".join(shlex.quote(p) for p in self.config.interpreter)
        wrapped = f"{shell} {shlex.quote(command)} </dev/null 2>&1"
        future = asyncio.run_coroutine_threadsafe(
            self._harbor_env.service_exec(
                wrapped,
                service=self.config.service,
                cwd=cwd,
                env=dict(self.config.env) or None,
                timeout_sec=timeout,
            ),
            self._loop,
        )
        try:
            result = future.result(timeout=timeout + 120)
        except BaseException:
            future.cancel()
            raise
        output = (result.stdout or "") + (result.stderr or "")
        return output, result.return_code

    def execute(
        self, action: dict, cwd: str = "", *, timeout: int | None = None
    ) -> dict[str, Any]:
        """Execute a command in the agent's service and return the result as a dict."""
        command = action.get("command", "")
        cwd = cwd or self.config.cwd  # original: `docker exec -w <cwd>`
        try:
            out, rc = self._run(command, cwd, timeout or self.config.timeout)
            output = {"output": out, "returncode": rc, "exception_info": ""}
        except AgentStopped:
            raise
        except Exception as e:  # noqa: BLE001 - same catch-all as the original DockerEnvironment
            output = {
                "output": "",
                "returncode": -1,
                "exception_info": f"An error occurred while executing the command: {e}",
                "extra": {"exception_type": type(e).__name__, "exception": str(e)},
            }
        self._check_finished(output)
        return output

    def _check_finished(self, output: dict):
        """Raises Submitted if the output indicates task completion (unchanged from the original)."""
        lines = output.get("output", "").lstrip().splitlines(keepends=True)
        if (
            lines
            and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            and output["returncode"] == 0
        ):
            submission = "".join(lines[1:])
            raise Submitted(
                {
                    "role": "exit",
                    "content": submission,
                    "extra": {"exit_status": "Submitted", "submission": submission},
                }
            )

    def cleanup(self):
        """Containers belong to the Harbor trial; Harbor tears them down."""
