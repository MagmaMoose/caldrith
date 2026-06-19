"""Reconcile orchestration: plan target repos, diff each, apply or dry-run.

:mod:`caldrith.reconcile.planner` enumerates target repos (account-type aware).
:mod:`caldrith.reconcile.repository` applies the repository block to one repo.
:mod:`caldrith.reconcile.runner` wires it together and, in dry-run, posts a Check Run.
"""
