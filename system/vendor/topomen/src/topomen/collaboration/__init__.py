"""Agent orchestration implementations for TopoMen."""

import copy
import importlib

from topomen import Agent, Environment, Model
from topomen.collaboration.review import CriticDecision, FailureVerifier, MemoryWriteCritic, VerificationDecision

_AGENT_MAPPING = {
    "default": "topomen.collaboration.base_agent.DefaultAgent",
    "repair_memory": "topomen.collaboration.repair_agent.RepairMemoryAgent",
    "topology_repair_memory": "topomen.collaboration.repair_agent.TopologyRepairMemoryAgent",
}


def get_agent_class(spec: str) -> type[Agent]:
    full_path = _AGENT_MAPPING.get(spec, spec)
    try:
        module_name, class_name = full_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        return getattr(module, class_name)
    except (ValueError, ImportError, AttributeError):
        msg = f"Unknown agent type: {spec} (resolved to {full_path}, available: {_AGENT_MAPPING})"
        raise ValueError(msg)


def get_agent(model: Model, env: Environment, config: dict, *, default_type: str = "") -> Agent:
    config = copy.deepcopy(config)
    agent_class = get_agent_class(config.pop("agent_class", default_type))
    return agent_class(model, env, **config)


__all__ = [
    "CriticDecision",
    "FailureVerifier",
    "MemoryWriteCritic",
    "VerificationDecision",
    "get_agent",
    "get_agent_class",
]
