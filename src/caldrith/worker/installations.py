"""Paginated enumeration of every installation the App can see.

``apps.list_installations`` returns one page (default 30) per call, so a deployment
with more than 30 installations would silently lose the rest in any fan-out built on a
single call. Mirrors the pagination in ``reconcile.planner.list_target_repos``.
"""

from __future__ import annotations

from typing import Any

from githubkit import GitHub

from caldrith.github_json import response_json


async def paginate_installations(client: GitHub[Any]) -> list[dict[str, Any]]:
    """Return every installation across all pages of ``apps.list_installations``."""
    installations: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    while True:
        batch = response_json(
            await client.rest.apps.async_list_installations(per_page=per_page, page=page)
        )
        installations.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return installations
