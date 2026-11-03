"""Boundary rules for the flask-campus consumer layer (campus#980/#993).

The consumer layer is what app authors wire into their Flask apps. It
may import: campus.common, campus.model, campus_python, flask,
werkzeug, requests — and nothing else. It must NEVER import a campus
service module (campus.auth / campus.api / campus.audit resources-routes-
web / campus.yapper / campus.deploy / campus.config) — services import
the consumer layer, never the reverse (#980). Nor the storage/webauth
core layers that campus-core ships (apps never touch them; campus#992).

flask/werkzeug ARE allowed at module level here: this dist is the flask
layer (the module-level-flask ban applies to the shared kernels, not
the consumer layer).
"""

import ast
import pathlib
import unittest

import campus.audit.client
import campus.audit.http
import campus.audit.middleware
import campus.flask_campus

SRCS = [
    pathlib.Path(campus.flask_campus.__file__).resolve().parent,
    pathlib.Path(campus.audit.middleware.__file__).resolve().parent.parent,
]

FORBIDDEN_ROOTS = (
    "campus.auth",
    "campus.api",
    "campus.yapper",
    "campus.deploy",
    "campus.config",
    "campus.storage",
    "campus.webauth",
    "campus.services",
    # The service half of campus.audit stays in campus-suite; only
    # middleware/client/http are this dist's. The lazy
    # campus.flask_campus.propagation import in middleware/__init__ is
    # intra-dist and allowed.
    "campus.audit.resources",
    "campus.audit.routes",
    "campus.audit.web",
    "campus.audit.helpers",
    "campus.audit.roles",
    "campus.audit.decorators",
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


if __name__ == "__main__":
    unittest.main()
