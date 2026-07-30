"""Vendor an official ITR JSON schema into the registry.

Usage:
    python scripts/vendor_schema.py --form ITR-4 --ay 2026-27 \
        --version Ver1.0 --source <file-or-https-url>

The official schema ships inside the ITD offline utility
(``…\\assets\\schema\\`` in the extracted utility) or the portal Downloads
section — there is no clean public JSON URL, so extract the JSON and point
``--source`` at it (a local path, or an https URL if you have a direct one).

This copies the schema to ``schemas/itr/ay<AY>/<FORM><VERSION>.json``, prints
the SHA-256 for the MANIFEST, and prints the one-line change to add to
``schema_registry.SUPPORTED_VERSIONS``. It does NOT edit the registry itself —
a schema swap is a deliberate, reviewed change, and any key-name deltas
between the official schema and the builder output belong in
``itrcore/itr/quirks.py`` (version-keyed), never in the builders.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas" / "itr"


def load_source(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=30) as resp:
            return resp.read()
    return Path(source).read_bytes()


def vendor(*, form: str, ay: str, version: str, source: str) -> Path:
    raw = load_source(source)
    schema = json.loads(raw)  # fail fast if it is not valid JSON
    folder = SCHEMAS / f"ay{ay.replace('-', '_')}"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{form.replace('-', '')}_{version}.json"
    dest.write_text(json.dumps(schema, indent=2), encoding="utf-8")
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Vendor an ITR JSON schema.")
    ap.add_argument("--form", required=True, help="e.g. ITR-4")
    ap.add_argument("--ay", required=True, help="e.g. 2026-27")
    ap.add_argument("--version", required=True, help="e.g. Ver1.0")
    ap.add_argument("--source", required=True,
                    help="local path or https URL to the schema JSON")
    args = ap.parse_args(argv)
    dest = vendor(form=args.form, ay=args.ay, version=args.version,
                  source=args.source)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    print(f"Vendored: {dest}")
    print(f"SHA-256:  {digest}")
    print("Then register it in itrcore/itr/schema_registry.py:")
    print(f'    ("{args.form}", "{args.ay}"): "{args.version}",')
    print("and re-run: python -m pytest tests/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
