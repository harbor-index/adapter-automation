"""Harbor agent that runs the original CooperBench coop setting inside one Harbor trial.

What the original does for one feature pair (``cooperbench run --setting coop
-a mini_swe_agent_v2``; ``runner/coop.py`` + ``agents/mini_swe_agent_v2/adapter.py``):

* two threads, one per feature (``agent1`` = smaller feature id, ``agent2`` = larger);
* each thread starts its own container from the task image, connects a Redis mailbox
  (``MessagingConnector``, namespace ``#run:<id>``), configures git with a local bare remote
  and the ``gh`` shim (``GitConnector``), and runs ``DefaultAgent`` with ``coop.yaml`` on its
  own ``feature.md``;
* each agent's graded patch is the diff of the PR branch it pushed
  (``GitConnector.submitted_patch`` + ``normalize_patch``).

This class runs that same code (vendored unchanged in ``mini_swe_agent_v2``). Only the
transport differs: commands go to the trial's ``agent1`` / ``agent2`` compose services through
Harbor (``HarborServiceEnvironment``), and the Redis server is an in-process ``fakeredis``
server shared by the two agents of the trial. The two patches are written to
``/opt/task/submission/`` in the ``main`` service; Harbor carries that folder into a fresh
verifier environment, which grades them with the original ``test_merged``.

The pair's metadata (repo, task id, feature ids) comes from the task's ``environment/pair.json``,
read on the Harbor host; it is never inside a container and not part of ``instruction.md``.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import fakeredis
import yaml

from cooperbench_autoadapter.coop_agent.harbor_env import (
    AgentStopped,
    HarborServiceEnvironment,
)
from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.agents.default import (
    DefaultAgent,
)
from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.config import (
    builtin_config_dir,
)
from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.connectors import GitConnector
from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.connectors.messaging import (
    MessagingConnector,
)
from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.models.litellm_model import (
    LitellmModel,
)
from cooperbench_autoadapter.pair_instruction import parse as parse_instruction

try:  # Harbor is provided by the harness at run time.
    from harbor.agents.base import BaseAgent
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext
except (
    ImportError
):  # pragma: no cover - lets the adapter CLI import this package without Harbor
    BaseAgent = object  # type: ignore[assignment,misc]
    BaseEnvironment = Any  # type: ignore[assignment,misc]
    AgentContext = Any  # type: ignore[assignment,misc]

CODE_VERSION = "cooperbench@63b9d44"
SUBMISSION_DIR = "/opt/task/submission"


def normalize_patch(text: str) -> str:
    """Copy of ``cooperbench.agents._coop.runtime.normalize_patch``."""
    if not text or not text.strip():
        return ""
    return text.lstrip("\n").rstrip("\n") + "\n"


class _UsageTrackingModel(LitellmModel):
    """``LitellmModel`` that also sums token usage for Harbor's context (no behavior change)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.usage = {"input": 0, "output": 0, "cache_read": 0}
        self._lock = threading.Lock()

    def _calculate_cost(self, response):  # called once per successful completion
        try:
            u = getattr(response, "usage", None)
            if u is not None:
                details = getattr(u, "prompt_tokens_details", None)
                with self._lock:
                    self.usage["input"] += int(getattr(u, "prompt_tokens", 0) or 0)
                    self.usage["output"] += int(getattr(u, "completion_tokens", 0) or 0)
                    self.usage["cache_read"] += (
                        int(getattr(details, "cached_tokens", 0) or 0) if details else 0
                    )
        except Exception:  # noqa: BLE001, S110 - accounting must never break the run
            pass
        return super()._calculate_cost(response)


def _extract_conversation(results: dict, agents: list[str]) -> list[dict]:
    """Copy of ``cooperbench.runner.coop._extract_conversation`` (mini_swe_agent branches)."""
    conversation = []
    for agent_id in agents:
        r = results[agent_id]
        fid = r["feature_id"]
        for sent_msg in r.get("sent_messages", []):
            conversation.append(
                {
                    "from": agent_id,
                    "to": sent_msg.get("to", sent_msg.get("recipient")),
                    "message": sent_msg.get("message", sent_msg.get("content", "")),
                    "timestamp": sent_msg.get("timestamp"),
                    "feature_id": fid,
                }
            )
        for msg in r.get("messages", []):
            content = msg.get("content", "") or ""
            ts = msg.get("timestamp")
            if msg.get("role") == "assistant" and "send_message" in content:
                match = re.search(r'send_message\s+(\w+)\s+"([^"]+)"', content)
                if match:
                    to_agent, message = match.groups()
                    conversation.append(
                        {
                            "from": agent_id,
                            "to": to_agent,
                            "message": message,
                            "timestamp": ts,
                            "feature_id": fid,
                        }
                    )
            if msg.get("role") == "user" and "[Message from" in content:
                match = re.search(r"\[Message from (\w+)\]:\s*(.+)", content)
                if match:
                    from_agent, message = match.groups()
                    conversation.append(
                        {
                            "from": from_agent,
                            "to": agent_id,
                            "message": message.strip(),
                            "timestamp": ts,
                            "feature_id": fid,
                            "received": True,
                        }
                    )
    return conversation


def _ts_key(msg: dict) -> float:
    try:
        return float(msg.get("timestamp") or 0.0)
    except (TypeError, ValueError):
        return 0.0


class CooperBenchCoopAgent(BaseAgent):
    """Two cooperating mini-swe-agent v2 agents, one per feature (CooperBench coop setting).

    Agent kwargs (``--ak``):
        agent_config: optional YAML whose ``config:`` block is deep-merged into ``coop.yaml``
            (the original ``--agent-config``).
        messaging_enabled: default True (the original default).
    """

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        agent_config: str | None = None,
        messaging_enabled: bool | str = True,
        *args,
        **kwargs,
    ):
        super().__init__(logs_dir, model_name, *args, **kwargs)
        self._agent_config = agent_config
        self._messaging_enabled = str(messaging_enabled).lower() not in (
            "0",
            "false",
            "no",
        )
        self._stop = threading.Event()

    @staticmethod
    def name() -> str:
        return "cooperbench-coop-mini-swe-agent-v2"

    def version(self) -> str | None:
        return CODE_VERSION

    async def setup(self, environment: BaseEnvironment) -> None:
        # Nothing to install: the agent loops run in this process; the containers only
        # execute bash commands, exactly as in the original harness.
        return None

    # ------------------------------------------------------------------ helpers

    async def _service_uname(
        self, environment: BaseEnvironment, service: str
    ) -> dict[str, str]:
        res = await environment.service_exec(
            "uname -s; uname -n; uname -r; uname -v; uname -m",
            service=service,
            timeout_sec=60,
        )
        lines = (res.stdout or "").splitlines()
        keys = ["system", "node", "release", "version", "machine"]
        if res.return_code != 0 or len(lines) < 5:
            raise RuntimeError(
                f"cannot read uname in service {service}: {res.stdout} {res.stderr}"
            )
        return dict(zip(keys, (ln.strip() for ln in lines[:5])))

    async def _write_main(
        self, environment: BaseEnvironment, path: str, content: str
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            local = Path(tmp) / Path(path).name
            local.write_text(content)
            await environment.upload_file(local, path)

    def _load_config(self) -> dict:
        from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.utils.serialize import (
            recursive_merge,
        )

        with open(builtin_config_dir / "coop.yaml") as f:
            config = yaml.safe_load(f)
        if self._agent_config:
            with open(self._agent_config) as f:
                overrides = yaml.safe_load(f) or {}
            config = recursive_merge(config, overrides.get("config", overrides))
        return config

    # ------------------------------------------------------------------ one agent

    def _run_one(
        self,
        *,
        agent_id: str,
        agents: list[str],
        feature_id: int,
        task: str,
        environment: BaseEnvironment,
        loop: asyncio.AbstractEventLoop,
        uname: dict[str, str],
        redis_server: fakeredis.FakeServer,
        run_id: str,
        config: dict,
        image: str,
    ) -> dict:
        """Body of the original ``MiniSweAgentV2Runner.run`` for one agent (docker backend)."""
        agent_cfg = dict(config.get("agent", {}))
        model_cfg = config.get("model", {})
        env_cfg = config.get("environment", {})

        env_kwargs: dict[str, Any] = {
            "image": image,
            "cwd": "/workspace/repo",
            "timeout": 10800,
        }
        container_env = dict(env_cfg.get("env") or {})
        if container_env:
            env_kwargs["env"] = container_env
        env = HarborServiceEnvironment(
            harbor_env=environment,
            loop=loop,
            stop_event=self._stop,
            uname=uname,
            service=agent_id,
            **env_kwargs,
        )

        comm = None
        if self._messaging_enabled and len(agents) > 1:
            comm = MessagingConnector(
                agent_id=agent_id,
                agents=agents,
                url=f"redis://in-process#run:{run_id}",
                client=fakeredis.FakeRedis(server=redis_server),
            )

        model = _UsageTrackingModel(model_name=self.model_name, **model_cfg)

        git_connector = GitConnector(agent_id=agent_id, agents=agents, server_url="")
        git_connector.setup(env)

        extra_vars = {
            "agent_id": agent_id if len(agents) > 1 else None,
            "agents": agents,
            "git_enabled": False,
            "messaging_enabled": self._messaging_enabled,
        }
        agent_cfg.setdefault(
            "output_path", self.logs_dir / f"agent{feature_id}_full_traj.json"
        )
        agent = DefaultAgent(
            model=model, env=env, comm=comm, agent_id=agent_id, **agent_cfg
        )
        agent.extra_template_vars.update(extra_vars)

        error_msg = None
        try:
            result = agent.run(task=task)
            status = result.get("exit_status", "Submitted")
        except Exception as e:  # noqa: BLE001 - same as the original adapter
            status = "Error"
            error_msg = str(e) if not isinstance(e, AgentStopped) else f"stopped: {e}"

        patch = ""
        if not self._stop.is_set():
            try:
                patch = normalize_patch(git_connector.submitted_patch(env))
            except Exception:  # noqa: BLE001, S110 - same as the original adapter
                pass
            if not self._stop.is_set():
                # Publish the graded artifact to the grading container right away, so it
                # survives even if the other agent later runs into the trial timeout.
                fut = asyncio.run_coroutine_threadsafe(
                    self._write_main(
                        environment, f"{SUBMISSION_DIR}/{agent_id}.patch", patch
                    ),
                    loop,
                )
                fut.result(timeout=600)

        sanitized = [
            {**m, "content": ""} if m.get("content") is None else m
            for m in agent.messages
        ]
        return {
            "feature_id": feature_id,
            "agent_id": agent_id,
            "status": status,
            "patch": patch,
            "cost": agent.cost,
            "steps": agent.n_calls,
            "input_tokens": model.usage["input"],
            "output_tokens": model.usage["output"],
            "cache_read_tokens": model.usage["cache_read"],
            "cache_write_tokens": 0,
            "messages": sanitized,
            "sent_messages": agent.sent_messages,
            "error": error_msg,
        }

    # ------------------------------------------------------------------ trial

    @staticmethod
    def _pair_metadata(environment: BaseEnvironment) -> dict:
        """{repo, task_id, features} from the task's environment/pair.json (host side)."""
        path = Path(environment.environment_dir) / "pair.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"{path} not found: this agent needs a task generated by cooperbench-autoadapter"
            )
        return json.loads(path.read_text())

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        pair = self._pair_metadata(environment)
        repo, task_id = pair["repo"], int(pair["task_id"])
        features = [int(f) for f in pair["features"]]
        if features != sorted(features) or len(features) != 2:
            raise ValueError(
                f"pair.json features must be two ascending ids, got {features}"
            )
        texts = dict(zip(features, parse_instruction(instruction, features)))
        agents = [f"agent{i + 1}" for i in range(len(features))]
        run_id = uuid.uuid4().hex[:8]
        start_time = datetime.now()
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Mark the trial as coop in the grading container; patches arrive per agent.
        res = await environment.exec(
            f"mkdir -p {SUBMISSION_DIR} && rm -f {SUBMISSION_DIR}/agent*.patch && echo coop > {SUBMISSION_DIR}/mode",
            timeout_sec=60,
        )
        if res.return_code != 0:
            raise RuntimeError(
                f"cannot prepare {SUBMISSION_DIR} in main: {res.stdout} {res.stderr}"
            )

        # Same name as the original's get_image_name (the compose services run this image).
        image = f"akhatua/cooperbench-{repo.replace('_task', '').replace('_', '-')}:task{task_id}"
        uname = await self._service_uname(environment, agents[0])
        config = self._load_config()
        redis_server = fakeredis.FakeServer()
        loop = asyncio.get_running_loop()
        results: dict[str, dict] = {}

        def thread_body(agent_id: str, feature_id: int) -> None:
            try:
                results[agent_id] = self._run_one(
                    agent_id=agent_id,
                    agents=agents,
                    feature_id=feature_id,
                    task=texts[feature_id],
                    environment=environment,
                    loop=loop,
                    uname=uname,
                    redis_server=redis_server,
                    run_id=run_id,
                    config=config,
                    image=image,
                )
            except Exception as e:  # noqa: BLE001 - original: status Error, empty patch
                self.logger.warning(f"{agent_id} failed: {e}")
                results[agent_id] = {
                    "feature_id": feature_id,
                    "agent_id": agent_id,
                    "status": "Error",
                    "patch": "",
                    "cost": 0,
                    "steps": 0,
                    "messages": [],
                    "error": str(e),
                }

        pool = ThreadPoolExecutor(
            max_workers=len(agents), thread_name_prefix=f"coop-{run_id}"
        )
        futures = [
            loop.run_in_executor(pool, thread_body, a, f)
            for a, f in zip(agents, features)
        ]
        try:
            await asyncio.gather(*futures)
        except BaseException:
            # Trial timeout or cancellation: stop both agents at their next command and
            # never let them write to the grading container afterwards.
            self._stop.set()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            self._save_logs(
                repo, task_id, features, agents, results, run_id, start_time, context
            )

    def _save_logs(
        self, repo, task_id, features, agents, results, run_id, start_time, context
    ) -> None:
        try:
            end_time = datetime.now()
            sent = [
                m
                for m in _extract_conversation(
                    results, [a for a in agents if a in results]
                )
                if not m.get("received")
            ]
            sent.sort(key=_ts_key)
            (self.logs_dir / "conversation.json").write_text(
                json.dumps(sent, indent=2, default=str)
            )
            for agent_id, r in results.items():
                fid = r["feature_id"]
                (self.logs_dir / f"agent{fid}.patch").write_text(r.get("patch", ""))
                (self.logs_dir / f"agent{fid}_traj.json").write_text(
                    json.dumps(
                        {
                            "repo": repo,
                            "task_id": task_id,
                            "feature_id": fid,
                            "agent_id": agent_id,
                            "model": self.model_name,
                            "status": r.get("status"),
                            "cost": r.get("cost"),
                            "steps": r.get("steps"),
                            "messages": r.get("messages", []),
                        },
                        indent=2,
                        default=str,
                    )
                )
            summary = {
                "repo": repo,
                "task_id": task_id,
                "features": features,
                "setting": "coop",
                "run_id": run_id,
                "agent_framework": "mini_swe_agent_v2",
                "model": self.model_name,
                "started_at": start_time.isoformat(),
                "ended_at": end_time.isoformat(),
                "duration_seconds": (end_time - start_time).total_seconds(),
                "agents": {
                    a: {
                        "feature_id": r["feature_id"],
                        "status": r.get("status"),
                        "cost": r.get("cost", 0),
                        "steps": r.get("steps", 0),
                        "input_tokens": r.get("input_tokens", 0),
                        "output_tokens": r.get("output_tokens", 0),
                        "cache_read_tokens": r.get("cache_read_tokens", 0),
                        "patch_lines": len(r.get("patch", "").splitlines()),
                        "error": r.get("error"),
                    }
                    for a, r in results.items()
                },
                "total_cost": sum(r.get("cost", 0) for r in results.values()),
                "total_steps": sum(r.get("steps", 0) for r in results.values()),
                "messages_sent": len(sent),
            }
            (self.logs_dir / "result.json").write_text(
                json.dumps(summary, indent=2, default=str)
            )

            context.n_input_tokens = sum(
                r.get("input_tokens", 0) for r in results.values()
            )
            context.n_cache_tokens = sum(
                r.get("cache_read_tokens", 0) for r in results.values()
            )
            context.n_output_tokens = sum(
                r.get("output_tokens", 0) for r in results.values()
            )
            context.cost_usd = summary["total_cost"] or None
            context.metadata = {
                "agents": summary["agents"],
                "messages_sent": summary["messages_sent"],
                "total_steps": summary["total_steps"],
            }
        except Exception as e:  # noqa: BLE001 - logging must not mask the run result
            self.logger.warning(f"could not save coop logs: {e}")
