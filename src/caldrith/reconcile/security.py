"""Reconcile repository security toggles (Dependabot + private vuln reporting).

These are boolean switches behind dedicated endpoints (not ``repos.update``):

- **vulnerability alerts** (Dependabot alerts): ``PUT``/``DELETE`` ``/vulnerability-alerts``;
  ``GET`` returns ``204`` (enabled) / ``404`` (disabled).
- **automated security fixes** (Dependabot security updates):
  ``PUT``/``DELETE`` ``/automated-security-fixes``; ``GET`` returns ``{"enabled": …}``.
- **private vulnerability reporting**: ``PUT``/``DELETE``
  ``/private-vulnerability-reporting``; ``GET`` returns ``{"enabled": …}``.

Each toggle is read, compared, and flipped only on drift (idempotent). Vulnerability
alerts are reconciled first because automated security fixes depend on them.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import RepoScoped, RepositorySecurity
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


@dataclass
class SecurityResult:
    """Outcome of reconciling a repo's security toggles."""

    repo: str
    changed_fields: list[str] = field(default_factory=list)
    applied: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.changed_fields)


async def _enabled_204(coro: Awaitable[Any]) -> bool:
    """For endpoints that return 204 (enabled) / 404 (disabled)."""
    try:
        response = await coro
    except RequestFailed as exc:
        if exc.response.status_code == 404:
            return False
        raise
    # Robust to githubkit either raising on 404 (above) or returning the response.
    return getattr(response, "status_code", 204) != 404


async def _enabled_json(coro: Awaitable[Any]) -> bool:
    """For endpoints that return ``{"enabled": bool}`` (404 -> not enabled)."""
    try:
        response = await coro
    except RequestFailed as exc:
        if exc.response.status_code == 404:
            return False
        raise
    if getattr(response, "status_code", 200) == 404:
        return False
    return bool(response_json(response).get("enabled", False))


class RepositorySecurityApplier:
    """Reconciles a single repo's security toggles against the desired block."""

    def __init__(self, client: GitHub, *, dry_run: bool = False) -> None:
        self._client = client
        self._dry_run = dry_run

    async def apply(self, target: TargetRepo, security: RepositorySecurity) -> SecurityResult:
        repos = self._client.rest.repos
        owner, repo = target.owner, target.name
        result = SecurityResult(repo=target.full_name)

        # Vulnerability alerts first — automated security fixes depend on them.
        desired = security.enable_vulnerability_alerts
        if desired is not None:
            current = await _enabled_204(repos.async_check_vulnerability_alerts(owner, repo))
            if current != desired:
                if not self._dry_run:
                    if desired:
                        await repos.async_enable_vulnerability_alerts(owner, repo)
                    else:
                        await repos.async_disable_vulnerability_alerts(owner, repo)
                result.changed_fields.append("vulnerability_alerts")

        desired = security.enable_automated_security_fixes
        if desired is not None:
            current = await _enabled_json(repos.async_check_automated_security_fixes(owner, repo))
            if current != desired:
                if not self._dry_run:
                    if desired:
                        await repos.async_enable_automated_security_fixes(owner, repo)
                    else:
                        await repos.async_disable_automated_security_fixes(owner, repo)
                result.changed_fields.append("automated_security_fixes")

        desired = security.enable_private_vulnerability_reporting
        if desired is not None:
            current = await _enabled_json(
                repos.async_check_private_vulnerability_reporting(owner, repo)
            )
            if current != desired:
                if not self._dry_run:
                    if desired:
                        await repos.async_enable_private_vulnerability_reporting(owner, repo)
                    else:
                        await repos.async_disable_private_vulnerability_reporting(owner, repo)
                result.changed_fields.append("private_vulnerability_reporting")

        result.applied = bool(result.changed_fields) and not self._dry_run
        return result


def _configured(config: RepoScoped) -> bool:
    """True when the repository block carries a ``security`` sub-block with any toggle."""
    if config.repository is None or config.repository.security is None:
        return False
    return bool(config.repository.security.model_dump(exclude_unset=True, exclude_none=True))


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Uniform adapter: reconcile repository security toggles for one repo."""
    if config.repository is None or config.repository.security is None:
        return []
    result = await RepositorySecurityApplier(client, dry_run=dry_run).apply(
        target, config.repository.security
    )
    return [
        TierResult(
            tier="security",
            scope=result.repo,
            changed=result.changed,
            applied=result.applied,
            notes=list(result.changed_fields),
        )
    ]


TIER = RepoTier(name="security", configured=_configured, reconcile=reconcile)
