"""GitHub webhook ingest endpoint.

Flow (kept well under GitHub's 10s timeout):
  1. Read the RAW body bytes (before any JSON parse).
  2. Verify the ``X-Hub-Signature-256`` HMAC over those raw bytes.
  3. Dedup on ``X-GitHub-Delivery`` via Redis ``SET NX``.
  4. Parse the (now trusted) JSON and enqueue the appropriate ARQ job.
  5. Return ``202 Accepted``.

Events handled in P1:
  - ``push``: a push to the admin repo's default branch -> full-account reconcile.
  - ``repository`` (created/edited): reconcile just that repo.
  - ``pull_request`` (opened/reopened/synchronize) touching the admin repo's settings
    on a *non-default* branch -> DRY-RUN: post a Check Run with the diff, mutate
    nothing.

Anything else is acknowledged (202) and ignored. Deferred event types slot in here.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, Request, Response
from starlette.status import HTTP_202_ACCEPTED, HTTP_401_UNAUTHORIZED

from caldrith.api.security import verify_signature
from caldrith.audit.logging import bind_context, get_logger
from caldrith.settings import get_config
from caldrith.worker.queue import (
    dedup_delivery,
    enqueue_reconcile_installation,
    enqueue_reconcile_repo,
)

router = APIRouter(tags=["webhooks"])
_log = get_logger(__name__)


def _ref_branch(ref: str | None) -> str | None:
    """Extract a branch name from a ``refs/heads/<branch>`` ref."""
    if ref and ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    return None


def _admin_settings_paths(config: Any) -> set[str]:
    """The file paths that, when touched, indicate a settings change."""
    path = f"{config.config_path.strip('/')}/{config.settings_file_path}".lstrip("/")
    return {path}


async def _handle_push(arq_redis: Any, payload: dict, installation_id: int) -> None:
    """A push to the admin repo's default branch triggers a full-account reconcile."""
    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login")
    repo_name = repo.get("name")
    default_branch = repo.get("default_branch")
    pushed_branch = _ref_branch(payload.get("ref"))
    config = get_config()

    if (
        owner
        and repo_name == config.admin_repo
        and pushed_branch is not None
        and pushed_branch == default_branch
    ):
        await enqueue_reconcile_installation(
            arq_redis, installation_id=installation_id, owner=owner
        )


async def _handle_repository(arq_redis: Any, payload: dict, installation_id: int) -> None:
    """A repository created/edited event reconciles just that repository."""
    action = payload.get("action")
    if action not in {"created", "edited"}:
        return
    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login")
    repo_name = repo.get("name")
    if owner and repo_name:
        await enqueue_reconcile_repo(
            arq_redis,
            installation_id=installation_id,
            owner=owner,
            repo=repo_name,
            dry_run=False,
        )


async def _handle_pull_request(arq_redis: Any, payload: dict, installation_id: int) -> None:
    """A PR touching the admin settings file on a non-default branch -> dry-run."""
    action = payload.get("action")
    if action not in {"opened", "reopened", "synchronize"}:
        return

    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login")
    repo_name = repo.get("name")
    default_branch = repo.get("default_branch")
    config = get_config()

    pr = payload.get("pull_request") or {}
    head = pr.get("head") or {}
    head_ref = head.get("ref")
    head_sha = head.get("sha")

    # Only the admin repo, only a non-default-branch head (the PR's proposed change).
    if not (owner and repo_name == config.admin_repo and head_sha):
        return
    if head_ref is not None and head_ref == default_branch:
        return

    # Dry-run: a single reconcile_repo against the admin repo itself, which loads the
    # proposed config from the PR head and posts a Check Run. (P1 previews the config
    # parse/diff; broader per-repo previews are a deferred enhancement.)
    await enqueue_reconcile_repo(
        arq_redis,
        installation_id=installation_id,
        owner=owner,
        repo=repo_name,
        dry_run=True,
        head_sha=head_sha,
    )


@router.post("/", status_code=HTTP_202_ACCEPTED)
async def receive_webhook(
    request: Request,
    response: Response,
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    """Verify, dedup, and enqueue a GitHub webhook delivery."""
    config = get_config()

    # 1. RAW body — must be read before any JSON parsing for a valid HMAC.
    body = await request.body()

    # 2. Verify signature over the raw bytes.
    if not verify_signature(config.webhook_secret, body, x_hub_signature_256):
        response.status_code = HTTP_401_UNAUTHORIZED
        return {"status": "invalid signature"}

    log = bind_context(_log, delivery_id=x_github_delivery)

    # 3. Dedup on the delivery id.
    redis = request.app.state.redis
    if x_github_delivery is not None:
        is_new = await dedup_delivery(redis, x_github_delivery)
        if not is_new:
            log.info("webhook.duplicate", gh_event=x_github_event)
            return {"status": "duplicate"}

    # 4. Parse the now-trusted payload and enqueue.
    payload: dict = json.loads(body) if body else {}
    installation_id = (payload.get("installation") or {}).get("id")
    if installation_id is not None:
        request.state.installation_id = installation_id

    log = bind_context(log, installation_id=installation_id)

    if installation_id is None:
        # Lifecycle pings (e.g. ``installation`` bootstrap) without a target — ack.
        log.info("webhook.no_installation", gh_event=x_github_event)
        return {"status": "ignored"}

    arq_redis = request.app.state.arq_redis

    if x_github_event == "push":
        await _handle_push(arq_redis, payload, installation_id)
    elif x_github_event == "repository":
        await _handle_repository(arq_redis, payload, installation_id)
    elif x_github_event == "pull_request":
        await _handle_pull_request(arq_redis, payload, installation_id)
    else:
        log.info("webhook.unhandled_event", gh_event=x_github_event)

    return {"status": "accepted"}
