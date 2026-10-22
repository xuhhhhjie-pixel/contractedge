"""Provider profile resolution for OpenAI-compatible LLM endpoints."""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from typing import Any

_DISABLED_PROFILES = {"", "default", "none", "off", "disabled"}

_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "chatanywhere": {
        "model_name": "openai/gpt-5.4",
        "api_base": "https://api.chatanywhere.tech/v1",
    },
    "deepseek": {
        # DeepSeek documents the OpenAI-compatible base URL and model name.
        # LiteLLM's openai/* provider prefix keeps the existing OpenAI-compatible path.
        "model_name": "openai/deepseek-v4-pro",
        "api_base": "https://api.deepseek.com",
    },
}


def _profile_from_env() -> str:
    return os.getenv("TOPOMEN_LLM_PROVIDER") or os.getenv("TOPOMEN_LLM_PROFILE") or ""


def _env_prefix(profile: str) -> str:
    return "TOPOMEN_" + re.sub(r"[^A-Z0-9]+", "_", profile.upper()).strip("_")


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def _load_json_env(*names: str) -> dict[str, Any]:
    raw = _first_env(*names)
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {next(name for name in names if os.getenv(name))}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected a JSON object in {next(name for name in names if os.getenv(name))}")
    return loaded


def _set_openai_compatible_env(*, api_key: str | None, api_base: str | None) -> None:
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key
    if api_base:
        os.environ["OPENAI_API_BASE"] = api_base
        os.environ["OPENAI_BASE_URL"] = api_base


def apply_llm_provider_profile(config: dict[str, Any] | None, *, override_model_name: bool = True) -> dict[str, Any]:
    """Apply the selected LLM provider profile to a model config.

    `TOPOMEN_LLM_PROVIDER` is intentionally stronger than YAML defaults so experiments
    can switch providers without editing every built-in config file.
    """
    resolved_config = deepcopy(config or {})
    profile = _profile_from_env().strip().lower()
    if profile in _DISABLED_PROFILES:
        return resolved_config

    preset = _PROVIDER_PRESETS.get(profile, {})
    prefix = _env_prefix(profile)

    model_name = _first_env(f"{prefix}_MODEL_NAME") or preset.get("model_name")
    api_base = _first_env(f"{prefix}_API_BASE", f"{prefix}_BASE_URL") or preset.get("api_base")

    if profile == "deepseek":
        api_key = _first_env(f"{prefix}_API_KEY", "DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("TOPOMEN_LLM_PROVIDER=deepseek requires TOPOMEN_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY.")
    elif profile == "chatanywhere":
        api_key = _first_env(f"{prefix}_API_KEY", "CHATANYWHERE_API_KEY", "OPENAI_API_KEY")
    else:
        api_key = _first_env(f"{prefix}_API_KEY", "TOPOMEN_API_KEY", "OPENAI_API_KEY")
        model_name = model_name or _first_env("TOPOMEN_MODEL_NAME")
        api_base = api_base or _first_env("TOPOMEN_API_BASE", "TOPOMEN_BASE_URL", "OPENAI_API_BASE", "OPENAI_BASE_URL")

    _set_openai_compatible_env(api_key=api_key, api_base=api_base)

    if override_model_name and model_name:
        resolved_config["model_name"] = model_name

    model_kwargs = dict(resolved_config.get("model_kwargs") or {})
    if api_base:
        model_kwargs["api_base"] = api_base
    # Keep secrets in environment variables instead of serialized model config.
    model_kwargs.pop("api_key", None)

    provider_kwargs = _load_json_env(f"{prefix}_MODEL_KWARGS_JSON", "TOPOMEN_MODEL_KWARGS_JSON")
    if provider_kwargs:
        model_kwargs = {**model_kwargs, **provider_kwargs}
    if model_kwargs:
        resolved_config["model_kwargs"] = model_kwargs

    resolved_config.setdefault("cost_tracking", "ignore_errors")
    return resolved_config
