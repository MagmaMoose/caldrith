"""Tests for the open-PR re-base sweep (``reconcile.pr_update``).

Drives :func:`update_open_prs` against a respx-mocked GitHub: PRs behind their base get
an "Update branch" call, up-to-date PRs are left alone, and a PR that cannot be updated
(409/422 conflict) is recorded as skipped without aborting the sweep.
"""

from __future__ import annotations

import httpx
import respx
from githubkit import GitHub

from caldrith.reconcile.pr_update import update_open_prs

_PULLS = "https://api.github.com/repos/acme/admin/pulls"


def _pr(number: int, *, base: str = "main", head: str = "feat") -> dict:
    return {
        "number": number,
        "base": {"ref": base},
        "head": {"ref": head, "label": f"acme:{head}"},
    }


def _mock_pulls(*prs: dict) -> None:
    respx.get(_PULLS, params={"state": "open", "per_page": "100", "page": "1"}).mock(
        return_value=httpx.Response(200, json=list(prs))
    )


def _mock_compare(base: str, head: str, behind_by: int) -> None:
    respx.get(f"https://api.github.com/repos/acme/admin/compare/{base}...acme:{head}").mock(
        return_value=httpx.Response(200, json={"behind_by": behind_by, "ahead_by": 1})
    )


@respx.mock
async def test_behind_pr_is_updated() -> None:
    _mock_pulls(_pr(7, head="feat"))
    _mock_compare("main", "feat", behind_by=3)
    update = respx.put(f"{_PULLS}/7/update-branch").mock(
        return_value=httpx.Response(202, json={"message": "Updating pull request branch."})
    )

    summary = await update_open_prs(GitHub("token"), owner="acme", repo="admin")

    assert update.called
    assert summary.updated == [7]
    assert summary.up_to_date == [] and summary.skipped == []
    assert summary.any_updated is True


@respx.mock
async def test_up_to_date_pr_is_not_touched() -> None:
    _mock_pulls(_pr(8, head="ready"))
    _mock_compare("main", "ready", behind_by=0)
    update = respx.put(f"{_PULLS}/8/update-branch").mock(return_value=httpx.Response(202, json={}))

    summary = await update_open_prs(GitHub("token"), owner="acme", repo="admin")

    assert not update.called
    assert summary.up_to_date == [8]
    assert summary.updated == []


@respx.mock
async def test_conflict_pr_is_skipped_not_fatal() -> None:
    _mock_pulls(_pr(9, head="stale"), _pr(10, head="clean"))
    _mock_compare("main", "stale", behind_by=2)
    _mock_compare("main", "clean", behind_by=1)
    # The first PR can't be fast-forwarded (merge conflict) -> 409; the sweep continues.
    respx.put(f"{_PULLS}/9/update-branch").mock(return_value=httpx.Response(409, json={}))
    respx.put(f"{_PULLS}/10/update-branch").mock(return_value=httpx.Response(202, json={}))

    summary = await update_open_prs(GitHub("token"), owner="acme", repo="admin")

    assert summary.updated == [10]
    assert summary.skipped == [9]


@respx.mock
async def test_compare_failure_is_skipped_not_fatal() -> None:
    # The behind-check (compare_commits) itself can fail — e.g. a fork head the App can't
    # read or a deleted head branch (404). That must be skipped, not abort the sweep, so a
    # later healthy PR is still updated.
    _mock_pulls(_pr(11, head="forky"), _pr(12, head="normal"))
    respx.get("https://api.github.com/repos/acme/admin/compare/main...acme:forky").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    _mock_compare("main", "normal", behind_by=2)
    update12 = respx.put(f"{_PULLS}/12/update-branch").mock(
        return_value=httpx.Response(202, json={})
    )

    summary = await update_open_prs(GitHub("token"), owner="acme", repo="admin")

    assert update12.called
    assert summary.updated == [12]
    assert summary.skipped == [11]
    assert summary.up_to_date == []


@respx.mock
async def test_no_open_prs_is_a_noop() -> None:
    _mock_pulls()

    summary = await update_open_prs(GitHub("token"), owner="acme", repo="admin")

    assert summary.updated == [] and summary.up_to_date == [] and summary.skipped == []
    assert summary.any_updated is False
