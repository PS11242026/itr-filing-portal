"""Per-AY statutory configuration loader with a content hash for
reproducibility (stored on every computation snapshot)."""

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class AYConfig:
    def __init__(self, raw: dict, config_hash: str) -> None:
        self.raw = raw
        self.config_hash = config_hash

    @property
    def ay(self) -> str:
        return self.raw["ay"]

    def regime(self, name: str) -> dict:
        return self.raw[f"{name}_regime"]


@lru_cache(maxsize=4)
def load(ay: str) -> AYConfig:
    path = CONFIG_DIR / f"ay_{ay.replace('-', '_')}.yaml"
    if not path.exists():
        raise ValueError(f"no statutory config for AY {ay} "
                         f"(expected {path.name})")
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return AYConfig(yaml.safe_load(text), digest)
