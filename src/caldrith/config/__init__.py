"""Configuration schema, loader, and diff engine.

:mod:`caldrith.config.schema` and :mod:`caldrith.config.diff` are pure and I/O-free
(unit-tested for idempotency). :mod:`caldrith.config.loader` is the only module here
that touches the network — it fetches and validates the admin repo's settings file.
"""
