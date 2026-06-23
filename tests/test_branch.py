"""Tests for the branch protection applier (canonicalisation + idempotent PUT)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import (
    BranchConfig,
    BranchProtection,
    BranchRestrictions,
    RequiredPullRequestReviews,
    RequiredStatusChecks,
)
from caldrith.reconcile.branch import BranchProtectionApplier
from caldrith.reconcile.planner import TargetRepo

_REPO = "https://api.github.com/repos/acme/widget"
_PROT = "https://api.github.com/repos/acme/widget/branches/main/protection"
_SIG = "https://api.github.com/repos/acme/widget/branches/main/protection/required_signatures"


def _sig(enabled: bool = False) -> httpx.Response:
    """A GET commit-signature-protection response ({enabled})."""
    return httpx.Response(200, json={"enabled": enabled, "url": _SIG})


def _repo(**overrides: Any) -> dict[str, Any]:
    """A GET repo response with the fields the applier reads (archived, default_branch)."""
    base: dict[str, Any] = {
        "name": "widget",
        "owner": {"login": "acme"},
        "default_branch": "main",
        "archived": False,
    }
    base.update(overrides)
    return base


def _live(**overrides: Any) -> dict[str, Any]:
    """A GET branch-protection response (note the {enabled} wrappers GitHub returns)."""
    base: dict[str, Any] = {
        "url": _PROT,
        "required_status_checks": {
            "strict": True,
            "contexts": ["ci/build"],
            "checks": [{"context": "ci/build"}],
            "url": "x",
        },
        "enforce_admins": {"enabled": True, "url": "x"},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 2,
            "require_last_push_approval": False,
            "url": "x",
        },
        "required_linear_history": {"enabled": False},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": False},
    }
    base.update(overrides)
    return base


def _not_protected() -> httpx.Response:
    return httpx.Response(404, json={"message": "Branch not protected"})


def _matching_config() -> BranchConfig:
    return BranchConfig(
        name="main",
        protection=BranchProtection(
            enforce_admins=True,
            required_status_checks=RequiredStatusChecks(strict=True, contexts=["ci/build"]),
            required_pull_request_reviews=RequiredPullRequestReviews(
                dismiss_stale_reviews=True, required_approving_review_count=2
            ),
        ),
    )


@respx.mock
async def test_noop_when_protection_matches() -> None:
    # Desired equals the live state, including the {enabled}-wrapped booleans.
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=httpx.Response(200, json=_live()))
    respx.get(_SIG).mock(return_value=_sig(False))
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))

    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(
            TargetRepo("acme", "widget"), _matching_config()
        )

    assert not put.called  # canonical-equal -> idempotent no-op (proves {enabled} flattening)
    assert result.changed is False
    assert result.action == "noop"


@respx.mock
async def test_applies_when_unprotected() -> None:
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=_not_protected())
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))

    cfg = BranchConfig(
        name="main",
        protection=BranchProtection(
            enforce_admins=True,
            required_pull_request_reviews=RequiredPullRequestReviews(
                required_approving_review_count=1
            ),
        ),
    )
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert put.called
    body = json.loads(put.calls.last.request.content)
    assert body["enforce_admins"] is True
    assert body["required_pull_request_reviews"]["required_approving_review_count"] == 1
    assert body["restrictions"] is None  # required PUT key; null when no restrictions declared
    assert result.changed is True and result.applied is True and result.action == "update"


@respx.mock
async def test_detects_review_count_drift() -> None:
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=httpx.Response(200, json=_live()))  # live count = 2
    respx.get(_SIG).mock(return_value=_sig(False))
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))

    cfg = BranchConfig(
        name="main",
        protection=BranchProtection(
            enforce_admins=True,
            required_status_checks=RequiredStatusChecks(strict=True, contexts=["ci/build"]),
            required_pull_request_reviews=RequiredPullRequestReviews(
                dismiss_stale_reviews=True,
                required_approving_review_count=1,  # want 1, live is 2
            ),
        ),
    )
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert put.called
    assert result.changed is True


@respx.mock
async def test_dry_run_never_writes() -> None:
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=_not_protected())
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))

    cfg = BranchConfig(name="main", protection=BranchProtection(enforce_admins=True))
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client, dry_run=True).apply(
            TargetRepo("acme", "widget"), cfg
        )

    assert not put.called
    assert result.changed is True and result.applied is False
    assert result.payload is not None


@respx.mock
async def test_remove_protection() -> None:
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=httpx.Response(200, json=_live()))
    delete = respx.delete(_PROT).mock(return_value=httpx.Response(204))

    cfg = BranchConfig(name="main", protection=None)
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert delete.called
    assert result.action == "remove" and result.changed is True


@respx.mock
async def test_remove_is_noop_when_already_unprotected() -> None:
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=_not_protected())
    delete = respx.delete(_PROT).mock(return_value=httpx.Response(204))

    cfg = BranchConfig(name="main", protection=None)
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert not delete.called
    assert result.action == "noop" and result.changed is False


@respx.mock
async def test_default_branch_resolved() -> None:
    respx.get(_REPO).mock(
        return_value=httpx.Response(200, json=_repo(default_branch="develop")),
    )
    prot = respx.get("https://api.github.com/repos/acme/widget/branches/develop/protection").mock(
        return_value=_not_protected()
    )
    put = respx.put("https://api.github.com/repos/acme/widget/branches/develop/protection").mock(
        return_value=httpx.Response(200, json=_live())
    )

    cfg = BranchConfig(name="default", protection=BranchProtection(enforce_admins=True))
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert prot.called and put.called
    assert result.branch == "develop"


@respx.mock
async def test_archived_repo_is_noop() -> None:
    """A stray webhook for an archived repo skips writes (matches RepositoryApplier)."""
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo(archived=True)))
    prot = respx.get(_PROT).mock(return_value=_not_protected())
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))

    cfg = BranchConfig(name="main", protection=BranchProtection(enforce_admins=True))
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert not prot.called  # the GET protection call never happens
    assert not put.called
    assert result.action == "noop" and result.changed is False and result.applied is False


@respx.mock
async def test_noop_flattens_enabled_for_non_enforce_admins_bools() -> None:
    """{enabled}-flatten must work for every _BOOL_FIELDS member, not just enforce_admins.

    Live has required_linear_history / allow_force_pushes / allow_deletions /
    required_conversation_resolution wrapped as {"enabled": True}; desired declares them
    flat as True. If the canonicalisation only handled enforce_admins, the comparison
    would spuriously diff and PUT.
    """
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(
        return_value=httpx.Response(
            200,
            json=_live(
                required_linear_history={"enabled": True},
                allow_force_pushes={"enabled": True},
                allow_deletions={"enabled": True},
                required_conversation_resolution={"enabled": True},
            ),
        )
    )
    respx.get(_SIG).mock(return_value=_sig(False))
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))

    cfg = BranchConfig(
        name="main",
        protection=BranchProtection(
            enforce_admins=True,
            required_status_checks=RequiredStatusChecks(strict=True, contexts=["ci/build"]),
            required_pull_request_reviews=RequiredPullRequestReviews(
                dismiss_stale_reviews=True, required_approving_review_count=2
            ),
            required_linear_history=True,
            allow_force_pushes=True,
            allow_deletions=True,
            required_conversation_resolution=True,
        ),
    )
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert not put.called
    assert result.action == "noop" and result.changed is False


@respx.mock
async def test_restrictions_sent_in_put_body() -> None:
    """A declared push-restrictions block is canonicalised into the PUT body."""
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=_not_protected())
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))

    cfg = BranchConfig(
        name="main",
        protection=BranchProtection(
            enforce_admins=True,
            restrictions=BranchRestrictions(users=["octocat"], teams=["core"], apps=["dependabot"]),
        ),
    )
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert put.called
    body = json.loads(put.calls.last.request.content)
    assert body["restrictions"] == {
        "users": ["octocat"],
        "teams": ["core"],
        "apps": ["dependabot"],
    }
    assert result.applied is True


@respx.mock
async def test_required_signatures_enabled_via_dedicated_endpoint() -> None:
    """required_signatures drift triggers the commit-signature endpoint, not the PUT."""
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=httpx.Response(200, json=_live()))
    respx.get(_SIG).mock(return_value=_sig(False))  # signatures currently OFF
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))
    create_sig = respx.post(_SIG).mock(return_value=httpx.Response(200, json=_sig(True).json()))

    cfg = _matching_config()  # equals live protection EXCEPT we now want signatures on
    cfg.protection.required_signatures = True  # type: ignore[union-attr]
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert not put.called  # the PUT body is unchanged — only signatures differ
    assert create_sig.called
    assert result.changed is True and result.applied is True


@respx.mock
async def test_required_signatures_only_on_unprotected_branch() -> None:
    """Regression: signatures-only on an unprotected branch must PUT protection first.

    Commit-signature protection attaches to existing branch protection. When signatures
    is the ONLY declared protection and the branch is currently unprotected, the canonical
    desired equals the all-off actual (``changed`` is False) — so without first PUTting a
    (possibly all-off) protection, the signature POST 404s on an unprotected branch and the
    declared security control is silently never enforced.
    """
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(return_value=_not_protected())  # branch has no protection yet
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))
    create_sig = respx.post(_SIG).mock(return_value=httpx.Response(200, json=_sig(True).json()))

    cfg = BranchConfig(name="main", protection=BranchProtection(required_signatures=True))
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert put.called  # an all-off protection is established so the signature POST can attach
    body = json.loads(put.calls.last.request.content)
    assert body["enforce_admins"] is False
    assert body["required_pull_request_reviews"] is None
    assert create_sig.called  # signatures actually enabled, not silently dropped
    assert result.changed is True and result.applied is True


@respx.mock
async def test_contexts_sort_is_idempotent() -> None:
    """Live contexts in a different order from desired must not cause spurious drift."""
    respx.get(_REPO).mock(return_value=httpx.Response(200, json=_repo()))
    respx.get(_PROT).mock(
        return_value=httpx.Response(
            200,
            json=_live(
                required_status_checks={
                    "strict": True,
                    "contexts": ["ci/lint", "ci/build"],  # reversed from desired
                    "checks": [{"context": "ci/lint"}, {"context": "ci/build"}],
                    "url": "x",
                },
            ),
        )
    )
    respx.get(_SIG).mock(return_value=_sig(False))
    put = respx.put(_PROT).mock(return_value=httpx.Response(200, json=_live()))

    cfg = BranchConfig(
        name="main",
        protection=BranchProtection(
            enforce_admins=True,
            required_status_checks=RequiredStatusChecks(
                strict=True, contexts=["ci/build", "ci/lint"]
            ),
            required_pull_request_reviews=RequiredPullRequestReviews(
                dismiss_stale_reviews=True, required_approving_review_count=2
            ),
        ),
    )
    async with GitHub("token") as client:
        result = await BranchProtectionApplier(client).apply(TargetRepo("acme", "widget"), cfg)

    assert not put.called
    assert result.action == "noop" and result.changed is False
