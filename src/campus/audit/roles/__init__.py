"""campus.audit.roles

Named roles for audit API keys (#918).

A key's privilege set is a list of scope strings. That is precise, but
it puts the policy decision — "what may this person hold?" — in every
issuer's head, spelled out in scope names every time. A role is a named
bundle of scopes: the issuer picks "traces-viewer" and the scope list
is the same everywhere, reviewed in one place.

The catalogue is **additive**. Wherever a scope list is accepted, a
role name is accepted too, and it resolves to exactly one scope list.
Raw scope lists keep working, so no key, no client and no existing
script breaks.

No role resolves to a wildcard. The operator key deliberately holds no
wildcard (`campus.config.AUDIT_OPERATOR_API_KEY_SCOPES`), and a
standing "*" role would be a privilege-escalation footgun in the one
place keys get minted.
"""

__all__ = [
    "CATALOGUE",
    "UnknownRoleError",
    "list_roles",
    "resolve_role",
    "resolve_scopes",
]

import typing

# The catalogue: role name -> the scopes it grants. Keep it small, and
# keep every bundle explicit — a role is a promise about what the
# holder can do.
CATALOGUE: dict[str, list[str]] = {
    # Read traces in the browser and query them over the API.
    "traces-viewer": ["traces:read"],
    # As above, plus the search scope the API's search endpoint needs.
    "traces-searcher": ["traces:read", "traces:search"],
    # A tracing producer: may read, search and ingest spans.
    "traces-producer": ["traces:read", "traces:search", "traces:write"],
    # May list and manage API keys — the operator's privilege, without
    # the trace privileges.
    "key-operator": ["apikeys:read", "apikeys:write"],
}


class UnknownRoleError(ValueError):
    """Raised when a role name is not in the catalogue."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"Unknown role '{name}'. Available roles: "
            f"{', '.join(sorted(CATALOGUE))}."
        )


def list_roles() -> dict[str, list[str]]:
    """The catalogue, as role name -> scopes."""
    return {name: list(scopes) for name, scopes in CATALOGUE.items()}


def resolve_role(name: str) -> list[str]:
    """Resolve a role name to its scope list.

    Args:
        name: The role name

    Returns:
        The scopes the role grants

    Raises:
        UnknownRoleError: The name is not in the catalogue
    """
    if not isinstance(name, str) or name not in CATALOGUE:
        raise UnknownRoleError(str(name))
    return list(CATALOGUE[name])


def resolve_scopes(
        scopes: str | typing.Sequence[str] | None = None,
        role: str | None = None,
) -> list[str]:
    """Resolve a scope list and/or a role name into one scope list.

    A role and an explicit scope list are mutually exclusive: a request
    that names both is ambiguous about which one was meant, and
    silently preferring one of them would hide a mistake in the
    caller's request.

    Args:
        scopes: A comma-separated string or a sequence of scopes
        role: A role name from the catalogue

    Returns:
        The resolved scope list

    Raises:
        UnknownRoleError: The role name is not in the catalogue
        ValueError: Both a role and an explicit scope list were given
    """
    if role is not None:
        if scopes:
            raise ValueError(
                "Give either a role or a list of scopes, not both."
            )
        return resolve_role(role)
    if not scopes:
        return []
    if isinstance(scopes, str):
        return [scope.strip() for scope in scopes.split(",") if scope.strip()]
    return [str(scope).strip() for scope in scopes if str(scope).strip()]
