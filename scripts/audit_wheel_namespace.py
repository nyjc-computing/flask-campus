#!/usr/bin/env python3
"""Audit built wheels: verify they ship init-less PEP 420 namespace portions.

`campus` is a native namespace package (PEP 420): every distribution that
contributes subpackages under `campus/` MUST NOT ship `campus/__init__.py`.
A single stale init file silently shadows the namespace - whichever dist
ships it wins, and every other dist's submodules stop importing, with no
error at install time. This audit turns that failure mode into a build-time
exit code.

Checks per wheel:
1. No `campus/__init__.py` (the shared ancestor level must stay init-less).
2. Every subdirectory of `campus/` containing .py files ships an
   `__init__.py` (each dist's own subpackages stay regular packages).
3. The wheel ships something under `campus/` at all (catches a `packages`
   include that drifted empty).

Usage:
    python scripts/audit_wheel_namespace.py               # audit dist/*.whl
    python scripts/audit_wheel_namespace.py dist/foo.whl  # audit specific wheels

Exit code 0 when all wheels pass; 1 on any violation or missing wheels.
"""

import sys
import zipfile
from pathlib import Path

NAMESPACE = "campus"


def audit_wheel(wheel: Path) -> list[str]:
    """Return a list of namespace violations for one built wheel."""
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    problems: list[str] = []

    top_init = f"{NAMESPACE}/__init__.py"
    if top_init in names:
        problems.append(f"{wheel.name}: ships {top_init} - a regular package here shadows all other dists' portions")

    # Subpackage dirs shipped by this dist must remain regular packages;
    # only the shared ancestor level is allowed to be init-less.
    py_dirs = {n.rsplit("/", 1)[0] for n in names if n.endswith(".py") and "/" in n}
    shipped = {d for d in py_dirs if d.startswith(f"{NAMESPACE}/") and d != NAMESPACE}
    for directory in sorted(shipped):
        if f"{directory}/__init__.py" not in names:
            problems.append(f"{wheel.name}: {directory}/ ships .py files without __init__.py")

    if not shipped and not any(n.startswith(f"{NAMESPACE}/") for n in names):
        problems.append(f"{wheel.name}: ships nothing under {NAMESPACE}/ - packages include is misconfigured")

    return problems


def main(argv: list[str]) -> int:
    wheels = [Path(a) for a in argv[1:]] or sorted(Path("dist").glob("*.whl"))
    if not wheels:
        print("no wheels found: run `poetry build` first (or pass paths explicitly)", file=sys.stderr)
        return 1

    failures = 0
    for wheel in wheels:
        problems = audit_wheel(wheel)
        if problems:
            failures += len(problems)
            for problem in problems:
                print(f"FAIL {problem}")
        else:
            print(f"OK   {wheel.name}: init-less {NAMESPACE}/ portion, subpackages intact")

    if failures:
        print(f"\n{failures} namespace violation(s) across {len(wheels)} wheel(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
