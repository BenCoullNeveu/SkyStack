import yaml
from pathlib import Path

def _normalize_value(value):
    """Ensure config values are loaded consistently, but do not convert to Path automatically."""
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_normalize_value(v) for v in value]
    elif isinstance(value, str):
        return value.strip()  # keep as plain string
    return value  # int, float, bool, None, etc.

def load_config(config_path="config.yaml"):
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")
    with open(config_file, "r") as f:
        config = yaml.safe_load(f) or {}
        return _normalize_value(config)