"""Reconcile branch protection for a single branch.

GitHub's branch-protection API is asymmetric: the ``GET`` response wraps several
booleans as ``{"enabled": bool}`` and returns verbose objects, while the ``PUT`` body
takes flat values. We canonicalise BOTH the desired config and the live state into one
comparable shape, ``PUT`` the full desired protection only when it differs (idempotent),
and ``DELETE`` protection when a branch declares ``protection: null``.

Declarative + full-replace, like github/safe-settings: the ``protection`` block is the
COMPLETE desired protection; omitted fields fall back to GitHub's "off" defaults.

Supported through the branch-protection PUT: ``required_pull_request_reviews``,
``required_status_checks``, ``enforce_admins``, ``restrictions`` (push restrictions —
users / teams / apps), ``required_linear_history``, ``allow_force_pushes``,
``allow_deletions``, ``required_conversation_resolution``, ``block_creations``,
``lock_branch``, ``allow_fork_syncing``. ``required_signatures`` is reconciled via its
own endpoint (commit signature protection) since the PUT body does not accept it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import BranchConfig, BranchProtection, RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo

# Booleans returned by GET wrapped as ``{"enabled": bool}`` but sent flat in the PUT.
_BOOL_FIELDS = (
    "enforce_admins",
    "required_linear_history",
    "allow_force_pushes",
    "allow_deletions",
    "required_conversation_resolution",
    "block_creations",
    "lock_branch",
    "allow_fork_syncing",
)
_RPR_BOOL_FIELDS = (
    "dismiss_stale_reviews",
    "require_code_owner_reviews",
    "require_last_push_approval",
)


@dataclass
class BranchResult:
    """Outcome of reconciling one branch's protection."""

    repo: str
    branch: str
    changed: bool
    applied: bool
    action: str  # "update" | "remove" | "noop"
    payload: dict[str, Any] | None  # the PUT body, for the dry-run check summary

    @property
    def has_changes(self) -> bool:
        return self.changed


def _canonical_rsc(rsc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Canonicalise required_status_checks to ``{strict, contexts}`` (or None)."""
    if not rsc:
        return None
    return {"strict": bool(rsc.get("strict", False)), "contexts": sorted(rsc.get("contexts") or [])}


def _canonical_rpr(rpr: dict[str, Any] | None) -> dict[str, Any] | None:
    """Canonicalise required_pull_request_reviews to the supported sub-fields (or None)."""
    if not rpr:
        return None
    out: dict[str, Any] = {f: bool(rpr.get(f, False)) for f in _RPR_BOOL_FIELDS}
    out["required_approving_review_count"] = int(rpr.get("required_approving_review_count") or 0)
    return out


def _names(items: Any, *keys: str) -> list[str]:
    """Sorted login/slug list from either desired (``[str]``) or live (``[{login}]``)."""
    out: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            value = next((item[k] for k in keys if item.get(k)), None)
        else:
            value = item
        if value:
            out.append(value)
    return sorted(out)


def _canonical_restrictions(restrictions: dict[str, Any] | None) -> dict[str, Any] | None:
    """Canonicalise push ``restrictions`` to ``{users, teams, apps}`` of names (or None).

    Works for both the desired block (lists of login/slug strings) and the live GET
    response (lists of ``{login}`` / ``{slug}`` objects).
    """
    if restrictions is None:
        return None
    return {
        "users": _names(restrictions.get("users"), "login", "name"),
        "teams": _names(restrictions.get("teams"), "slug", "name"),
        "apps": _names(restrictions.get("apps"), "slug", "name"),
    }


def _canonical_desired(protection: BranchProtection) -> dict[str, Any]:
    """Full-replace desired state: objects-or-None, booleans-or-False.

    ``required_signatures`` is excluded — it is reconciled via its own endpoint, not the
    branch-protection PUT.
    """
    declared = protection.model_dump(exclude_none=True)
    out: dict[str, Any] = {
        "required_status_checks": _canonical_rsc(declared.get("required_status_checks")),
        "required_pull_request_reviews": _canonical_rpr(
            declared.get("required_pull_request_reviews")
        ),
        "restrictions": _canonical_restrictions(declared.get("restrictions")),
    }
    for field_name in _BOOL_FIELDS:
        out[field_name] = bool(declared.get(field_name, False))
    return out


def _canonical_actual(actual: dict[str, Any] | None) -> dict[str, Any]:
    """Canonicalise the live GET protection (or None = unprotected) into desired shape."""
    if actual is None:
        out: dict[str, Any] = {
            "required_status_checks": None,
            "required_pull_request_reviews": None,
            "restrictions": None,
        }
        out.update({f: False for f in _BOOL_FIELDS})
        return out
    out = {
        "required_status_checks": _canonical_rsc(actual.get("required_status_checks")),
        "required_pull_request_reviews": _canonical_rpr(
            actual.get("required_pull_request_reviews")
        ),
        "restrictions": _canonical_restrictions(actual.get("restrictions")),
    }
    for field_name in _BOOL_FIELDS:
        value = actual.get(field_name)
        out[field_name] = (
            bool(value.get("enabled", False)) if isinstance(value, dict) else bool(value)
        )
    return out


def _put_body(desired_canon: dict[str, Any]) -> dict[str, Any]:
    """The PUT body — exactly the canonical desired state (restrictions included)."""
    return dict(desired_canon)


class BranchProtectionApplier:
    """Reconciles a single branch's protection against the desired block."""

    def __init__(self, client: GitHub, *, dry_run: bool = False) -> None:
        self._client = client
        self._dry_run = dry_run

    async def _get_repo(self, target: TargetRepo) -> dict[str, Any]:
        return response_json(
            await self._client.rest.repos.async_get(owner=target.owner, repo=target.name)
        )

    def _resolve_branch(self, repo: dict[str, Any], name: str) -> str:
        """Resolve ``default`` to the repo's default branch; pass other names through."""
        if name != "default":
            return name
        return repo["default_branch"]

    async def _get_protection(self, target: TargetRepo, branch: str) -> dict[str, Any] | None:
        """Return the live protection, or None if the branch is unprotected (404)."""
        try:
            response = await self._client.rest.repos.async_get_branch_protection(
                owner=target.owner, repo=target.name, branch=branch
            )
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                return None
            raise
        # Robust to githubkit either raising on 404 (above) or returning the response.
        if getattr(response, "status_code", 200) == 404:
            return None
        return response_json(response)

    async def _get_signatures(self, target: TargetRepo, branch: str) -> bool:
        """Return whether commit signature protection is enabled (404 -> not enabled)."""
        try:
            response = await self._client.rest.repos.async_get_commit_signature_protection(
                owner=target.owner, repo=target.name, branch=branch
            )
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                return False
            raise
        if getattr(response, "status_code", 200) == 404:
            return False
        return bool(response_json(response).get("enabled", False))

    async def apply(self, target: TargetRepo, branch_cfg: BranchConfig) -> BranchResult:
        """Reconcile ``branch_cfg`` on ``target`` (PUT/DELETE unless dry-run)."""
        repo = await self._get_repo(target)
        branch = self._resolve_branch(repo, branch_cfg.name)
        # Archived repos reject branch-protection writes (403/422); treat as a no-op so a
        # stray webhook on one (bypassing list_target_repos' filter) never errors the job.
        if repo.get("archived"):
            return BranchResult(target.full_name, branch, False, False, "noop", None)
        actual = await self._get_protection(target, branch)

        if branch_cfg.protection is None:
            if actual is None:
                return BranchResult(target.full_name, branch, False, False, "noop", None)
            if not self._dry_run:
                # Deleting protection also clears commit-signature protection.
                await self._client.rest.repos.async_delete_branch_protection(
                    owner=target.owner, repo=target.name, branch=branch
                )
            return BranchResult(target.full_name, branch, True, not self._dry_run, "remove", None)

        desired_canon = _canonical_desired(branch_cfg.protection)
        changed = desired_canon != _canonical_actual(actual)
        body = _put_body(desired_canon)

        # required_signatures lives behind its own endpoint; full-replace means an
        # omitted value is "off" (delete protection).
        desired_sig = bool(branch_cfg.protection.required_signatures)
        actual_sig = await self._get_signatures(target, branch) if actual is not None else False
        sig_changed = desired_sig != actual_sig

        if not (changed or sig_changed) or self._dry_run:
            action = "update" if (changed or sig_changed) else "noop"
            return BranchResult(
                target.full_name,
                branch,
                changed or sig_changed,
                False,
                action,
                body if changed else None,
            )

        if changed:
            await self._client.rest.repos.async_update_branch_protection(
                owner=target.owner,
                repo=target.name,
                branch=branch,
                data=body,  # type: ignore[arg-type]
            )
        if sig_changed:
            if desired_sig:
                await self._client.rest.repos.async_create_commit_signature_protection(
                    owner=target.owner, repo=target.name, branch=branch
                )
            else:
                await self._client.rest.repos.async_delete_commit_signature_protection(
                    owner=target.owner, repo=target.name, branch=branch
                )
        return BranchResult(target.full_name, branch, True, True, "update", body)


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Uniform adapter: reconcile branch protection for every configured branch."""
    if not config.branches:
        return []
    applier = BranchProtectionApplier(client, dry_run=dry_run)
    results: list[TierResult] = []
    for branch_cfg in config.branches:
        res = await applier.apply(target, branch_cfg)
        results.append(
            TierResult(
                tier="branches",
                scope=res.repo,
                changed=res.changed,
                applied=res.applied,
                notes=[f"{res.branch}: {res.action}"] if res.changed else [],
            )
        )
    return results


TIER = RepoTier(name="branches", configured=lambda c: bool(c.branches), reconcile=reconcile)
