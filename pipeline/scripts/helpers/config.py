"""Central configuration loader for the podcast pipeline."""

import os
from pathlib import Path

import yaml


def _find_config_path() -> Path:
    """Walk up from this file to find config.yaml in the pipeline root."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "config.yaml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("config.yaml not found in any parent directory")


def load_config(config_path: str | Path | None = None) -> dict:
    """Load and return the pipeline configuration dictionary."""
    path = Path(config_path) if config_path else _find_config_path()
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Singleton-style access
_config: dict | None = None


def get_config() -> dict:
    """Return cached config, loading on first access."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_paths() -> dict:
    """Return the paths section of the config."""
    return get_config()["paths"]


def source_root() -> Path:
    return Path(get_paths()["source_root"])


def episodes_root() -> Path:
    return Path(get_paths()["episodes_root"])


def assets_root() -> Path:
    return Path(get_paths()["assets_root"])


def pipeline_root() -> Path:
    return Path(get_paths()["pipeline_root"])
