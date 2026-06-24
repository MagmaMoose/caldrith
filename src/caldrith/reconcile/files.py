"""Provision required files (workflows) into a repo via a pull request.

Caldrith never pushes to the default branch directly — it opens (and reuses) a single
PR from a stable branch (``ci/caldrith/managed-files``) that adds, updates, and prunes
the declared files, so a human/automation merges it. This is how required workflows
(the Chargate gate, a Diatreme release) get rolled out org-wide.

A run's adds, updates and prunes go into **one commit**, authored through the GraphQL
``createCommitOnBranch`` mutation so GitHub signs it on the App's behalf — the managed
PR's commits show as **Verified**. (The REST contents API does not sign a third-party
App's commits, so they would otherwise show as unverified.)

Idempotent and non-destructive:
- A file already matching ``content`` on the default branch is skipped.
- ``create_only`` files are written only when absent (never overwrite a repo's own),
  and a ``.yml``/``.yaml`` sibling counts as present — so a managed ``release.yaml``
  won't be added next to a repo's existing ``release.yml``.
- ``skip_repos`` globs exclude a file from specific repos (a per-file escape hatch).
- An empty repo (no commit on its default branch) is skipped gracefully — there is
  nothing to branch a PR from yet.
- The PR branch is reused; files already correct on it aren't re-committed; an open PR
  is not duplicated. So re-running while a PR is pending is a no-op.
- Files the branch still carries that the config no longer declares — a workflow
  dropped from ``settings.yml``, or a repo newly matched by ``skip_repos`` — are pruned
  so the PR reflects only the currently-required files. Caldrith touches only its own
  ``ci/caldrith/managed-files`` branch: a file it added net-new is deleted, and a repo
  file it had merely updated is reverted to the default branch's version — the repo's
  own files are never removed. If pruning leaves nothing to provision, the now-empty PR
  is closed and the branch deleted rather than left dangling. Boundary: removing the
  *last* managed file (an empty or absent ``files:`` block) skips the files tier
  entirely, so it neither prunes nor closes — keep at least one file declared, or close
  any leftover managed PRs by hand.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import ManagedFile, RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo
from caldrith.reconcile.selection import matches_any

_BRANCH = "ci/caldrith/managed-files"
_COMMIT_MESSAGE = "chore: provision required workflows (caldrith)"
_PR_TITLE = "Caldrith: provision required workflows"
_PR_BODY = (
    "Caldrith manages these files org-wide. Merging brings this repo in line with the "
    "organisation's required workflows; Caldrith keeps them in sync thereafter."
)

# GraphQL mutation that authors a (GitHub-signed, hence Verified) commit on a branch.
_COMMIT_MUTATION = """
mutation ($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit {
      oid
    }
  }
}
"""


def _sibling_ext_path(path: str) -> str | None:
    """The same path with ``.yml`` <-> ``.yaml`` swapped, or ``None`` if not YAML.

    GitHub Actions accepts either extension, so a repo's ``release.yml`` and a managed
    ``release.yaml`` are the *same* workflow under two names. Treating the sibling as
    present stops ``create_only`` from provisioning a duplicate that double-fires.
    """
    if path.endswith(".yaml"):
        return path[: -len(".yaml")] + ".yml"
    if path.endswith(".yml"):
        return path[: -len(".yml")] + ".yaml"
    return None


def _b64(text: str) -> str:
    """Base64-encode ``text`` (the encoding GitHub's file-content APIs expect)."""
    return base64.b64encode(text.encode()).decode()


@dataclass
class FilesResult:
    """Outcome of provisioning managed files into a repo."""

    repo: str
    files: list[str] = field(default_factory=list)  # paths created/updated this run
    removed: list[str] = field(default_factory=list)  # paths pruned from the branch
    pr_url: str | None = None
    closed_pr_url: str | None = None  # set when pruning emptied (and closed) the PR
    applied: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.files or self.removed)


class FileProvisioner:
    """Opens/updates a PR adding the declared files to a single repo."""

    def __init__(self, client: GitHub, *, dry_run: bool = False) -> None:
        self._client = client
        self._dry_run = dry_run

    async def _get_file(
        self, target: TargetRepo, path: str, ref: str
    ) -> tuple[str | None, str | None]:
        """Return ``(content, blob_sha)`` for ``path`` at ``ref``, or ``(None, None)``."""
        try:
            response = await self._client.rest.repos.async_get_content(
                owner=target.owner, repo=target.name, path=path, ref=ref
            )
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                return None, None
            raise
        data = response_json(response)
        if not isinstance(data, dict) or data.get("type") != "file" or "content" not in data:
            return None, None  # a directory / symlink / submodule — treat as absent
        return base64.b64decode(data["content"]).decode(), data.get("sha")

    async def _sibling_present(self, target: TargetRepo, path: str, ref: str) -> bool:
        """True if the ``.yml``/``.yaml`` sibling of ``path`` exists at ``ref``."""
        sibling = _sibling_ext_path(path)
        if sibling is None:
            return False
        content, _ = await self._get_file(target, sibling, ref)
        return content is not None

    async def _default_branch(self, target: TargetRepo) -> str:
        repo = response_json(
            await self._client.rest.repos.async_get(owner=target.owner, repo=target.name)
        )
        return repo.get("default_branch") or "main"

    async def _ensure_branch(self, target: TargetRepo, base: str) -> str | None:
        """Ensure the managed PR branch exists and return its head commit OID.

        Creates the branch from ``base`` HEAD if missing. Returns ``None`` when there is
        no ``base`` commit to branch from (an empty repository), so the caller skips it
        instead of crashing — there is nothing to provision into a repo with no commits
        yet. The OID is the ``expectedHeadOid`` for the signing commit mutation.
        """
        try:
            ref = response_json(
                await self._client.rest.git.async_get_ref(
                    owner=target.owner, repo=target.name, ref=f"heads/{_BRANCH}"
                )
            )
            return ref["object"]["sha"]  # already exists — reuse it
        except RequestFailed as exc:
            if exc.response.status_code != 404:
                raise
        try:
            base_ref = response_json(
                await self._client.rest.git.async_get_ref(
                    owner=target.owner, repo=target.name, ref=f"heads/{base}"
                )
            )
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                return None  # empty repo: the default branch has no commit
            raise
        sha = base_ref["object"]["sha"]
        await self._client.rest.git.async_create_ref(
            owner=target.owner,
            repo=target.name,
            data={"ref": f"refs/heads/{_BRANCH}", "sha": sha},
        )
        return sha

    async def _ensure_pr(self, target: TargetRepo, base: str) -> str:
        """Return the URL of the managed PR, opening one if none is open."""
        existing = response_json(
            await self._client.rest.pulls.async_list(
                owner=target.owner, repo=target.name, head=f"{target.owner}:{_BRANCH}", state="open"
            )
        )
        if existing:
            return existing[0]["html_url"]
        created = response_json(
            await self._client.rest.pulls.async_create(
                owner=target.owner,
                repo=target.name,
                data={"title": _PR_TITLE, "head": _BRANCH, "base": base, "body": _PR_BODY},
            )
        )
        return created["html_url"]

    async def _close_branch(self, target: TargetRepo) -> list[str]:
        """Delete the managed branch and close its PR; return the closed PR URL(s).

        Used when pruning has left nothing to provision (the branch would equal the
        default branch): closing + deleting is cleaner than leaving an empty PR open.

        The branch is deleted *before* the PR is closed: deleting the head ref auto-closes
        any open PR, and a genuine failure (e.g. an externally deletion-protected branch)
        then surfaces — and is retried next reconcile — instead of being masked after the
        PR was already closed. Open PRs are captured first so their URLs can be reported,
        and closed explicitly afterwards in case the auto-close did not fire. Only a 404
        (branch already gone) is swallowed.
        """
        open_prs = response_json(
            await self._client.rest.pulls.async_list(
                owner=target.owner, repo=target.name, head=f"{target.owner}:{_BRANCH}", state="open"
            )
        )
        try:
            await self._client.rest.git.async_delete_ref(
                owner=target.owner, repo=target.name, ref=f"heads/{_BRANCH}"
            )
        except RequestFailed as exc:
            if exc.response.status_code != 404:
                raise  # surface a real failure (e.g. a protected branch) before closing
        closed: list[str] = []
        for pr in open_prs:
            await self._client.rest.pulls.async_update(
                owner=target.owner,
                repo=target.name,
                pull_number=pr["number"],
                data={"state": "closed"},
            )
            if pr.get("html_url"):
                closed.append(pr["html_url"])
        return closed

    async def _orphans(
        self, target: TargetRepo, base: str, desired: set[str]
    ) -> list[tuple[str, str]]:
        """Return ``(path, status)`` for files the branch carries that ``desired`` omits.

        The managed branch is caldrith's alone, so every path it changes relative to the
        default branch is caldrith's. A changed path the config no longer declares is an
        orphan to undo: an ``added`` one is a net-new file to delete; a ``modified`` one
        is a repo file to revert. Returns ``[]`` when the branch does not exist yet
        (nothing has been provisioned, so nothing to prune).
        """
        try:
            comparison = response_json(
                await self._client.rest.repos.async_compare_commits(
                    owner=target.owner, repo=target.name, basehead=f"{base}...{_BRANCH}"
                )
            )
        except RequestFailed as exc:
            if exc.response.status_code == 404:
                return []  # no managed branch yet — nothing to prune
            raise
        orphans: list[tuple[str, str]] = []
        for entry in comparison.get("files") or []:
            path, status = entry.get("filename"), entry.get("status")
            if path and path not in desired and status in ("added", "modified"):
                orphans.append((path, status))
        return orphans

    async def _build_changes(
        self,
        target: TargetRepo,
        base: str,
        needed: list[ManagedFile],
        orphans: list[tuple[str, str]],
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Compute the ``(additions, deletions)`` the managed branch needs this run.

        A declared file is added/updated only when the branch's copy differs (so a re-run
        while the PR is pending stages nothing). An orphan caldrith ``added`` is deleted;
        one it had only ``modified`` is reverted to the default branch's content, or
        deleted if the repo has since removed its own copy. Shapes match the GraphQL
        ``FileChanges`` input: additions are ``{path, contents}`` (base64), deletions
        ``{path}``.
        """
        additions: list[dict[str, str]] = []
        deletions: list[dict[str, str]] = []
        for managed in needed:
            branch_content, _ = await self._get_file(target, managed.path, _BRANCH)
            if branch_content == managed.content:
                continue  # already staged on the branch
            additions.append({"path": managed.path, "contents": _b64(managed.content)})
        for path, status in orphans:
            branch_content, blob_sha = await self._get_file(target, path, _BRANCH)
            if blob_sha is None:
                continue  # already absent on the branch
            if status == "added":
                deletions.append({"path": path})  # net-new file caldrith added
                continue
            base_content, _ = await self._get_file(target, path, base)
            if base_content is None:
                deletions.append({"path": path})  # repo dropped its own copy
            elif base_content != branch_content:
                additions.append({"path": path, "contents": _b64(base_content)})  # revert
        return additions, deletions

    async def _commit_on_branch(
        self,
        target: TargetRepo,
        head_oid: str,
        additions: list[dict[str, str]],
        deletions: list[dict[str, str]],
    ) -> None:
        """Author one signed commit on the managed branch via GraphQL.

        ``createCommitOnBranch`` attributes the commit to the App's installation identity
        and GitHub signs it, so the managed PR's commits are **Verified** (unlike the REST
        contents API, which leaves a third-party App's commits unsigned). ``head_oid`` is
        the expected current branch head — GitHub rejects the mutation if it has moved.
        """
        file_changes: dict[str, list[dict[str, str]]] = {}
        if additions:
            file_changes["additions"] = additions
        if deletions:
            file_changes["deletions"] = deletions
        await self._client.async_graphql(
            _COMMIT_MUTATION,
            {
                "input": {
                    "branch": {
                        "repositoryNameWithOwner": target.full_name,
                        "branchName": _BRANCH,
                    },
                    "message": {"headline": _COMMIT_MESSAGE},
                    "expectedHeadOid": head_oid,
                    "fileChanges": file_changes,
                }
            },
        )

    async def apply(self, target: TargetRepo, files: list[ManagedFile]) -> FilesResult:
        """Provision ``files`` into ``target`` via a PR (unless dry-run).

        Adds/updates the declared files and prunes any the branch still carries from a
        previous run that the config no longer declares, so the PR reflects exactly the
        currently-required files.
        """
        result = FilesResult(repo=target.full_name)
        default_branch = await self._default_branch(target)

        desired: set[str] = set()
        needed: list[ManagedFile] = []
        for managed in files:
            if matches_any(target.name, managed.skip_repos):
                continue  # excluded from this repo -> not provisioned (and pruned if present)
            desired.add(managed.path)
            content, _ = await self._get_file(target, managed.path, default_branch)
            if content is None:
                if managed.create_only and await self._sibling_present(
                    target, managed.path, default_branch
                ):
                    continue  # a .yml/.yaml variant already exists — don't duplicate it
                needed.append(managed)  # absent -> create
            elif content != managed.content and not managed.create_only:
                needed.append(managed)  # drifted -> update (create_only files are left alone)

        orphans = await self._orphans(target, default_branch, desired)

        if not needed and not orphans:
            return result  # repo already compliant and the branch carries nothing stale
        result.files = [m.path for m in needed]
        result.removed = [path for path, _ in orphans]
        if self._dry_run:
            return result

        if not needed:
            # Nothing left to provision (every branch change is now an orphan), so the
            # branch would equal the default branch: close the PR and drop the branch
            # rather than per-file prune only to leave an empty PR. Reached only when
            # orphans is non-empty (the all-compliant case returned above).
            closed = await self._close_branch(target)
            result.closed_pr_url = closed[0] if closed else None
            result.applied = True
            return result

        head_oid = await self._ensure_branch(target, default_branch)
        if head_oid is None:
            # Empty repository: no base commit to branch a PR from. Skip gracefully.
            result.files = []
            result.removed = []
            return result
        additions, deletions = await self._build_changes(target, default_branch, needed, orphans)
        if additions or deletions:
            await self._commit_on_branch(target, head_oid, additions, deletions)
        result.pr_url = await self._ensure_pr(target, default_branch)
        result.applied = True
        return result


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Uniform adapter: provision managed files into one repo (via PR)."""
    if not config.files:
        return []
    result = await FileProvisioner(client, dry_run=dry_run).apply(target, config.files)
    notes = list(result.files)
    notes.extend(f"-{path}" for path in result.removed)
    if result.pr_url:
        notes.append(f"PR: {result.pr_url}")
    if result.closed_pr_url:
        notes.append(f"closed PR: {result.closed_pr_url}")
    return [
        TierResult(
            tier="files",
            scope=result.repo,
            changed=result.changed,
            applied=result.applied,
            notes=notes,
        )
    ]


TIER = RepoTier(name="files", configured=lambda c: bool(c.files), reconcile=reconcile)
