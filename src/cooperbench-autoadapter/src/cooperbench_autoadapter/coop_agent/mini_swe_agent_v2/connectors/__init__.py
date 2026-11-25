"""Connectors for inter-agent communication.

Vendored subset: the shared git servers (``--git``) are not part of this adapter, so only the
per-agent ``GitConnector`` (submission through a PR on a local bare remote) and the
``MessagingConnector`` are exported.
"""

from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.connectors.git import GitConnector
from cooperbench_autoadapter.coop_agent.mini_swe_agent_v2.connectors.messaging import MessagingConnector

__all__ = [
    "GitConnector",
    "MessagingConnector",
]
