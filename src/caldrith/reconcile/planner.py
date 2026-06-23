"""Enumerate the repositories an installation should reconcile.

The set of accessible repos is the same for Organization and User installations:
``GET /installation/repositories`` (``apps.list_repos_accessible_to_installation``)
returns exactly the repos the App was granted, regardless of account type. We page
through it fully.

Account-type awareness is preserved as a seam: :func:`account_type` resolves whether
the installation targets an Organization or a User, which later tiers (suborg overlays,
org-only features) will branch on. P1 does not branch on it for the repository block.
"""

from __future__ import annotations

from dataclasses import dataclass

from githubkit import GitHub

from caldrith.github_json import response_json


@dataclass(frozen=True)
class TargetRepo:
    """A repository selected for reconciliation."""

    owner: str
    name: str
    visibility: str | None = None  # public | private | internal — used by visibility overlays

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


async def account_type(client: GitHub, installation_id: int) -> str:
    """Return the installation's account type, e.g. ``"Organization"`` or ``"User"``.

    Resolved via ``apps.get_installation``. Kept as a distinct call so deferred,
    account-type-specific tiers (suborgs, org rulesets) can branch on it without
    re-plumbing the planner.
    """
    response = await client.rest.apps.async_get_installation(installation_id)
    account = response_json(response).get("account") or {}
    # ``account`` is a union (user vs enterprise); SimpleUser carries ``type``.
    return account.get("type") or "Organization"


async def list_target_repos(client: GitHub) -> list[TargetRepo]:
    """Return every repository accessible to the installation.

    Works identically for Organization and User installations. Pages through
    ``GET /installation/repositories`` until exhausted.
    """
    targets: list[TargetRepo] = []
    page = 1
    per_page = 100
    while True:
        response = await client.rest.apps.async_list_repos_accessible_to_installation(
            per_page=per_page,
            page=page,
        )
        repositories = response_json(response).get("repositories") or []
        for repo in repositories:
            # Archived repos reject settings PATCHes (403/422); never target them.
            if repo.get("archived"):
                continue
            targets.append(
                TargetRepo(
                    owner=repo["owner"]["login"],
                    name=repo["name"],
                    # `visibility` distinguishes internal from private; fall back to the
                    # `private` bool when the field is absent.
                    visibility=repo.get("visibility")
                    or ("private" if repo.get("private") else "public"),
                )
            )
        if len(repositories) < per_page:
            break
        page += 1
    return targets
