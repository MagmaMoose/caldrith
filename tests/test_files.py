"""Tests for the file provisioner (opens/reuses a PR to add required workflows)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import ManagedFile, SafeSettingsConfig
from caldrith.reconcile.files import (
    FileProvisioner,
    _managed_branch,
    _repo_is_ahead,
    _resolve_bases,
    reconcile,
)
from caldrith.reconcile.planner import TargetRepo


def _wf(action: str, version: str, sha: str = "a" * 40) -> str:
    """A tiny workflow file pinning one SHA-pinned action with a ``# vX.Y.Z`` comment."""
    return f"name: W\njobs:\n  j:\n    steps:\n      - uses: {action}@{sha} # v{version}\n"


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
_STAGING_BRANCH = f"{_BRANCH}-staging"  # ci/caldrith/managed-files-staging (a non-default sibling)
_STAGING_BRANCH_REF = f"{_REPO}/git/ref/heads/{_STAGING_BRANCH}"
_STAGING_REF = f"{_REPO}/git/ref/heads/staging"  # the staging base branch itself
_STAGING_COMPARE = f"{_REPO}/compare/staging...{_STAGING_BRANCH}"

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert create_ref.called and commit.called and create_pr.called
    assert _added_paths(commit) == [_PATH]  # one signed commit adds the file
    assert result.files == [_PATH]
    assert result.pr_url == "https://github.com/acme/widget/pull/1"
    assert result.applied is True


@respx.mock
async def test_heals_stale_slash_branch_directory_conflict() -> None:
    # A leftover ci/caldrith/managed-files/staging ref (Caldrith's retired slash naming)
    # makes ci/caldrith/managed-files a *directory* in the ref store, so creating the flat
    # branch 422s with "cannot lock ref". The provisioner must delete the stale nested ref
    # and retry — WITHOUT touching the ci/caldrith/managed-files-staging hyphen sibling that
    # merely shares the name prefix — so the repo still gets its PR instead of silently
    # falling out of sync for every base.
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=httpx.Response(404))
    _no_branch()
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(404))  # flat branch absent
    respx.get(_MAIN_REF).mock(
        return_value=httpx.Response(200, json={"object": {"sha": "deadbeef"}})
    )
    create_ref = respx.post(_REFS).mock(
        side_effect=[
            httpx.Response(  # directory/file conflict caused by the nested slash ref
                422,
                json={
                    "message": (
                        "cannot lock ref 'refs/heads/ci/caldrith/managed-files': "
                        "'refs/heads/ci/caldrith/managed-files/staging' exists"
                    )
                },
            ),
            httpx.Response(201, json={}),  # retry succeeds once the nested ref is gone
        ]
    )
    matching = respx.get(f"{_REPO}/git/matching-refs/heads/{_BRANCH}").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"ref": f"refs/heads/{_BRANCH}/staging"},  # the nested slash leftover
                {"ref": f"refs/heads/{_STAGING_BRANCH}"},  # hyphen sibling — must survive
            ],
        )
    )
    del_slash = respx.delete(f"{_REPO}/git/refs/heads/{_BRANCH}/staging").mock(
        return_value=httpx.Response(204)
    )
    del_sibling = respx.delete(f"{_REPO}/git/refs/heads/{_STAGING_BRANCH}").mock(
        return_value=httpx.Response(204)
    )
    respx.get(_CONTENTS, params={"ref": _BRANCH}).mock(return_value=httpx.Response(404))
    commit = _mock_commit()
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[]))
    create_pr = respx.post(_PULLS).mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/acme/widget/pull/1"})
    )

    async with GitHub("token") as client:
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert matching.called
    assert del_slash.called  # stale nested slash ref dropped
    assert not del_sibling.called  # hyphen sibling left untouched
    assert create_ref.call_count == 2  # 422, then success after the heal
    assert commit.called and create_pr.called
    assert result.pr_url == "https://github.com/acme/widget/pull/1"
    assert result.applied is True


@respx.mock
async def test_noop_when_already_matching() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file(CONTENT))  # matches
    _no_branch()
    commit = _mock_commit()

    async with GitHub("token") as client:
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert not commit.called
    assert result.changed is False


@respx.mock
async def test_create_only_does_not_overwrite_existing() -> None:
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(return_value=_file("different"))
    _no_branch()
    commit = _mock_commit()

    async with GitHub("token") as client:
        [result] = await FileProvisioner(client).apply(
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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

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
        [result] = await FileProvisioner(client, dry_run=True).apply(
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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

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
        [result] = await FileProvisioner(client, dry_run=True).apply(
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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

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
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[{"number": 9, "html_url": "u"}]))
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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

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
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [_managed()])

    assert not commit.called  # orphan already absent + security staged -> empty changeset
    assert result.applied is True


def test_repo_is_ahead_compares_pinned_action_versions() -> None:
    admin = _wf("magmamoose/chargate", "2.1.0")
    assert _repo_is_ahead(_wf("magmamoose/chargate", "2.2.0", "b" * 40), admin) is True
    assert _repo_is_ahead(_wf("magmamoose/chargate", "2.0.0", "b" * 40), admin) is False
    assert _repo_is_ahead(_wf("magmamoose/chargate", "2.1.0", "b" * 40), admin) is False  # equal
    # an action the admin file doesn't declare is ignored
    assert _repo_is_ahead(_wf("actions/checkout", "99.0.0", "b" * 40), admin) is False
    assert _repo_is_ahead("name: W\n", admin) is False  # no pins


@respx.mock
async def test_upgrade_only_skips_when_repo_pin_is_ahead() -> None:
    # Dependabot / Renovate bumped chargate in the repo past the admin baseline — caldrith
    # must NOT revert it (no downgrade).
    admin = _wf("magmamoose/chargate", "2.1.0")
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(
        return_value=_file(_wf("magmamoose/chargate", "2.2.0", "c" * 40))
    )
    _no_branch()
    commit = _mock_commit()

    managed = ManagedFile(path=_PATH, content=admin, upgrade_only=True)
    async with GitHub("token") as client:
        [result] = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert result.files == [] and result.changed is False
    assert not commit.called  # nothing provisioned -> no commit, no downgrade PR


@respx.mock
async def test_upgrade_only_updates_when_repo_pin_is_behind() -> None:
    admin = _wf("magmamoose/chargate", "2.2.0")
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(
        return_value=_file(_wf("magmamoose/chargate", "2.1.0", "c" * 40))
    )
    _no_branch()

    managed = ManagedFile(path=_PATH, content=admin, upgrade_only=True)
    async with GitHub("token") as client:
        [result] = await FileProvisioner(client, dry_run=True).apply(
            TargetRepo("acme", "widget"), [managed]
        )

    assert result.files == [_PATH]  # repo behind the baseline -> upgrade it


@respx.mock
async def test_upgrade_only_still_syncs_non_version_drift() -> None:
    # Same pinned version, but the admin content changed elsewhere -> repo isn't "ahead",
    # so the file is still synced (upgrade_only only guards against *downgrades*).
    admin = _wf("magmamoose/chargate", "2.1.0") + "permissions:\n  contents: read\n"
    _mock_repo()
    respx.get(_CONTENTS, params={"ref": "main"}).mock(
        return_value=_file(_wf("magmamoose/chargate", "2.1.0", "c" * 40))
    )
    _no_branch()

    managed = ManagedFile(path=_PATH, content=admin, upgrade_only=True)
    async with GitHub("token") as client:
        [result] = await FileProvisioner(client, dry_run=True).apply(
            TargetRepo("acme", "widget"), [managed]
        )

    assert result.files == [_PATH]  # same version, other drift -> sync


# ---------------------------------------------------------------------------
# multi-base: a file's ``branches`` provisions one managed PR per base branch
# ---------------------------------------------------------------------------


def test_managed_branch_names_default_and_extra_bases() -> None:
    # The default branch keeps the stable branch name (existing PRs untouched); any other
    # base gets a *sibling* branch (hyphen, not "/") so it has its own managed PR.
    assert _managed_branch("main", "main") == _BRANCH
    assert _managed_branch("staging", "main") == f"{_BRANCH}-staging"
    assert _managed_branch("main", "develop") == f"{_BRANCH}-main"  # "main" isn't default here
    # A non-default managed branch must NOT be nested under the default one: Git can't hold
    # both a ref and a ref path-prefixed by it (a directory/file conflict), so once
    # ci/caldrith/managed-files exists a ".../staging" child could never be created. A "/"
    # inside a base name is flattened to "-" for the same reason.
    assert not _managed_branch("staging", "main").startswith(f"{_BRANCH}/")
    assert _managed_branch("release/v2", "main") == f"{_BRANCH}-release-v2"


def test_resolve_bases_defaults_dedups_and_preserves_order() -> None:
    plain = ManagedFile(path=_PATH, content=CONTENT)
    assert _resolve_bases(plain, "main") == ["main"]  # unset -> the default branch only
    both = ManagedFile(path=_PATH, content=CONTENT, branches=["default", "staging"])
    assert _resolve_bases(both, "main") == ["main", "staging"]  # "default" -> the default branch
    dupe = ManagedFile(path=_PATH, content=CONTENT, branches=["default", "main"])
    assert _resolve_bases(dupe, "main") == ["main"]  # "default" and its name collapse to one
    reordered = ManagedFile(path=_PATH, content=CONTENT, branches=["staging", "default"])
    assert _resolve_bases(reordered, "develop") == ["staging", "develop"]  # order preserved


@respx.mock
async def test_branches_opens_one_pr_per_base() -> None:
    # A file targeting [default, staging], absent on both branches, opens two managed PRs:
    # one on ci/caldrith/managed-files (base main) and one on the namespaced staging branch.
    _mock_repo()
    respx.get(_CONTENTS).mock(return_value=httpx.Response(404))  # absent on every ref
    _no_branch()  # main...managed-branch compare: no managed branch yet
    respx.get(_STAGING_COMPARE).mock(return_value=httpx.Response(404))
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(404))  # main managed branch absent
    respx.get(_STAGING_BRANCH_REF).mock(return_value=httpx.Response(404))  # staging managed absent
    respx.get(_MAIN_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "m"}}))
    respx.get(_STAGING_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "s"}}))
    create_ref = respx.post(_REFS).mock(return_value=httpx.Response(201, json={}))
    _mock_commit()
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[]))  # no open PR on either head
    create_pr = respx.post(_PULLS).mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/acme/widget/pull/1"})
    )

    managed = ManagedFile(path=_PATH, content=CONTENT, branches=["default", "staging"])
    async with GitHub("token") as client:
        results = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert {r.base for r in results} == {"main", "staging"}
    assert all(r.applied and r.files == [_PATH] for r in results)
    # Both managed branches were created, each from its own base branch...
    created = {json.loads(c.request.content)["ref"] for c in create_ref.calls}
    assert created == {f"refs/heads/{_BRANCH}", f"refs/heads/{_STAGING_BRANCH}"}
    # ...and a PR was opened against each base branch from the matching head.
    pr_bodies = [json.loads(c.request.content) for c in create_pr.calls]
    assert {b["base"] for b in pr_bodies} == {"main", "staging"}
    assert {b["head"] for b in pr_bodies} == {_BRANCH, _STAGING_BRANCH}


@respx.mock
async def test_missing_base_branch_skipped_while_default_provisions() -> None:
    # branches=[default, staging] but the repo has no staging branch: the default branch
    # still gets its PR; staging is skipped gracefully (no branch created, no PR opened).
    _mock_repo()
    respx.get(_CONTENTS).mock(return_value=httpx.Response(404))  # absent everywhere
    _no_branch()
    respx.get(_STAGING_COMPARE).mock(return_value=httpx.Response(404))
    respx.get(_BRANCH_REF).mock(return_value=httpx.Response(404))
    respx.get(_STAGING_BRANCH_REF).mock(return_value=httpx.Response(404))
    respx.get(_MAIN_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "m"}}))
    respx.get(_STAGING_REF).mock(return_value=httpx.Response(404))  # no staging branch in the repo
    create_ref = respx.post(_REFS).mock(return_value=httpx.Response(201, json={}))
    _mock_commit()
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[]))
    create_pr = respx.post(_PULLS).mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/acme/widget/pull/1"})
    )

    managed = ManagedFile(path=_PATH, content=CONTENT, branches=["default", "staging"])
    async with GitHub("token") as client:
        results = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    by_base = {r.base: r for r in results}
    assert by_base["main"].applied and by_base["main"].pr_url is not None
    assert by_base["staging"].applied is False and by_base["staging"].changed is False
    assert by_base["staging"].files == []  # nothing provisioned into the missing branch
    # Only the default managed branch was created; only one PR opened, against main.
    created = {json.loads(c.request.content)["ref"] for c in create_ref.calls}
    assert created == {f"refs/heads/{_BRANCH}"}
    assert create_pr.call_count == 1
    assert json.loads(create_pr.calls.last.request.content)["base"] == "main"


@respx.mock
async def test_staging_only_file_never_touches_default_branch() -> None:
    # branches=[staging] provisions only into staging; the default branch is left alone.
    _mock_repo()
    respx.get(_CONTENTS).mock(return_value=httpx.Response(404))
    respx.get(_STAGING_COMPARE).mock(return_value=httpx.Response(404))
    respx.get(_STAGING_BRANCH_REF).mock(return_value=httpx.Response(404))
    respx.get(_STAGING_REF).mock(return_value=httpx.Response(200, json={"object": {"sha": "s"}}))
    main_ref = respx.get(_MAIN_REF).mock(  # the default branch's base ref must never be read
        return_value=httpx.Response(200, json={"object": {"sha": "m"}})
    )
    create_ref = respx.post(_REFS).mock(return_value=httpx.Response(201, json={}))
    _mock_commit()
    respx.get(_PULLS).mock(return_value=httpx.Response(200, json=[]))
    create_pr = respx.post(_PULLS).mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/acme/widget/pull/1"})
    )

    managed = ManagedFile(path=_PATH, content=CONTENT, branches=["staging"])
    async with GitHub("token") as client:
        results = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert len(results) == 1 and results[0].base == "staging"
    assert not main_ref.called  # only staging is provisioned; the default branch is untouched
    created_ref = json.loads(create_ref.calls.last.request.content)["ref"]
    assert created_ref == f"refs/heads/{_STAGING_BRANCH}"
    assert json.loads(create_pr.calls.last.request.content)["base"] == "staging"


@respx.mock
async def test_multi_base_noop_when_matching_on_both() -> None:
    # The file already matches on both base branches -> no commit on either managed branch.
    _mock_repo()
    respx.get(_CONTENTS).mock(return_value=_file(CONTENT))  # matches on every ref
    _no_branch()
    respx.get(_STAGING_COMPARE).mock(return_value=httpx.Response(404))
    commit = _mock_commit()

    managed = ManagedFile(path=_PATH, content=CONTENT, branches=["default", "staging"])
    async with GitHub("token") as client:
        results = await FileProvisioner(client).apply(TargetRepo("acme", "widget"), [managed])

    assert {r.base for r in results} == {"main", "staging"}
    assert all(r.changed is False for r in results)
    assert not commit.called  # nothing to do on either base


@respx.mock
async def test_reconcile_tags_each_base_in_dry_run() -> None:
    # The reconcile adapter emits one TierResult per base, tagged with the base branch when
    # more than one is in play, so the dry-run Check Run distinguishes the two PRs.
    _mock_repo()
    respx.get(_CONTENTS).mock(return_value=httpx.Response(404))  # absent -> would be provisioned
    _no_branch()
    respx.get(_STAGING_COMPARE).mock(return_value=httpx.Response(404))

    config = SafeSettingsConfig(
        files=[ManagedFile(path=_PATH, content=CONTENT, branches=["default", "staging"])]
    )
    async with GitHub("token") as client:
        rows = await reconcile(client, TargetRepo("acme", "widget"), config, dry_run=True)

    assert [r.tier for r in rows] == ["files", "files"]
    by_tag = {r.notes[0]: r for r in rows}  # the first note is the base tag when multi-base
    assert set(by_tag) == {"base: main", "base: staging"}
    assert all(_PATH in r.notes for r in rows)
    assert all(r.changed and not r.applied for r in rows)  # dry-run: drift detected, not applied
