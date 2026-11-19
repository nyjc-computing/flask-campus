"""Boundary rules for the flask-campus consumer layer (campus#980/#993).

The consumer layer is what app authors wire into their Flask apps. It
may import: campus.common, campus.model, campus_python, flask,
werkzeug, requests — and nothing else. It must NEVER import a campus
service module (campus.auth / campus.api / campus.audit / campus.yapper /
campus.deploy / campus.config) — services import the consumer layer,
never the reverse (#980). Nor the storage/webauth core layers that
campus-core ships (apps never touch them; campus#992).

flask/werkzeug ARE allowed at module level here: this dist is the flask
layer (the module-level-flask ban applies to the shared kernels, not
the consumer layer).

Since flask-campus#31 the dist ships NO campus.audit portion at all:
the audit middleware lives under campus.flask_campus.audit and the
caller-side audit client ships with the SDK as campus_python.audit —
one owner per import prefix. These tests also guard that invariant.
"""

import ast
import importlib
import pathlib
import unittest

import campus.flask_campus

SRCS = [
    pathlib.Path(campus.flask_campus.__file__).resolve().parent,
]

FORBIDDEN_ROOTS = (
    "campus.auth",
    "campus.api",
    "campus.audit",
    "campus.yapper",
    "campus.deploy",
    "campus.config",
    "campus.storage",
    "campus.webauth",
    "campus.services",
)


class TestConsumerLayerBoundaries(unittest.TestCase):
    """No import (module level or lazy) of service or core modules."""

    def test_no_service_or_core_imports(self):
        offenders: list[str] = []
        for src in SRCS:
            for py in src.rglob("*.py"):
                tree = ast.parse(py.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        names = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        names = [node.module or ""]
                    else:
                        continue
                    for name in names:
                        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN_ROOTS):
                            offenders.append(f"{py.relative_to(src.parent.parent)}: {name}")
        self.assertEqual([], offenders)


class TestOneOwnerPerPrefix(unittest.TestCase):
    """campus.audit belongs to campus-suite alone; the audit client
    belongs to campus-api-python (flask-campus#31).

    If this dist ever ships a campus/audit portion again, imports
    would silently shadow (or merge with) the suite's service package
    under the PEP 420 namespace — the exact confusion #31 removes.
    """

    def test_dist_ships_no_campus_audit_portion(self):
        src_root = SRCS[0].parent  # .../src/campus
        self.assertFalse(
            (src_root / "audit").exists(),
            "flask-campus must not ship a campus/audit portion "
            "(one owner per import prefix; see flask-campus#31)",
        )

    def test_audit_client_imports_from_sdk(self):
        audit_client = importlib.import_module("campus_python.audit")

        self.assertTrue(hasattr(audit_client, "AuditClient"))
        self.assertTrue(hasattr(audit_client, "roles"))
        self.assertTrue(hasattr(audit_client.roles, "resolve_role"))


if __name__ == "__main__":
    unittest.main()
