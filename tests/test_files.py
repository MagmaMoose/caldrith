"""Tests for the file provisioner (opens/reuses a PR to add required workflows)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import ManagedFile
from caldrith.reconcile.files import FileProvisioner
from caldrith.reconcile.planner import TargetRepo

_REPO = "https://api.github.com/repos/acme/widget"
_PATH = ".github/workflows/security.yml"
_CONTENTS = f"{_REPO}/contents/{_PATH}"
_MAIN_REF = f"{_REPO}/git/ref/heads/main"
_BRANCH = "ci/caldrith/managed-files"
_BRANCH_REF = f"{_REPO}/git/ref/heads/{_BRANCH}"
_REFS = f"{_REPO}/git/refs"
_REF = f"{_REPO}/git/refs/heads/{_BRANCH}"  # delete-ref endpoint (plural "refs")
_PULLS = f"{_REPO}/pulls"
_COMPARE = f"{_REPO}/compare/main...{_BRANCH}"
_GRAPHQL = "https://api.github.com/graphql"

CONTENT = "name: Security\non: [pull_request]\n"


def _no_branch() -> None:
    """Mock the base..branch compare as 404 — no managed branch exists yet to prune."""
    respx.get(_COMPARE).mock(return_value=httpx.Response(404))


def _compare(*files: tuple[str, str]) -> None:
    """Mock the base..branch compare to report the given ``(filename, status)`` pairs."""
    respx.get(_COMPARE).mock(
        return_value=httpx.Response(
            200, json={"files": [{"filename": path, "status": status} for path, status in files]}
        )
    )


def _on_branch(url: str, content: str, sha: str = "x") -> None:
    """Mock a content GET at the managed branch returning ``content`` with blob ``sha``."""
    respx.get(url, params={"ref": _BRANCH}).mock(return_value=_file(content, sha=sha))


def _branch_exists() -> None:
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "b"}}))


def _open_pr(number: int = 9) -> None:
    """Mock the open-PR list as one reusable managed PR (number 9 by default)."""
    url = f"https://github.com/acme/widget/pull/{number}"
    respx.get(_PULLS).mock(
        return_value=httpx.Response(200, json=[{"number": number, "html_url": url}])
    )


def _mock_commit(oid: str = "newoid") -> respx.Route:
    """Mock the GraphQL createCommitOnBranch mutation (the signed-commit endpoint)."""
    return respx.post(_GRAPHQL).mock(
        return_value=httpx.Response(
            200, json={"data": {"createCommitOnBranch": {"commit": {"oid": oid}}}}
        )
    )


def _committed(route: respx.Route) -> dict:
    """Return the createCommitOnBranch `fileChanges` from the last GraphQL request."""
    body = json.loads(route.calls.last.request.content)
    return body["variables"]["input"]["fileChanges"]


def _added_paths(route: respx.Route) -> list[str]:
    return [a["path"] for a in _committed(route).get("additions", [])]


def _deleted_paths(route: respx.Route) -> list[str]:
    return [d["path"] for d in _committed(route).get("deletions", [])]


def _added_content(route: respx.Route, path: str) -> str:
    add = next(a for a in _committed(route)["additions"] if a["path"] == path)
    return base64.b64decode(add["contents"]).decode()


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
    _no_branch()
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(404))  # branch absent
    respx.get(_MAIN_REF).mock(
        return_value=httpx.Response(200, json={"object": {"sha": "deadbeef"}})
    )
    create_ref = respx.post(_REFS).mock(return_value=httpx.Response(201, json={}))
    respx.get(_CONTENTS, params={"ref": _BRANCH}).mock(return_value=httpx.Response(404))
    commit = _mock_commit()
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[]))  # no open PR
    create_pr = respx.post(_PULLS).mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/acme/widget/pull/1"})
    )

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert create_ref.called and commit.called and create_pr.called
    assert _added_paths(commit) == [_PATH]  # one signed commit adds the file
    assert result.files == [_PATH]
    assert result.pr_url == "https://github.com/acme/widget/pull/1"
    assert result.applied is True


@respx.mock
async def test_noop_when_already_matching() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file(CONTENT))  # matches
    _no_branch()
    commit = _mock_commit()

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert not commit.called
    assert result.changed is False


@respx.mock
async def test_create_only_does_not_overwrite_existing() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file("different"))
    _no_branch()
    commit = _mock_commit()

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(
            TargetRepo("acme", "widget"), [_managed(create_only=True)]
        )

    assert not commit.called  # create_only -> never overwrite
    assert result.changed is False


@respx.mock
async def test_updates_drifted_and_reuses_open_pr() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file("old content"))
    # The branch carries the (desired) file as a modification of the repo's own copy —
    # it stays in the desired set, so it is updated, not pruned.
    _compare((_PATH, "modified"))
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "b"}}))
    respx.get(_CONTENTS, params={"ref": _BRANCH}).mock(
        return_value=_file("old content", sha="branchsha")  # branch still has old -> update
    )
    commit = _mock_commit()
    respx.get(_PULLS).mock(  # a PR is already open -> reuse, don't duplicate
        return_value=httpx.Response(
            200, json=[{"html_url": "https://github.com/acme/widget/pull/7"}]
        )
    )
    create_pr = respx.post(_PULLS).mock(return_value=httpx.Response(201, json={"html_url": "x"}))

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert commit.called and not create_pr.called  # one signed commit; existing PR reused
    assert _added_content(commit, _PATH) == CONTENT  # updated to the declared content
    assert result.pr_url == "https://github.com/acme/widget/pull/7"


@respx.mock
async def test_dry_run_never_writes() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    _no_branch()
    commit = _mock_commit()
    create_pr = respx.post(_PULLS).mock(return_value=httpx.Response(201, json={"html_url": "x"}))

    async with GitHub("token") as client:
        result = await FileProvisioner(client, dry_run=True).apply(
            TargetRepo("acme", "widget"), [_managed()]
        )

    assert not commit.called and not create_pr.called
    assert result.files == [_PATH] and result.applied is False


@respx.mock
async def test_skip_repos_excludes_file_for_matching_repo() -> None:
    # A per-file skip_repos glob excludes THIS file from the repo without dropping the
    # repo from other management. No content is even read.
    _mock_repo()
    _no_branch()
    contents = respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    commit = _mock_commit()
    create_pr = respx.post(_PULLS).mock(return_value=httpx.Response(201, json={"html_url": "x"}))

    managed = ManagedFile(path=_PATH, content=CONTENT, skip_repos=["wid*"])
    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert not contents.called  # skipped before any lookup
    assert not commit.called and not create_pr.called
    assert result.changed is False


@respx.mock
async def test_create_only_skips_when_sibling_extension_exists() -> None:
    # Managed release.yaml is create_only; the repo already has release.yml (same
    # workflow, other extension) -> do NOT add a duplicate that double-fires.
    rel_yaml = f"{_REPO}/contents/.github/workflows/release.yaml"
    rel_yml = f"{_REPO}/contents/.github/workflows/release.yml"
    _mock_repo()
    _no_branch()
    respx.get(rel_yaml, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    respx.get(rel_yml, params={"ref": "main"}).mock(return_value=_file("name: Release\n"))
    commit = _mock_commit()

    managed = ManagedFile(
        path=".github/workflows/release.yaml",
        content="name: Release (caldrith)\n",
        create_only=True,
    )
    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert not commit.called  # sibling release.yml present -> no duplicate
    assert result.changed is False


@respx.mock
async def test_empty_repo_skipped_gracefully() -> None:
    # An empty repo (no commit on the default branch) has nothing to branch from:
    # skip without raising, so one empty repo can't break the org reconcile.
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    _no_branch()
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(404))
    respx.get(_MAIN_REF).mock(return_value=httpx.Response(404))  # no base commit
    create_ref = respx.post(_REFS).mock(return_value=httpx.Response(201, json={}))
    commit = _mock_commit()
    create_pr = respx.post(_PULLS).mock(return_value=httpx.Response(201, json={"html_url": "x"}))

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert not create_ref.called and not commit.called and not create_pr.called
    assert result.changed is False and result.applied is False


@respx.mock
async def test_prunes_added_orphan_and_reuses_pr() -> None:
    # settings.yml now declares only security.yml. The open PR's branch still carries a
    # release.yaml caldrith added net-new in a previous run -> it is deleted from the
    # branch, leaving the PR proposing only security.yml.
    rel = ".github/workflows/release.yaml"
    rel_url = f"{_REPO}/contents/{rel}"
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    _compare((_PATH, "added"), (rel, "added"))  # security still declared (kept), release dropped
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "b"}}))
    respx.get(rel_url, params={"ref": _BRANCH}).mock(
        return_value=_file("name: Release\n", sha="relsha")
    )
    # security.yml already staged correctly on the branch -> not re-added.
    respx.get(_CONTENTS, params={"ref": _BRANCH}).mock(return_value=_file(CONTENT))
    commit = _mock_commit()
    respx.get(_PULLS).mock(  # existing open PR -> reuse, don't duplicate
        return_value=httpx.Response(
            200, json=[{"html_url": "https://github.com/acme/widget/pull/9"}]
        )
    )
    create_pr = respx.post(_PULLS).mock(return_value=httpx.Response(201, json={"html_url": "x"}))

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert commit.called and not create_pr.called  # one signed commit; PR reused
    assert _deleted_paths(commit) == [rel] and _added_paths(commit) == []  # only the orphan dropped
    assert result.files == [_PATH] and result.removed == [rel]
    assert result.pr_url == "https://github.com/acme/widget/pull/9"
    assert result.applied is True


@respx.mock
async def test_reverts_modified_orphan_to_base() -> None:
    # A non-create_only file caldrith had updated is dropped from the config while
    # another managed file (security.yml) is still provisioned, so the branch survives.
    # On the branch the dropped file is a *modification* of the repo's own copy, so it is
    # reverted to the default branch's content — the repo's file itself is never deleted.
    legacy = ".github/workflows/legacy.yml"
    legacy_url = f"{_REPO}/contents/{legacy}"
    _mock_repo()
    # security.yml has drifted on the base, so it stays "needed" -> the branch is kept.
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file("drifted"))
    _compare((_PATH, "modified"), (legacy, "modified"))
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "b"}}))
    respx.get(_CONTENTS, params={"ref": _BRANCH}).mock(return_value=_file(CONTENT, sha="secsha"))
    respx.get(legacy_url, params={"ref": _BRANCH}).mock(
        return_value=_file("caldrith edit\n", sha="legsha")
    )
    respx.get(legacy_url, params={"ref": "main"}).mock(return_value=_file("repo original\n"))
    commit = _mock_commit()
    respx.get(_PULLS).mock(
        return_value=httpx.Response(
            200, json=[{"number": 9, "html_url": "https://github.com/acme/widget/pull/9"}]
        )
    )

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    # The orphan is reverted (re-added with the base content), not deleted; security.yml
    # is already staged so it is not re-added.
    assert _added_paths(commit) == [legacy] and _deleted_paths(commit) == []
    assert _added_content(commit, legacy) == "repo original\n"
    assert result.files == [_PATH] and result.removed == [legacy]
    assert result.applied is True


@respx.mock
async def test_dry_run_reports_removed_without_writing() -> None:
    rel = ".github/workflows/release.yaml"
    _mock_repo()
    # security.yml is in sync with the base, so nothing new is provisioned.
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file(CONTENT))
    _compare((rel, "added"))
    commit = _mock_commit()

    async with GitHub("token") as client:
        result = await FileProvisioner(client, dry_run=True).apply(
            TargetRepo("acme", "widget"), [_managed()]
        )

    assert not commit.called
    assert result.files == [] and result.removed == [rel]
    assert result.applied is False


@respx.mock
async def test_prune_to_empty_closes_pr_and_deletes_branch() -> None:
    # The lone file the branch carried (release.yaml, with security.yml skipped as on
    # chargate) is dropped from the config. Pruning would empty the PR, so instead the PR
    # is closed and the branch deleted wholesale — no empty PR is left dangling.
    rel = ".github/workflows/release.yaml"
    rel_url = f"{_REPO}/contents/{rel}"
    _mock_repo()
    contents_main = respx.get(_CONTENTS, params={"ref": "main"}).mock(
        return_value=httpx.Response(404)
    )
    _compare((rel, "added"))
    delete_file = respx.delete(rel_url).mock(return_value=httpx.Response(200, json={}))
    respx.get(_PULLS).mock(
        return_value=httpx.Response(
            200, json=[{"number": 9, "html_url": "https://github.com/acme/widget/pull/9"}]
        )
    )
    close = respx.patch(f"{_PULLS}/9").mock(return_value=httpx.Response(200, json={}))
    delete_ref = respx.delete(_REF).mock(return_value=httpx.Response(204))

    managed = ManagedFile(path=_PATH, content=CONTENT, skip_repos=["wid*"])
    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert close.called and b"closed" in close.calls.last.request.content
    assert delete_ref.called
    assert not delete_file.called  # branch dropped wholesale, not pruned file-by-file
    assert not contents_main.called  # security.yml skipped before any base lookup
    assert result.files == [] and result.removed == [rel]
    assert result.pr_url is None and result.applied is True
    assert result.closed_pr_url == "https://github.com/acme/widget/pull/9"  # surfaced for audit


@respx.mock
async def test_close_to_empty_with_no_open_pr_still_deletes_branch() -> None:
    # The PR was already closed/merged by a human but the branch still carries an orphan.
    # _close_branch must still delete the lingering branch (no PR to close).
    rel = ".github/workflows/release.yaml"
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    _compare((rel, "added"))
    close = respx.patch(url__regex=rf"{_PULLS}/\d+").mock(return_value=httpx.Response(200, json={}))
    delete_ref = respx.delete(_REF).mock(return_value=httpx.Response(204))
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[]))  # no open PR

    managed = ManagedFile(path=_PATH, content=CONTENT, skip_repos=["wid*"])
    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert delete_ref.called and not close.called  # branch deleted, no PR to close
    assert result.removed == [rel] and result.closed_pr_url is None
    assert result.pr_url is None and result.applied is True


@respx.mock
async def test_close_branch_swallows_delete_ref_404() -> None:
    # A concurrently-deleted branch (404 on delete_ref) must not abort the run.
    rel = ".github/workflows/release.yaml"
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    _compare((rel, "added"))
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[]))
    delete_ref = respx.delete(_REF).mock(return_value=httpx.Response(404))

    managed = ManagedFile(path=_PATH, content=CONTENT, skip_repos=["wid*"])
    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert delete_ref.called and result.applied is True and result.removed == [rel]


@respx.mock
async def test_close_branch_surfaces_real_delete_ref_failure() -> None:
    # A real failure (e.g. a deletion-protected branch -> non-404) must surface, not be
    # masked after the PR was closed. The PR is closed only AFTER a successful delete.
    rel = ".github/workflows/release.yaml"
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    _compare((rel, "added"))
    close = respx.patch(url__regex=rf"{_PULLS}/\d+").mock(return_value=httpx.Response(200, json={}))
    respx.get(_PULLS).mock(
        return_value=httpx.Response(200, json=[{"number": 9, "html_url": "u"}])
    )
    respx.delete(_REF).mock(return_value=httpx.Response(422, json={"message": "protected"}))

    managed = ManagedFile(path=_PATH, content=CONTENT, skip_repos=["wid*"])
    async with GitHub("token") as client:
        with pytest.raises(RequestFailed):
            await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])
    assert not close.called  # PR left open so the next reconcile retries


@respx.mock
async def test_compare_error_other_than_404_propagates() -> None:
    # _orphans swallows only 404 (no branch). Any other compare error must propagate so a
    # rate-limit/permission failure can't silently widen into "never prune". (403 rather
    # than 5xx so githubkit doesn't retry-with-backoff and slow the test.)
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file(CONTENT))
    respx.get(_COMPARE).mock(return_value=httpx.Response(403, json={"message": "forbidden"}))

    async with GitHub("token") as client:
        with pytest.raises(RequestFailed):
            await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])


@respx.mock
async def test_prunes_multiple_added_orphans_in_one_run() -> None:
    # Two no-longer-declared files are both deleted in a single run; security.yml stays.
    rel = ".github/workflows/release.yaml"
    legacy = ".github/workflows/legacy.yml"
    rel_url, legacy_url = f"{_REPO}/contents/{rel}", f"{_REPO}/contents/{legacy}"
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))  # absent
    _compare((_PATH, "added"), (rel, "added"), (legacy, "added"))
    _branch_exists()
    _on_branch(_CONTENTS, CONTENT)  # security already staged
    _on_branch(rel_url, "r", sha="rs")
    _on_branch(legacy_url, "l", sha="ls")
    commit = _mock_commit()
    _open_pr()

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert _deleted_paths(commit) == [rel, legacy] and _added_paths(commit) == []  # one commit
    assert result.removed == [rel, legacy] and result.files == [_PATH]


@respx.mock
async def test_modified_orphan_deleted_when_repo_dropped_its_own_copy() -> None:
    # A modified orphan whose file the repo has since deleted on the default branch is
    # deleted from the branch (not left behind), so the prune is real.
    legacy = ".github/workflows/legacy.yml"
    legacy_url = f"{_REPO}/contents/{legacy}"
    _mock_repo()
    # security.yml has drifted, so it stays needed -> the branch survives.
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file("drifted"))
    _compare((_PATH, "modified"), (legacy, "modified"))
    _branch_exists()
    _on_branch(_CONTENTS, CONTENT, sha="secsha")
    _on_branch(legacy_url, "caldrith edit\n", sha="legsha")
    # The repo deleted its own copy on the default branch.
    respx.get(legacy_url, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    commit = _mock_commit()
    _open_pr()

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert _deleted_paths(commit) == [legacy] and _added_paths(commit) == []  # base gone -> delete
    assert result.removed == [legacy]


@respx.mock
async def test_modified_orphan_noop_when_branch_already_matches_base() -> None:
    # If the branch copy already equals the default branch's content, there is nothing to
    # restore — neither a revert PUT nor a delete is issued.
    legacy = ".github/workflows/legacy.yml"
    legacy_url = f"{_REPO}/contents/{legacy}"
    _mock_repo()
    # security.yml has drifted, so it stays needed -> the branch survives.
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file("drifted"))
    _compare((_PATH, "modified"), (legacy, "modified"))
    _branch_exists()
    _on_branch(_CONTENTS, CONTENT, sha="secsha")
    _on_branch(legacy_url, "same\n", sha="legsha")
    respx.get(legacy_url, params={"ref": "main"}).mock(return_value=_file("same\n"))
    commit = _mock_commit()
    _open_pr()

    async with GitHub("token") as client:
        await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert not commit.called  # branch already matches base -> empty changeset, no commit


@respx.mock
async def test_prune_skips_orphan_already_absent_on_branch() -> None:
    # If an orphan compare reported is no longer on the branch (gone between calls), the
    # per-file prune no-ops gracefully instead of issuing a delete with no sha.
    rel = ".github/workflows/release.yaml"
    rel_url = f"{_REPO}/contents/{rel}"
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))  # needed
    _compare((_PATH, "added"), (rel, "added"))
    _branch_exists()
    _on_branch(_CONTENTS, CONTENT)  # security staged
    # The orphan compare reported is no longer on the branch (gone between calls).
    respx.get(rel_url, params={"ref": _BRANCH}).mock(return_value=httpx.Response(404))
    commit = _mock_commit()
    _open_pr()

    async with GitHub("token") as client:
        result = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert not commit.called  # orphan already absent + security staged -> empty changeset
    assert result.applied is True
