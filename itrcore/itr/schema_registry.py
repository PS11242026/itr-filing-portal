"""Vendored-schema registry with pinned versions per form and AY."""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

SCHEMAS_ROOT = Path(__file__).resolve().parents[2] / "schemas" / "itr"

# Bump when a new official schema version is vendored (see MANIFEST.md).
SUPPORTED_VERSIONS: dict[tuple[str, str], str] = {
    ("ITR-1", "2026-27"): "Ver1.0",
    ("ITR-2", "2026-27"): "Ver1.0",
    ("ITR-3", "2026-27"): "Ver1.0",
    ("ITR-4", "2026-27"): "Ver1.0",
}


@dataclass(frozen=True)
class SchemaBundle:
    form: str
    ay: str
    version: str
    path: Path
    schema: dict


@lru_cache(maxsize=8)
def get(form: str, ay: str) -> SchemaBundle:
    key = (form, ay)
    if key not in SUPPORTED_VERSIONS:
        raise ValueError(f"{form} for AY {ay} is not supported yet "
                         f"(supported: {sorted(SUPPORTED_VERSIONS)})")
    version = SUPPORTED_VERSIONS[key]
    folder = SCHEMAS_ROOT / f"ay{ay.replace('-', '_')}"
    fname = f"{form.replace('-', '')}_{version}.json"
    path = folder / fname
    if not path.exists():
        raise FileNotFoundError(
            f"schema file missing: {path} — see {folder / 'MANIFEST.md'}")
    return SchemaBundle(form=form, ay=ay, version=version, path=path,
                        schema=json.loads(path.read_text(encoding="utf-8")))
