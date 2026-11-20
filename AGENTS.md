# flask-campus Quick Reference

The Flask consumer layer for Campus apps (PEP 420 portion
`campus/flask_campus`). User-facing docs are a MkDocs Material site
(mkdocstrings auto-generates the API reference from Google-style
docstrings) published to GitHub Pages — same stack as
campus-api-python.

## Docs workflow

```bash
poetry run mkdocs build --strict   # docs-rot gate: same check CI runs on every PR
poetry run mkdocs serve            # local preview at http://127.0.0.1:8000
```

- Reference pages (`docs/reference/*.md`) use mkdocstrings `:::`
  directives — they follow the code automatically. Write docstrings,
  not reference prose. Undocumented public members still render
  (heading + signature), so a missing docstring is a visible docs gap.
- mkdocstrings resolves the portions from `src/` (`paths: ["src"]`).
  The `campus.*` namespace top is PEP 420, but every documented
  portion below it carries an `__init__.py` — griffe cannot descend
  into implicit-namespace subpackages of a regular parent (SDK
  lesson; see mkdocs.yml).
- Strict build caught a real docstring/signature drift in
  `parameter.py` (`params` vs `func`) on day one — treat build
  warnings as bugs.

## Version bumps must ship with up-to-date docs

**Rule: every version bump must have up-to-date docs.** After
`poetry version ...`:

1. Run `poetry run python scripts/sync_docs_version.py` — regenerates
   `docs/snippets/version.md` (the stamp on the landing page).
2. Update any guides/examples (`docs/*.md`) the bump affects, the
   README install snippet, and CHANGELOG.md.
3. Regenerate the interface manifest
   (`scripts/generate_interface_manifest.py`) if signatures changed,
   and bump per the strict-0.x rules (patch = additive, minor =
   breaking).

CI enforces the stamp: the `docs` job regenerates it and fails on
`git diff --exit-code -- docs/`, and `mkdocs build --strict` fails on
broken links/refs. A bump without docs cannot merge.
