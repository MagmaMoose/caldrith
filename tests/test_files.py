"""Tests for the file provisioner (opens/reuses a PR to add required workflows)."""

from __future__ import annotations

import base64

import httpx
import respx
from githubkit import GitHub

from caldrith.config.schema import ManagedFile
from caldrith.reconcile.files import FileProvisioner
from caldrith.reconcile.planner import TargetRepo

_REPO = "https://api.github.com/repos/acme/widget"
_PATH = ".github/workflows/security.yml"
_CONTENTS = f"{_REPO}/contents/{_PATH}"
_MAIN_REF = f"{_REPO}/git/ref/heads/main"
_BRANCH_REF = f"{_REPO}/git/ref/heads/caldrith/managed-files"
_REFS = f"{_REPO}/git/refs"
_PULLS = f"{_REPO}/pulls"

CONTENT = "name: Security\non: [pull_request]\n"


def _file(content: str, sha: str = "abc") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "type": "file",
            "content": base64.b64encode(content.encode()).decode(),
            "sha": sha,
        },
    )


def _mock_repo(default_branch: str = "main") -> None:
    respx.get(_REPO).mock(
        return_value=httpx.Response(
            200,
            json={"name": "widget", "owner": {"login": "acme"}, "default_branch": default_branch},
        )
    )


def _managed(create_only: bool = False) -> ManagedFile:
    return ManagedFile(path=_PATH, content=CONTENT, create_only=create_only)


@respx.mock
async def test_provisions_when_absent_opens_pr() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(404))  # branch absent
    respx.get(_MAIN_REF).mock(
        return_value=httpx.Response(200, json={"object": {"sha": "deadbeef"}})
    )
    create_ref = respx.post(_REFS).mock(return_value=httpx.Response(201, json={}))
    respx.get(_CONTENTS, params={"ref": "caldrith/managed-files"}).mock(
        return_value=httpx.Response(404)
    )
    put = respx.put(_CONTENTS).mock(return_value=httpx.Response(201, json={}))
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[]))  # no open PR
    create_pr = respx.post(_PULLS).mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/acme/widget/pull/1"})
    )

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert create_ref.called and put.called and create_pr.called
    assert result.files == [_PATH]
    assert result.pr_url == "https://github.com/acme/widget/pull/1"
    assert result.applied is True


@respx.mock
async def test_noop_when_already_matching() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file(CONTENT))  # matches
    put = respx.put(_CONTENTS).mock(return_value=httpx.Response(201, json={}))

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert not put.called
    assert result.changed is False


@respx.mock
async def test_create_only_does_not_overwrite_existing() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file("different"))
    put = respx.put(_CONTENTS).mock(return_value=httpx.Response(201, json={}))

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(
            TargetRepo("acme", "widget"), [_managed(create_only=True)]
        )

    assert not put.called  # create_only -> never overwrite
    assert result.changed is False


@respx.mock
async def test_updates_drifted_and_reuses_open_pr() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file("old content"))
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "b"}}))
    respx.get(_CONTENTS, params={"ref": "caldrith/managed-files"}).mock(
        return_value=_file("old content", sha="branchsha")  # branch still has old -> PUT
    )
    put = respx.put(_CONTENTS).mock(return_value=httpx.Response(200, json={}))
    respx.get(_PULLS).mock(  # a PR is already open -> reuse, don't duplicate
        return_value=httpx.Response(
            200, json=[{"html_url": "https://github.com/acme/widget/pull/7"}]
        )
    )
    create_pr = respx.post(_PULLS).mock(return_value=httpx.Response(201, json={"html_url": "x"}))

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert put.called
    assert not create_pr.called  # reused existing PR
    body = put.calls.last.request.content
    assert b"branchsha" in body  # updated with the existing blob sha
    assert result.pr_url == "https://github.com/acme/widget/pull/7"


@respx.mock
async def test_dry_run_never_writes() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    put = respx.put(_CONTENTS).mock(return_value=httpx.Response(201, json={}))
    create_pr = respx.post(_PULLS).mock(return_value=httpx.Response(201, json={"html_url": "x"}))

    async with GitHub("token") as client:
        result = await FileProvisioner(client, dry_run=True).apply(
            TargetRepo("acme", "widget"), [_managed()]
        )

    assert not put.called and not create_pr.called
    assert result.files == [_PATH] and result.applied is False
