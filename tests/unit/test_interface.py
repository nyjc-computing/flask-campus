"""flask-campus public-interface closed-world guard and manifest check.

The public interface of this dist is the set of package ``__all__``s:

- campus.flask_campus        (request/response glue, login/session context)
- campus.audit.middleware    (tracing/journeys/identity middleware)
- campus.audit.client        (AuditClient, the caller-side audit API)
- campus.audit.http          (the HTTP client seam the middleware speaks through)

Declared names must actually import; underscore-prefixed names are
private by convention. The manifest (tests/interface_manifest.json) is
the checked-in snapshot CI compares against reality, so any interface
change is a deliberate, diffable commit that ships with a CHANGELOG
entry and the version bump the diff dictates (additive -> patch,
breaking -> minor).

Unlike campus-common, no tree-closure rule: these packages declare
symbols (not submodules) in __all__, matching the carved surface.
"""

import ast
import importlib
import json
import pathlib
import unittest

PACKAGES = [
    "campus.flask_campus",
    "campus.audit.middleware",
    "campus.audit.client",
    "campus.audit.http",
]

MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "interface_manifest.json"


def _all_of(tree: ast.Module) -> list[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if getattr(target, "id", None) == "__all__":
                    return [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
    raise AssertionError("no __all__ defined")


def _init_of(module_name: str) -> ast.Module:
    module = importlib.import_module(module_name)
    init_path = pathlib.Path(module.__file__).resolve()
    return ast.parse(init_path.read_text(encoding="utf-8"))


def build_interface_manifest() -> dict[str, list[str]]:
    """The full declared-interface snapshot, as a JSON-ready dict."""
    return {name: sorted(_all_of(_init_of(name))) for name in PACKAGES}


class TestPackageInterface(unittest.TestCase):
    """__all__ closure rules for every package in the dist."""

    def test_all_declared_and_importable_and_no_underscores(self):
        for pkg in PACKAGES:
            with self.subTest(pkg=pkg):
                module = importlib.import_module(pkg)
                for name in module.__all__:
                    self.assertFalse(name.startswith("_"), name)
                    self.assertIsNotNone(getattr(module, name))


class TestInterfaceManifest(unittest.TestCase):
    """The checked-in manifest must match reality exactly (diff = deliberate)."""

    def test_manifest_matches_reality(self):
        committed = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(committed, build_interface_manifest())


if __name__ == "__main__":
    unittest.main()
