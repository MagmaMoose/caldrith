"""Tests for the reconcile planner (target repo enumeration)."""

from __future__ import annotations

import httpx
import respx
from githubkit import GitHub

from caldrith.reconcile.planner import TargetRepo, account_type, list_target_repos


def _repo(owner: str, name: str) -> dict:
    return {
        "id": hash(name) % 100000,
        "name": name,
        "owner": {"login": owner, "id": 1},
    }


@respx.mock
async def test_list_target_repos_single_page() -> None:
    respx.get("https://api.github.com/installation/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 2,
                "repositories": [_repo("acme", "widget"), _repo("acme", "gadget")],
            },
        )
    )

    async with GitHub("token") as client:
        targets = await list_target_repos(client)

    assert targets == [
        TargetRepo(owner="acme", name="widget"),
        TargetRepo(owner="acme", name="gadget"),
    ]


@respx.mock
async def test_list_target_repos_paginates() -> None:
    full_page = [_repo("acme", f"r{i}") for i in range(100)]
    respx.get(
        "https://api.github.com/installation/repositories",
        params={"page": "1"},
    ).mock(return_value=httpx.Response(200, json={"total_count": 101, "repositories": full_page}))
    respx.get(
        "https://api.github.com/installation/repositories",
        params={"page": "2"},
    ).mock(
        return_value=httpx.Response(
            200, json={"total_count": 101, "repositories": [_repo("acme", "last")]}
        )
    )

    async with GitHub("token") as client:
        targets = await list_target_repos(client)

    assert len(targets) == 101
    assert targets[-1] == TargetRepo(owner="acme", name="last")


@respx.mock
async def test_list_target_repos_skips_archived() -> None:
    respx.get("https://api.github.com/installation/repositories").mock(
        return_value=httpx.Response(
            200,
            json={
                "total_count": 3,
                "repositories": [
                    _repo("acme", "active"),
                    {**_repo("acme", "old"), "archived": True},
                    _repo("acme", "fresh"),
                ],
            },
        )
    )

    async with GitHub("token") as client:
        targets = await list_target_repos(client)

    assert targets == [TargetRepo("acme", "active"), TargetRepo("acme", "fresh")]


@respx.mock
async def test_account_type_resolved() -> None:
    respx.get("https://api.github.com/app/installations/42").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 42,
                "account": {"login": "acme", "id": 1, "type": "Organization"},
            },
        )
    )

    async with GitHub("token") as client:
        kind = await account_type(client, 42)

    assert kind == "Organization"


def test_target_repo_full_name() -> None:
    assert TargetRepo(owner="acme", name="widget").full_name == "acme/widget"
