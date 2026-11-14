"""Janus's feature modules.

Routes and CLI commands call into these; they never import from ``routes``.

===============  ==============================================================
``auth``         the permission catalogue, built-in roles and ``require`` checks
``gateways``     which gateway the current request acts on (session selection)
``caddy``        everything that talks to Caddy: config generation, the admin
                 API transport (real or simulated), plugin detection
``collector``    access-log ingestion and Prometheus metrics scraping
``services``     configuration deploys, analytics, alerts, security rules,
                 API keys, health and audit
``docs``         the in-app documentation served at ``/docs``
===============  ==============================================================
"""
