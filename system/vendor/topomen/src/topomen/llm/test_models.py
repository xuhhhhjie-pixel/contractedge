import logging
import time
from typing import Any

from pydantic import BaseModel

from topomen.llm import GLOBAL_MODEL_STATS
from topomen.llm.utils.actions_text import format_observation_messages
from topomen.llm.utils.openai_multimodal import expand_multimodal_content


def make_output(content: str, actions: list[dict], cost: float = 1.0) -> dict:
    """Helper to create an output dict for DeterministicModel.

    Args:
        content: The response content string
        actions: List of action dicts, e.g., [{"command": "echo hello"}]
        cost: Cost to report for this output (default 1.0)
    """
    return {
        "role": "assistant",
        "content": content,
        "extra": {"actions": actions, "cost": cost, "timestamp": time.time()},
    }


def _process_test_actions(actions: list[dict]) -> bool:
    """Process special test actions. Returns True if the query should be retried."""
    for action in actions:
        if "raise" in action:
            raise action["raise"]
        cmd = action.get("command", "")
        if cmd.startswith("/sleep "):
            time.sleep(float(cmd.split("/sleep ")[1]))
            return True
        if cmd.startswith("/warning"):
            logging.warning(cmd.split("/warning")[1])
            return True
    return False


class DeterministicModelConfig(BaseModel):
    outputs: list[dict]
    """List of exact output messages to return in sequence. Each dict should have 'role', 'content', and 'extra' (with 'actions')."""
    model_name: str = "deterministic"
    cost_per_call: float = 1.0
    observation_template: str = (
        "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
        "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
    )
    """Template used to render the observation after executing an action."""
    multimodal_regex: str = ""
    """Regex to extract multimodal content. Empty string disables multimodal processing."""


class DeterministicModel:
    def __init__(self, **kwargs):
        """Initialize with a list of output messages to return in sequence."""
        self.config = DeterministicModelConfig(**kwargs)
        self.current_index = -1

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        self.current_index += 1
        output = self.config.outputs[self.current_index]
        if _process_test_actions(output.get("extra", {}).get("actions", [])):
            return self.query(messages, **kwargs)
        GLOBAL_MODEL_STATS.add(self.config.cost_per_call)
        return output

    def format_message(self, **kwargs) -> dict:
        return expand_multimodal_content(kwargs, pattern=self.config.multimodal_regex)

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        """Format execution outputs into observation messages."""
        return format_observation_messages(
            outputs,
            observation_template=self.config.observation_template,
            template_vars=template_vars,
            multimodal_regex=self.config.multimodal_regex,
        )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return self.config.model_dump()

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "model": self.config.model_dump(mode="json"),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
            }
        }
