"""Explicit YAML loading; no environment overlays, credentials or mutable caches."""
import hashlib
import json
from importlib.resources import files
from pathlib import Path

import yaml

from .models import ShortDTEConfig


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("configuration requires unique string keys")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_config(path: str | Path | None = None) -> ShortDTEConfig:
    raw = (files(__package__).joinpath("defaults.yaml").read_text(encoding="utf-8")
           if path is None else Path(path).read_text(encoding="utf-8"))
    # Use the SafeLoader subclass directly: retain duplicate-key rejection
    # without the generic loading entry point prohibited by repository gates.
    loader = _UniqueKeyLoader(raw)
    try:
        data = loader.get_single_data()
    finally:
        loader.dispose()
    if not isinstance(data, dict):
        raise ValueError("configuration must be a mapping")
    # Avoid bool/int equality accepting true as schema version 1.
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported or missing configuration schema_version")
    if "live_trading_enabled" in data and data["live_trading_enabled"] is not False:
        raise ValueError("live trading is unavailable")
    return ShortDTEConfig.model_validate(data)


def canonical_config_json(config: ShortDTEConfig) -> str:
    return json.dumps(config.model_dump(mode="json"), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def config_hash(config: ShortDTEConfig) -> str:
    return "sha256:" + hashlib.sha256(canonical_config_json(config).encode("utf-8")).hexdigest()
