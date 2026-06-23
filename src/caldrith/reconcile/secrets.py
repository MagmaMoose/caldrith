"""Reconcile Actions / Dependabot secret *presence* (never values).

Secret VALUES cannot be read back from the GitHub API, so Caldrith never diffs or
rotates a value — doing so idempotently is impossible. Instead it manages *presence*:

- A declared secret that already exists is left untouched (no rotation).
- A declared secret that is missing is created from an environment-supplied value
  (``CALDRITH_SECRET_<NAME>``, name upper-cased), sealed-box encrypted with the repo's
  public key. If no value is available, the gap is reported but cannot be filled.
- When ``prune`` is set, secrets that are not declared are deleted.

Both the Actions and Dependabot secret stores are supported (parallel endpoints).
"""

from __future__ import annotations

import os
from base64 import b64encode
from typing import Any

from githubkit import GitHub
from nacl import encoding, public

from caldrith.config.schema import RepoScoped, SecretsConfig
from caldrith.github_json import response_json
from caldrith.reconcile.base import RepoTier, TierResult
from caldrith.reconcile.planner import TargetRepo


def _encrypt(public_key_b64: str, value: str) -> str:
    """Sealed-box encrypt ``value`` with a base64 public key; return base64 ciphertext."""
    key = public.PublicKey(public_key_b64.encode(), encoder=encoding.Base64Encoder)
    sealed = public.SealedBox(key)
    return b64encode(sealed.encrypt(value.encode())).decode()


def _env_value(name: str) -> str | None:
    """The supplied value for secret ``name`` (``CALDRITH_SECRET_<NAME>``), if any."""
    return os.environ.get(f"CALDRITH_SECRET_{name.upper()}")


def _configured(config: RepoScoped) -> bool:
    secrets = config.secrets
    return secrets is not None and bool(secrets.actions or secrets.dependabot or secrets.prune)


async def _reconcile_store(
    api: Any,
    target: TargetRepo,
    declared: list[str],
    *,
    prune: bool,
    kind: str,
    dry_run: bool,
    result: TierResult,
) -> bool:
    """Reconcile one secret store (Actions or Dependabot). Returns whether a write ran."""
    listed = response_json(await api.async_list_repo_secrets(owner=target.owner, repo=target.name))
    live = {s["name"] for s in (listed.get("secrets") or [])}
    declared_set = set(declared)
    did_write = False

    public_key: dict[str, Any] | None = None
    for name in declared:
        if name in live:
            continue  # present — never rotate (value is unreadable)
        value = _env_value(name)
        if value is None:
            result.changed = True
            result.notes.append(f"{kind} secret missing (no value supplied): {name}")
            continue
        result.changed = True
        result.notes.append(f"create {kind} secret: {name}")
        if not dry_run:
            if public_key is None:
                public_key = response_json(
                    await api.async_get_repo_public_key(owner=target.owner, repo=target.name)
                )
            await api.async_create_or_update_repo_secret(
                owner=target.owner,
                repo=target.name,
                secret_name=name,
                data={
                    "encrypted_value": _encrypt(public_key["key"], value),
                    "key_id": public_key["key_id"],
                },
            )
            did_write = True

    if prune:
        for name in live - declared_set:
            result.changed = True
            result.notes.append(f"delete {kind} secret: {name}")
            if not dry_run:
                await api.async_delete_repo_secret(
                    owner=target.owner, repo=target.name, secret_name=name
                )
                did_write = True

    return did_write


async def reconcile(
    client: GitHub, target: TargetRepo, config: RepoScoped, *, dry_run: bool = False
) -> list[TierResult]:
    """Reconcile declared secret presence on ``target`` (create/prune unless dry-run)."""
    secrets: SecretsConfig | None = config.secrets
    if secrets is None:
        return []
    result = TierResult(tier="secrets", scope=target.full_name)
    did_write = False
    # `prune` must only affect a store you actually declare secrets in — otherwise a
    # config with only `actions:` + `prune: true` would wipe ALL Dependabot secrets
    # (and vice-versa). A store is "managed" iff it lists at least one secret.
    if secrets.actions:
        did_write |= await _reconcile_store(
            client.rest.actions,
            target,
            secrets.actions,
            prune=secrets.prune,
            kind="actions",
            dry_run=dry_run,
            result=result,
        )
    if secrets.dependabot:
        did_write |= await _reconcile_store(
            client.rest.dependabot,
            target,
            secrets.dependabot,
            prune=secrets.prune,
            kind="dependabot",
            dry_run=dry_run,
            result=result,
        )
    result.applied = did_write and not dry_run
    return [result]


TIER = RepoTier(name="secrets", configured=_configured, reconcile=reconcile)
