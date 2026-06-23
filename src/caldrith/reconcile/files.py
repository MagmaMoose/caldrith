"""Provision required files (workflows) into a repo via a pull request.

Caldrith never pushes to the default branch directly — it opens (and reuses) a single
PR from a stable branch (``caldrith/managed-files``) that adds/updates the declared
files, so a human/automation merges it. This is how required workflows (the Chargate
gate, a Diatreme release) get rolled out org-wide.

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
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

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


@dataclass
class FilesResult:
    """Outcome of provisioning managed files into a repo."""

    repo: str
    files: list[str] = field(default_factory=list)  # paths created/updated this run
    pr_url: str | None = None
    applied: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.files)


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

    async def _ensure_branch(self, target: TargetRepo, base: str) -> bool:
        """Ensure the managed PR branch exists (create from ``base`` HEAD if missing).

        Returns ``False`` when there is no ``base`` commit to branch from (an empty
        repository), so the caller skips it instead of crashing — there is nothing to
        provision into a repo with no commits yet.
        """
        try:
            await self._client.rest.git.async_get_ref(
                owner=target.owner, repo=target.name, ref=f"heads/{_BRANCH}"
            )
            return True  # already exists — reuse it
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
                return False  # empty repo: the default branch has no commit
            raise
        await self._client.rest.git.async_create_ref(
            owner=target.owner,
            repo=target.name,
            data={"ref": f"refs/heads/{_BRANCH}", "sha": base_ref["object"]["sha"]},
        )
        return True

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

    async def apply(self, target: TargetRepo, files: list[ManagedFile]) -> FilesResult:
        """Provision ``files`` into ``target`` via a PR (unless dry-run)."""
        result = FilesResult(repo=target.full_name)
        default_branch = await self._default_branch(target)

        needed: list[ManagedFile] = []
        for managed in files:
            if matches_any(target.name, managed.skip_repos):
                continue  # this file is excluded from this repo (per-file escape hatch)
            content, _ = await self._get_file(target, managed.path, default_branch)
            if content is None:
                if managed.create_only and await self._sibling_present(
                    target, managed.path, default_branch
                ):
                    continue  # a .yml/.yaml variant already exists — don't duplicate it
                needed.append(managed)  # absent -> create
            elif content != managed.content and not managed.create_only:
                needed.append(managed)  # drifted -> update (create_only files are left alone)

        if not needed:
            return result  # repo already compliant
        result.files = [m.path for m in needed]
        if self._dry_run:
            return result

        if not await self._ensure_branch(target, default_branch):
            # Empty repository: no base commit to branch a PR from. Skip gracefully.
            result.files = []
            return result
        for managed in needed:
            branch_content, blob_sha = await self._get_file(target, managed.path, _BRANCH)
            if branch_content == managed.content:
                continue  # already staged on the PR branch
            data: dict[str, Any] = {
                "message": _COMMIT_MESSAGE,
                "content": base64.b64encode(managed.content.encode()).decode(),
                "branch": _BRANCH,
            }
            if blob_sha is not None:
                data["sha"] = blob_sha
            await self._client.rest.repos.async_create_or_update_file_contents(
                owner=target.owner, repo=target.name, path=managed.path, data=data
            )
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
    if result.pr_url:
        notes.append(f"PR: {result.pr_url}")
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
