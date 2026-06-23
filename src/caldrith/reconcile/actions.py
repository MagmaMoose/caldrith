"""Reconcile a repository's GitHub Actions settings.

Two independent endpoints back this tier, each read-compared-and-set only on drift:

- **permissions** — whether Actions is ``enabled`` and which ``allowed_actions``
  (``all`` / ``local_only`` / ``selected``). ``allowed_actions`` is only meaningful when
  Actions is enabled, so it is sent only then.
- **default workflow permissions** — the default ``GITHUB_TOKEN`` scope
  (``read`` / ``write``) and whether Actions ``can_approve_pull_request_reviews``.

Only the sub-settings the config declares are touched; unset fields fall back to the
repo's current value so a partial declaration never clobbers the other half.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.config.schema import RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


def _configured(config: RepoScoped) -> bool:
    if config.actions is None:
        return False
    return bool(config.actions.model_dump(exclude_unset=True, exclude_none=True))


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile Actions permissions + default workflow permissions (set drift)."""
    if config.actions is None:
        return []
    desired = config.actions
    actions = client.rest.actions
    owner, repo = target.owner, target.name
    result = TierResult(tier="actions", scope=target.full_name)

    # --- Actions permissions (enabled + allowed_actions) ---
    if desired.enabled is not None or desired.allowed_actions is not None:
        current = response_json(
            await actions.async_get_github_actions_permissions_repository(owner=owner, repo=repo)
        )
        enabled_drift = desired.enabled is not None and current.get("enabled") != desired.enabled
        allowed_drift = (
            desired.allowed_actions is not None
            and current.get("allowed_actions") != desired.allowed_actions
        )
        if enabled_drift or allowed_drift:
            result.changed = True
            result.notes.append("set actions permissions")
            if not dry_run:
                want_enabled = (
                    desired.enabled if desired.enabled is not None else current.get("enabled")
                )
                body: dict[str, Any] = {"enabled": want_enabled}
                want_allowed = (
                    desired.allowed_actions
                    if desired.allowed_actions is not None
                    else current.get("allowed_actions")
                )
                if want_enabled and want_allowed:
                    body["allowed_actions"] = want_allowed
                await actions.async_set_github_actions_permissions_repository(
                    owner=owner, repo=repo, data=body
                )

    # --- Default workflow token permissions ---
    if (
        desired.default_workflow_permissions is not None
        or desired.can_approve_pull_request_reviews is not None
    ):
        current = response_json(
            await actions.async_get_github_actions_default_workflow_permissions_repository(
                owner=owner, repo=repo
            )
        )
        dwp_drift = (
            desired.default_workflow_permissions is not None
            and current.get("default_workflow_permissions") != desired.default_workflow_permissions
        )
        cap_drift = (
            desired.can_approve_pull_request_reviews is not None
            and current.get("can_approve_pull_request_reviews")
            != desired.can_approve_pull_request_reviews
        )
        if dwp_drift or cap_drift:
            result.changed = True
            result.notes.append("set default workflow permissions")
            if not dry_run:
                body = {
                    "default_workflow_permissions": (
                        desired.default_workflow_permissions
                        if desired.default_workflow_permissions is not None
                        else current.get("default_workflow_permissions")
                    ),
                    "can_approve_pull_request_reviews": (
                        desired.can_approve_pull_request_reviews
                        if desired.can_approve_pull_request_reviews is not None
                        else current.get("can_approve_pull_request_reviews")
                    ),
                }
                await actions.async_set_github_actions_default_workflow_permissions_repository(
                    owner=owner, repo=repo, data=body
                )

    result.applied = result.changed and not dry_run
    return [result]


TIER = RepoTier(name="actions", configured=_configured, reconcile=reconcile)
