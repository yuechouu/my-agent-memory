"""Centralized configuration for API keys and service endpoints.

Resolution priority: explicit config > environment variable > auth file > default.
"""

import json
import os
from pathlib import Path
from typing import Optional

__all__ = ["get_api_key", "get_auth_data"]

# Default auth file path (Kilo convention)
_AUTH_FILE = Path.home() / ".local" / "share" / "kilo" / "auth.json"


def _load_auth_file() -> dict:
    """Load and parse the auth file. Returns {} on any failure."""
    if not _AUTH_FILE.exists():
        return {}
    try:
        return json.loads(_AUTH_FILE.read_text())
    except Exception:
        return {}


def get_api_key(
    provider: str,
    *,
    env_var: str = "",
    config_key: str = "",
    auth_file_key: str = "",
) -> str:
    """Resolve an API key for a given provider.

    Priority:
      1. Explicit config_key in the auth file's top-level keys
      2. Environment variable (env_var)
      3. Auth file nested under provider name (auth_file_key or provider)

    Args:
        provider: Provider name (e.g. 'siliconflow', 'xiaomimimo', 'deepseek').
        env_var: Environment variable name to check (e.g. 'SILICONFLOW_API_KEY').
        config_key: Top-level key in auth file (e.g. 'siliconflow_key').
        auth_file_key: Key inside provider dict in auth file (default: 'key').

    Returns:
        API key string, or empty string if not found.
    """
    auth = _load_auth_file()

    # 1. Top-level config key
    if config_key and auth.get(config_key):
        return auth[config_key]

    # 2. Environment variable
    if env_var:
        env_val = os.getenv(env_var, "")
        if env_val:
            return env_val

    # 3. Nested under provider name
    nested = auth.get(provider, {})
    if isinstance(nested, dict):
        return nested.get(auth_file_key or "key", "")

    return ""


def get_auth_data(provider: str) -> dict:
    """Get the full auth dict for a provider (e.g. for base_url, key, etc.)."""
    auth = _load_auth_file()
    data = auth.get(provider, {})
    return data if isinstance(data, dict) else {}
