"""Provision required files (workflows) into a repo via a pull request.

Caldrith never pushes to the default branch directly — it opens (and reuses) a single
PR from a stable branch (``caldrith/managed-files``) that adds/updates the declared
files, so a human/automation merges it. This is how required workflows (the Chargate
gate, a Diatreme release) get rolled out org-wide.

Idempotent and non-destructive:
- A file already matching ``content`` on the default branch is skipped.
- ``create_only`` files are written only when absent (never overwrite a repo's own).
- The PR branch is reused; files already correct on it aren't re-committed; an open PR
  is not duplicated. So re-running while a PR is pending is a no-op.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import ManagedFile
from caldrith.github_json import response_json
from caldrith.reconcile.planner import TargetRepo

_BRANCH = "caldrith/managed-files"
_COMMIT_MESSAGE = "chore: provision required workflows (caldrith)"
_PR_TITLE = "Caldrith: provision required workflows"
_PR_BODY = (
    "Caldrith manages these files org-wide. Merging brings this repo in line with the "
    "organisation's required workflows; Caldrith keeps them in sync thereafter."
)


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

    async def _default_branch(self, target: TargetRepo) -> str:
        repo = response_json(
            await self._client.rest.repos.async_get(owner=target.owner, repo=target.name)
        )
        return repo.get("default_branch") or "main"

    async def _ensure_branch(self, target: TargetRepo, base: str) -> None:
        """Ensure the managed PR branch exists (create from ``base`` HEAD if missing)."""
        try:
            await self._client.rest.git.async_get_ref(
                owner=target.owner, repo=target.name, ref=f"heads/{_BRANCH}"
            )
            return  # already exists — reuse it
        except RequestFailed as exc:
            if exc.response.status_code != 404:
                raise
        base_ref = response_json(
            await self._client.rest.git.async_get_ref(
                owner=target.owner, repo=target.name, ref=f"heads/{base}"
            )
        )
        await self._client.rest.git.async_create_ref(
            owner=target.owner,
            repo=target.name,
            data={"ref": f"refs/heads/{_BRANCH}", "sha": base_ref["object"]["sha"]},
        )

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
            content, _ = await self._get_file(target, managed.path, default_branch)
            if content is None:
                needed.append(managed)  # absent -> create
            elif content != managed.content and not managed.create_only:
                needed.append(managed)  # drifted -> update (create_only files are left alone)

        if not needed:
            return result  # repo already compliant
        result.files = [m.path for m in needed]
        if self._dry_run:
            return result

        await self._ensure_branch(target, default_branch)
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
