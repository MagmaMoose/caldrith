"""Structured audit logging for Caldrith.

All log output is JSON (structlog) so reconcile actions are machine-parseable in
production. Helpers bind the installation, delivery, and repo context that every
operator wants when auditing a mutation.
"""
