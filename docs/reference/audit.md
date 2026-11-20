# Audit middleware reference

Package surface: `campus.flask_campus.audit` — `init_tracing` and
`init_journeys` are the setup entry points;
`redact_sensitive` is the producer-side scrub primitive shared with
the audit service (re-exported lazily; canonical home
`campus.flask_campus.audit.tracing`).

## audit

::: campus.flask_campus.audit

## redact_sensitive

::: campus.flask_campus.audit.tracing
    options:
      members:
        - redact_sensitive
