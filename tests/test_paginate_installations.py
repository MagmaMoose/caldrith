"""Tests for the App-installations paginator."""

from __future__ import annotations

import httpx
import respx
from githubkit import GitHub

from caldrith.worker.installations import paginate_installations


@respx.mock
async def test_paginate_installations_walks_every_page() -> None:
    """Two full pages of 100 + a final short page must yield 250 installations."""

    def _page(n: int) -> list[dict[str, object]]:
        return [
            {"id": base, "account": {"login": f"org-{base}", "type": "Organization"}}
            for base in range((n - 1) * 100 + 1, (n - 1) * 100 + 1 + n_size(n))
        ]

    def n_size(page: int) -> int:
        return 100 if page < 3 else 50

    route = respx.get("https://api.github.com/app/installations").mock(
        side_effect=[
            httpx.Response(200, json=_page(1)),
            httpx.Response(200, json=_page(2)),
            httpx.Response(200, json=_page(3)),
        ]
    )

    installations = await paginate_installations(GitHub("token"))

    assert len(installations) == 250
    assert installations[0]["id"] == 1
    assert installations[-1]["id"] == 250
    assert route.call_count == 3


@respx.mock
async def test_paginate_installations_single_page_stops() -> None:
    """A first page shorter than per_page must terminate without a second request."""
    route = respx.get("https://api.github.com/app/installations").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "account": {"login": "Acme", "type": "Organization"}}],
        )
    )

    installations = await paginate_installations(GitHub("token"))

    assert [i["id"] for i in installations] == [1]
    assert route.call_count == 1
