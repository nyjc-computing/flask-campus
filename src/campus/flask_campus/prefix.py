"""campus.flask_campus.prefix

Gateway subpath mounting: move X-Forwarded-Prefix into SCRIPT_NAME.

A path-dispatching gateway (campus-gateway) serves consumer apps under
a namespace (/timetable/, /apps/, ...). Root-first apps emit every URL
at the host root (url_for('static') -> /static/...), which the gateway
cannot route — the observable failure is a fully unstyled page behind
the gateway (app stylesheet 404ing; CDN-hosted assets mask it).

The WSGI-correct mechanism is SCRIPT_NAME: the gateway forwards the
full request path and declares the namespace via X-Forwarded-Prefix;
this middleware MOVES the namespace from PATH_INFO into SCRIPT_NAME, so
werkzeug matches the namespace-stripped path and every url_for emits
namespace-ful URLs — static assets, links, and the auth blueprint alike
(flask-campus#50, which supersedes per-app login_prefix/
static_url_path edits for gateway deployments).

Trust model: the header is trusted from the immediately preceding hop
(the gateway), the same exposure class as X-Forwarded-Proto/Host. A
client reaching the app directly (origin domain) can spoof it — and
only breaks the generated URLs of its own requests; the OAuth callback
URL stays registry-validated by campus.auth (campus#1291).
"""

from collections.abc import Callable

_HEADER = "HTTP_X_FORWARDED_PREFIX"


def _normalize_prefix(value: str) -> str | None:
    """Return the header value as a mount prefix, or None if unusable.

    Valid: a single path segment chain with a leading slash and no
    trailing slash ("/timetable"). Anything else — empty, no leading
    slash, "//", ".." — is rejected outright rather than sanitized.
    """
    prefix = (value or "").strip().rstrip("/")
    if not prefix.startswith("/"):
        return None
    if "//" in prefix or ".." in prefix:
        return None
    return prefix


class PrefixFix:
    """WSGI middleware: move X-Forwarded-Prefix into SCRIPT_NAME.

    Consumes the header after applying it, so downstream forwarding
    middleware (e.g. the suite's ProxyFix) cannot double-strip. No-ops
    when the header is absent or malformed.
    """

    def __init__(self, app: Callable):
        self.app = app

    def __call__(self, environ, start_response):
        prefix = _normalize_prefix(environ.get(_HEADER) or "")
        environ.pop(_HEADER, None)
        if prefix:
            path = environ.get("PATH_INFO", "")
            environ["SCRIPT_NAME"] = prefix
            if path == prefix:
                environ["PATH_INFO"] = "/"
            elif path.startswith(prefix + "/"):
                environ["PATH_INFO"] = path[len(prefix):]
        return self.app(environ, start_response)
