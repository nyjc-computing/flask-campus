#!/usr/bin/env python3
"""Regenerate the flask-campus public-interface manifest.

The manifest (tests/interface_manifest.json) is the checked-in snapshot
of the declared public interface: every package's __all__. CI compares
it against reality (see tests/unit/test_interface.py); a diff is a
deliberate interface change and must ship with a CHANGELOG entry and
the version bump the diff dictates (additive -> patch, breaking -> minor).

Usage:
    python scripts/generate_interface_manifest.py            # rewrite manifest
    python scripts/generate_interface_manifest.py --check    # exit 1 on diff
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.unit.test_interface import build_interface_manifest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing; exit 1 on diff")
    args = parser.parse_args()
    manifest = json.dumps(build_interface_manifest(), indent=2) + "\n"
    manifest_path = PROJECT_ROOT / "tests" / "interface_manifest.json"
    if args.check:
        current = manifest_path.read_text(encoding="utf-8")
        if current != manifest:
            print("interface manifest is stale; run scripts/generate_interface_manifest.py", file=sys.stderr)
            return 1
        print("interface manifest OK")
        return 0
    manifest_path.write_text(manifest, encoding="utf-8")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
