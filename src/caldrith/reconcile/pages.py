"""Reconcile GitHub Pages configuration for a repository.

If Pages is not enabled and the config declares a source / build type, Caldrith enables
it (``create_pages_site``); if it is already enabled, declared fields (``build_type``,
``source_branch``/``source_path``, ``cname``, ``https_enforced``) are compared and the
site information updated only on drift. Only declared fields participate, so a partial
declaration never resets the rest. Pages is never *disabled* by this tier.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import PagesConfig, RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


def _configured(config: RepoScoped) -> bool:
    if config.pages is None:
        return False
    return bool(config.pages.model_dump(exclude_unset=True, exclude_none=True))


def _source(pages: PagesConfig) -> dict[str, str]:
    source: dict[str, str] = {}
    if pages.source_branch is not None:
        source["branch"] = pages.source_branch
    if pages.source_path is not None:
        source["path"] = pages.source_path
    return source


def _update_body(pages: PagesConfig) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if pages.build_type is not None:
        body["build_type"] = pages.build_type
    source = _source(pages)
    if source:
        body["source"] = source
    if pages.cname is not None:
        body["cname"] = pages.cname
    if pages.https_enforced is not None:
        body["https_enforced"] = pages.https_enforced
    return body


def _drifted(pages: PagesConfig, live: dict[str, Any]) -> bool:
    source = live.get("source") or {}
    if pages.build_type is not None and live.get("build_type") != pages.build_type:
        return True
    if pages.source_branch is not None and source.get("branch") != pages.source_branch:
        return True
    if pages.source_path is not None and source.get("path") != pages.source_path:
        return True
    if pages.cname is not None and live.get("cname") != pages.cname:
        return True
    return pages.https_enforced is not None and live.get("https_enforced") != pages.https_enforced


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile Pages on ``target`` (enable/update unless dry-run)."""
    if config.pages is None:
        return []
    pages = config.pages
    repos = client.rest.repos
    result = TierResult(tier="pages", scope=target.full_name)

    try:
        response = await repos.async_get_pages(owner=target.owner, repo=target.name)
        live: dict[str, Any] | None = response_json(response)
        if getattr(response, "status_code", 200) == 404:
            live = None
    except RequestFailed as exc:
        if exc.response.status_code != 404:
            raise
        live = None

    if live is None:
        result.changed = True
        result.notes.append("enable pages")
        if not dry_run:
            create_body: dict[str, Any] = {}
            if pages.build_type is not None:
                create_body["build_type"] = pages.build_type
            source = _source(pages)
            if source:
                create_body["source"] = source
            await repos.async_create_pages_site(
                owner=target.owner, repo=target.name, data=create_body
            )
            extras = {
                k: v for k, v in _update_body(pages).items() if k in ("cname", "https_enforced")
            }
            if extras:
                await repos.async_update_information_about_pages_site(
                    owner=target.owner, repo=target.name, data=extras
                )
            result.applied = True
        return [result]

    if _drifted(pages, live):
        result.changed = True
        result.notes.append("update pages")
        if not dry_run:
            await repos.async_update_information_about_pages_site(
                owner=target.owner, repo=target.name, data=_update_body(pages)
            )
            result.applied = True
    return [result]


TIER = RepoTier(name="pages", configured=_configured, reconcile=reconcile)
