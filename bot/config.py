"""Load non-secret settings from config.toml and secrets from environment variables."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.toml", "rb") as f:
        return tomllib.load(f)


def env(name: str, default: str | None = None) -> str | None:
    """Read a secret from the environment. Never log the returned value."""
    value = (os.environ.get(name) or "").strip()
    return value or default  # unset or empty (e.g. an unset GitHub secret) -> default


def env_flag(name: str) -> bool:
    return (env(name, "") or "").lower() in {"1", "true", "yes"}
