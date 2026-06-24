"""Manual reconcile trigger — break-glass when webhooks are silent.

``POST /reconcile`` enqueues a full reconcile for one or all installations the App can
see. Authorised by a long ``Bearer`` token from the ``MANUAL_TRIGGER_TOKEN`` env var; if
the var is unset the endpoint returns ``404`` (treat it as not enabled). The endpoint is
*tiny*: it resolves installation ids via the App-JWT-only ``apps`` endpoints and enqueues
the same ``reconcile_installation`` job the webhook flow uses — no reconcile work runs in
the request handler, so the response is immediate and idempotent.

Resolving installations:
  - body ``{"owner": "MagmaMoose"}`` → ``apps.get_org_installation`` (one installation).
  - body ``{}`` or omitted → ``apps.list_installations`` (every installation the App is on).
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from githubkit.exception import RequestFailed
from starlette.status import HTTP_202_ACCEPTED, HTTP_401_UNAUTHORIZED, HTTP_404_NOT_FOUND

from caldrith.audit.logging import get_logger
from caldrith.auth.client import GitHubClientFactory
from caldrith.github_json import response_json
from caldrith.settings import get_config
from caldrith.worker.queue import enqueue_reconcile_installation

router = APIRouter(tags=["reconcile"])
_log = get_logger(__name__)


def _check_token(authorization: str | None, expected: str) -> bool:
    """Constant-time check of ``Authorization: Bearer <token>``."""
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[len("Bearer ") :], expected)


@router.post("/reconcile", status_code=HTTP_202_ACCEPTED)
async def trigger_reconcile(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Enqueue ``reconcile_installation`` for one or every installation."""
    config = get_config()
    if not config.manual_trigger_token:
        # Endpoint disabled — same response as a missing route, so its existence isn't
        # advertised when the token is unset.
        raise HTTPException(status_code=HTTP_404_NOT_FOUND)
    if not _check_token(authorization, config.manual_trigger_token):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="invalid bearer token")

    body: dict[str, Any] = {}
    if request.headers.get("content-length", "0") != "0":
        try:
            body = await request.json()
        except ValueError:
            body = {}
    owner = body.get("owner") if isinstance(body, dict) else None

    factory = GitHubClientFactory(config)
    arq_redis = request.app.state.arq_redis
    installations = await _installations_for(factory, owner)
    for installation_id, login in installations:
        await enqueue_reconcile_installation(
            arq_redis, installation_id=installation_id, owner=login
        )
    _log.info("manual.reconcile.enqueued", owner=owner, count=len(installations))
    return {"enqueued": [{"installation_id": i, "owner": o} for i, o in installations]}


async def _installations_for(
    factory: GitHubClientFactory, owner: str | None
) -> list[tuple[int, str]]:
    """Resolve ``(installation_id, owner_login)`` for ``owner`` or every installation.

    Uses an App-JWT-authenticated client (no installation token). For a specific
    ``owner``, an ``apps.get_org_installation`` 404 is reported empty so callers see
    "App not installed on that org" rather than a 500.
    """
    async with factory.for_app() as client:
        if owner:
            try:
                installation = response_json(
                    await client.rest.apps.async_get_org_installation(org=owner)
                )
            except RequestFailed as exc:
                if exc.response.status_code == 404:
                    return []
                raise
            return [(installation["id"], installation["account"]["login"])]
        installations = response_json(await client.rest.apps.async_list_installations())
        return [(i["id"], i["account"]["login"]) for i in installations]
