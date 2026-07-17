"""Provision required files (workflows) into a repo via a pull request.

Caldrith never pushes to a base branch directly — it opens (and reuses) a managed PR
from a stable branch (``ci/caldrith/managed-files`` for the default branch, or
``ci/caldrith/managed-files-<base>`` for another base such as ``staging``) that adds,
updates, and prunes the declared files, so a human/automation merges it. A file's
``branches`` selects the base branches it targets (default: the repo's default branch),
and each base gets its own managed PR. This is how required workflows (the Chargate
gate, a Diatreme release) get rolled out org-wide.

A run's adds, updates and prunes go into **one commit**, authored through the GraphQL
``createCommitOnBranch`` mutation so GitHub signs it on the App's behalf — the managed
PR's commits show as **Verified**. (The REST contents API does not sign a third-party
App's commits, so they would otherwise show as unverified.)

Idempotent and non-destructive:
- A file already matching ``content`` on its base branch is skipped.
- ``create_only`` files are written only when absent (never overwrite a repo's own),
  and a ``.yml``/``.yaml`` sibling counts as present — so a managed ``release.yaml``
  won't be added next to a repo's existing ``release.yml``.
- Versions are never *downgraded*: if the repo pins any reference the file declares — a
  SHA- or tag-pinned action (``uses: owner/repo@<sha> # vX.Y.Z``) or a container image tag
  (``image: registry/owner/name:vX.Y.Z``) — at a newer version than ``content`` (e.g. a
  Dependabot / Renovate / Flux bump), the file is left as-is instead of reverted. This guard
  is unconditional; set ``allow_downgrade`` on a file to permit an intentional rollback. A
  downgrade a prior run had already staged on the managed PR is pruned like any orphan, and
  the PR is **closed** if that was the only change it carried (it reopens once a genuine
  change — a new file or a real upgrade — is due).
- ``skip_repos`` globs exclude a file from specific repos (a per-file escape hatch).
- An empty repo (no commit on its default branch) is skipped gracefully — there is
  nothing to branch a PR from yet.
- The PR branch is reused; files already correct on it aren't re-committed; an open PR
  is not duplicated. So re-running while a PR is pending is a no-op.
- Files the branch still carries that the config no longer declares — a workflow
  dropped from ``settings.yml``, or a repo newly matched by ``skip_repos`` — are pruned
  so the PR reflects only the currently-required files. Caldrith touches only its own
  managed branch: a file it added net-new is deleted, and a repo file it had merely
  updated is reverted to the base branch's version — the repo's
  own files are never removed. If pruning leaves nothing to provision, the now-empty PR
  is closed and the branch deleted rather than left dangling. Boundary: removing the
  *last* managed file (an empty or absent ``files:`` block) skips the files tier
  entirely, so it neither prunes nor closes — keep at least one file declared, or close
  any leftover managed PRs by hand.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

from githubkit import GitHub
from githubkit.exception import RequestFailed

from caldrith.config.schema import ManagedFile, RepoScoped
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo
from caldrith.reconcile.selection import matches_any

_BRANCH = "ci/caldrith/managed-files"
# Sentinel in a file's ``branches`` meaning "this repo's default branch" (whatever it is
# named). Managed PRs into the default branch keep the stable ``_BRANCH`` name; any other
# base gets a ``_BRANCH-<base>`` sibling branch, so each base branch has its own managed PR.
_DEFAULT_BASE = "default"
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


# The three version-pin styles a bot (Dependabot / Renovate / Flux) bumps and Caldrith must
# never revert. A version is ``X.Y`` or ``X.Y.Z`` — a leading ``v`` is optional.
#
# SHA-pinned action, the org's convention: ``uses: owner/repo@<40-hex> # vX.Y.Z`` — the
# trailing ``# vX.Y.Z`` comment tracks the pinned SHA's tag.
_ACTION_SHA_RE = re.compile(
    r"uses:\s*([\w.-]+/[\w.-]+)@[0-9a-fA-F]{40}\s*#\s*v?(\d+(?:\.\d+){1,2})"
)
# Tag-pinned action (no SHA): ``uses: owner/repo@vX.Y.Z``. A 40-hex SHA never matches (it
# carries no dots), so this can't double-count a SHA-pinned line.
_ACTION_TAG_RE = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)@v?(\d+(?:\.\d+){1,2})(?![\w.])")
# Container image tag: ``image: registry/owner/name:vX.Y.Z`` (optionally a ``- image:`` list
# item or quoted). Anchored on the ``image:`` key at line start so it never matches a ``uses:``
# SHA or an unrelated ``key: value`` — the tag is what Flux / Renovate bump.
    r"^\s*(?:-\s*)?image:\s*[\"']?([\w./:@-]+):v?(\d+(?:\.\d+){1,2})(?![\w.])", re.MULTILINE
    r"^\s*(?:-\s*)?image:\s*[\"']?([\w./-]+):v?(\d+(?:\.\d+){1,2})(?![\w.])", re.MULTILINE
)


def _semver(version: str) -> tuple[int, int, int]:
    """``"2.1"`` / ``"2.1.0"`` -> ``(2, 1, 0)`` for ordering."""
    parts = [int(p) for p in version.split(".")]
    major, minor, patch, *_ = (*parts, 0, 0)
    return major, minor, patch


def _pinned_versions(content: str) -> dict[str, tuple[int, int, int]]:
    """Map every version-pinned reference in ``content`` -> its semver, for downgrade checks.

    Covers all three pin styles a bot bumps: SHA-pinned actions, tag-pinned actions and
    container image tags. Keys are namespaced by kind (``uses:`` / ``image:``) so an action
    and an image that happen to share a path can't collide.
    """
    versions: dict[str, tuple[int, int, int]] = {}
    for action, version in _ACTION_SHA_RE.findall(content):
        versions[f"uses:{action}"] = _semver(version)
    for action, version in _ACTION_TAG_RE.findall(content):
        versions.setdefault(f"uses:{action}", _semver(version))  # SHA pin (if any) wins
    for image, version in _IMAGE_RE.findall(content):
        versions[f"image:{image}"] = _semver(version)
    return versions


def _repo_is_ahead(repo_content: str, admin_content: str) -> bool:
    """True if the repo pins any reference the admin file declares at a *newer* version.

    Overwriting would then downgrade that pin — a version bumped backwards, exactly what a
    Dependabot / Renovate action bump or a Flux image-tag bump past the admin baseline looks
    like. Only references the admin file declares are compared, so extra pins a repo adds on
    its own are ignored.
    """
    repo_pins = _pinned_versions(repo_content)
    return any(
        key in repo_pins and repo_pins[key] > admin_version
        for key, admin_version in _pinned_versions(admin_content).items()
    )


def _managed_branch(base: str, default_branch: str) -> str:
    """The managed PR branch name for a given ``base`` branch.

    The default branch keeps the stable ``ci/caldrith/managed-files`` name (so an existing
    managed PR stays untouched); any other base gets a ``ci/caldrith/managed-files-<base>``
    *sibling* — one managed PR per base branch.

    The separator is a hyphen, NOT ``/``, on purpose. ``ci/caldrith/managed-files/<base>``
    would be a child path of the default branch, and Git cannot hold both a ref and another
    ref nested under it (a directory/file conflict): once ``ci/caldrith/managed-files``
    exists, creating ``ci/caldrith/managed-files/staging`` fails with ``cannot lock ref``
    (HTTP 422), so that base would silently never be provisioned. Any ``/`` inside ``base``
    is flattened to ``-`` for the same reason.
    """
    if base == default_branch:
        return _BRANCH
    return f"{_BRANCH}-{base.replace('/', '-')}"


def _resolve_bases(managed: ManagedFile, default_branch: str) -> list[str]:
    """The distinct base branches ``managed`` targets, resolving the ``default`` sentinel.

    An unset ``branches`` targets the repo's default branch only (backward-compatible); the
    ``"default"`` keyword resolves to it, so ``["default", "staging"]`` targets both. The
    result is order-preserving and de-duplicated (a default branch listed both as
    ``"default"`` and by name is provisioned once).
    """
    raw = managed.branches if managed.branches is not None else [_DEFAULT_BASE]
    resolved: dict[str, None] = {}
    for name in raw:
        resolved.setdefault(default_branch if name == _DEFAULT_BASE else name, None)
    return list(resolved)


@dataclass
class FilesResult:
    """Outcome of provisioning managed files into one repo, for one base branch."""

    repo: str
    base: str = ""  # the base branch this managed PR targets (the repo's default unless set)
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

    async def _ensure_branch(self, target: TargetRepo, base: str, branch: str) -> str | None:
        """Ensure the managed PR branch exists and return its head commit OID.

        Creates the branch from ``base`` HEAD if missing. Returns ``None`` when there is
        no ``base`` commit to branch from (an empty repository), so the caller skips it
        instead of crashing — there is nothing to provision into a repo with no commits
        yet. The OID is the ``expectedHeadOid`` for the signing commit mutation.
        """
        try:
            ref = response_json(
                await self._client.rest.git.async_get_ref(
                    owner=target.owner, repo=target.name, ref=f"heads/{branch}"
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
        await self._create_branch_ref(target, branch, sha)
        return sha

    async def _create_branch_ref(self, target: TargetRepo, branch: str, sha: str) -> None:
        """Create ``refs/heads/{branch}`` at ``sha``, self-healing a directory/file conflict.

        A ref left nested under ``refs/heads/{branch}/`` — from Caldrith's retired
        ``ci/caldrith/managed-files/<base>`` slash naming (see :func:`_managed_branch`) —
        makes ``{branch}`` a *directory* in the ref store, so creating the flat ref 422s
        with ``cannot lock ref``. Unhandled, that aborts the repo's whole files tier (the
        default base is provisioned first, in :meth:`apply`), so *no* managed PR opens for
        *any* base and the repo silently falls out of sync. On that 422, drop the stale
        nested refs (Caldrith's own, hence safe) and retry once; a 422 from any other cause,
        or one that leaves nothing to delete, is re-raised unchanged.
        """
        data = {"ref": f"refs/heads/{branch}", "sha": sha}
        try:
            await self._client.rest.git.async_create_ref(
                owner=target.owner, repo=target.name, data=data
            )
            return
        except RequestFailed as exc:
            if exc.response.status_code != 422:
                raise
            if not await self._delete_nested_refs(target, branch):
                raise  # not the slash-branch conflict — surface the original 422
        await self._client.rest.git.async_create_ref(
            owner=target.owner, repo=target.name, data=data
        )

    async def _delete_nested_refs(self, target: TargetRepo, branch: str) -> list[str]:
        """Delete Caldrith's own refs nested under ``refs/heads/{branch}/``; return their names.

        These are leftovers from the retired ``ci/caldrith/managed-files/<base>`` slash
        naming. They live inside Caldrith's own ``ci/caldrith/managed-files`` namespace —
        never a user branch — so dropping them is safe, and it auto-closes any superseded
        stale managed PR. The strict ``refs/heads/{branch}/`` prefix filter matches only
        true children, so a ``{branch}-<base>`` *hyphen* sibling (the current scheme) is
        never touched even though it shares the ``{branch}`` string prefix.
        """
        nested_prefix = f"refs/heads/{branch}/"
        matched = response_json(
            await self._client.rest.git.async_list_matching_refs(
                owner=target.owner, repo=target.name, ref=f"heads/{branch}"
            )
        )
        deleted: list[str] = []
        for entry in matched:
            full_ref = entry.get("ref", "")
            if not full_ref.startswith(nested_prefix):
                continue  # the flat ref itself, or a `{branch}-<base>` hyphen sibling
            name = full_ref.removeprefix("refs/")  # heads/ci/caldrith/managed-files/staging
            await self._client.rest.git.async_delete_ref(
                owner=target.owner, repo=target.name, ref=name
            )
            deleted.append(name)
        return deleted

    async def _ensure_pr(self, target: TargetRepo, base: str, branch: str) -> str:
        """Return the URL of the managed PR for ``branch``, opening one if none is open."""
        existing = response_json(
            await self._client.rest.pulls.async_list(
                owner=target.owner, repo=target.name, head=f"{target.owner}:{branch}", state="open"
            )
        )
        if existing:
            return existing[0]["html_url"]
        created = response_json(
            await self._client.rest.pulls.async_create(
                owner=target.owner,
                repo=target.name,
                data={"title": _PR_TITLE, "head": branch, "base": base, "body": _PR_BODY},
            )
        )
        return created["html_url"]

    async def _close_branch(self, target: TargetRepo, branch: str) -> list[str]:
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
                owner=target.owner, repo=target.name, head=f"{target.owner}:{branch}", state="open"
            )
        )
        try:
            await self._client.rest.git.async_delete_ref(
                owner=target.owner, repo=target.name, ref=f"heads/{branch}"
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
        self, target: TargetRepo, base: str, branch: str, desired: set[str]
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
                    owner=target.owner, repo=target.name, basehead=f"{base}...{branch}"
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
        branch: str,
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
            branch_content, _ = await self._get_file(target, managed.path, branch)
            if branch_content == managed.content:
                continue  # already staged on the branch
            additions.append({"path": managed.path, "contents": _b64(managed.content)})
        for path, status in orphans:
            branch_content, blob_sha = await self._get_file(target, path, branch)
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
        branch: str,
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
                        "branchName": branch,
                    },
                    "message": {"headline": _COMMIT_MESSAGE},
                    "expectedHeadOid": head_oid,
                    "fileChanges": file_changes,
                }
            },
        )

    async def apply(self, target: TargetRepo, files: list[ManagedFile]) -> list[FilesResult]:
        """Provision ``files`` into ``target`` via one managed PR per target base branch.

        Each file targets one or more base branches (its ``branches``; the ``"default"``
        sentinel is the repo's default branch, and an unset ``branches`` means the default
        branch only). Files are grouped by resolved base branch and each base is
        provisioned independently — into its own ``ci/caldrith/managed-files`` (default
        branch) or ``ci/caldrith/managed-files-<base>`` PR — so a repo missing a listed
        base branch is skipped for that base without affecting the others. Returns one
        :class:`FilesResult` per base processed.
        """
        default_branch = await self._default_branch(target)
        by_base: dict[str, list[ManagedFile]] = {}
        for managed in files:
            for base in _resolve_bases(managed, default_branch):
                by_base.setdefault(base, []).append(managed)
        return [
            await self._apply_for_base(target, base, default_branch, base_files)
            for base, base_files in by_base.items()
        ]

    async def _apply_for_base(
        self, target: TargetRepo, base: str, default_branch: str, files: list[ManagedFile]
    ) -> FilesResult:
        """Provision ``files`` into ``target`` against one ``base`` branch (unless dry-run).

        Adds/updates the declared files and prunes any the managed branch still carries from
        a previous run that the config no longer declares, so the PR reflects exactly the
        currently-required files for this base.
        """
        branch = _managed_branch(base, default_branch)
        result = FilesResult(repo=target.full_name, base=base)

        desired: set[str] = set()
        needed: list[ManagedFile] = []
        for managed in files:
            if matches_any(target.name, managed.skip_repos):
                continue  # excluded from this repo -> not provisioned (and pruned if present)
            desired.add(managed.path)
            content, _ = await self._get_file(target, managed.path, base)
            if content is None:
                if managed.create_only and await self._sibling_present(target, managed.path, base):
                    continue  # a .yml/.yaml variant already exists — don't duplicate it
                needed.append(managed)  # absent -> create
            elif content != managed.content and not managed.create_only:
                if not managed.allow_downgrade and _repo_is_ahead(content, managed.content):
                    # Repo pins a newer version — never bump it backwards. Drop it from the
                    # desired set so a stale copy a prior run staged on the managed branch is
                    # pruned like an excluded file (the downgrade is reverted), and the PR is
                    # closed if that leaves nothing — rather than left open as a downgrade PR.
                    desired.discard(managed.path)
                    continue
                needed.append(managed)  # drifted -> update (create_only files are left alone)

        orphans = await self._orphans(target, base, branch, desired)

        if not needed and not orphans:
            return result  # repo already compliant and the branch carries nothing stale
        result.files = [m.path for m in needed]
        result.removed = [path for path, _ in orphans]
        if self._dry_run:
            return result

        if not needed:
            # Nothing left to provision (every branch change is now an orphan), so the
            # branch would equal its base: close the PR and drop the branch rather than
            # per-file prune only to leave an empty PR. Reached only when orphans is
            # non-empty (the all-compliant case returned above).
            closed = await self._close_branch(target, branch)
            result.closed_pr_url = closed[0] if closed else None
            result.applied = True
            return result

        head_oid = await self._ensure_branch(target, base, branch)
        if head_oid is None:
            # No base commit to branch a PR from — an empty repository, or a repo that
            # lacks this base branch (e.g. no ``staging``). Skip this base gracefully.
            result.files = []
            result.removed = []
            return result
        additions, deletions = await self._build_changes(target, base, branch, needed, orphans)
        if additions or deletions:
            await self._commit_on_branch(target, branch, head_oid, additions, deletions)
        result.pr_url = await self._ensure_pr(target, base, branch)
        result.applied = True
        return result


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Uniform adapter: provision managed files into one repo — one row per base branch."""
    if not config.files:
        return []
    results = await FileProvisioner(client, dry_run=dry_run).apply(target, config.files)
    # Only tag rows with their base branch when more than one base is in play, so the
    # common single-base Check Run summary is unchanged.
    multi_base = len(results) > 1
    rows: list[TierResult] = []
    for result in results:
        notes = [f"base: {result.base}"] if multi_base else []
        notes.extend(result.files)
        notes.extend(f"-{path}" for path in result.removed)
        if result.pr_url:
            notes.append(f"PR: {result.pr_url}")
        if result.closed_pr_url:
            notes.append(f"closed PR: {result.closed_pr_url}")
        rows.append(
            TierResult(
                tier="files",
                scope=result.repo,
                changed=result.changed,
                applied=result.applied,
                notes=notes,
            )
        )
    return rows


TIER = RepoTier(name="files", configured=lambda c: bool(c.files), reconcile=reconcile)
