"""structlog JSON logging configuration and context-binding helpers."""

from __future__ import annotations

import logging

import structlog
from structlog.stdlib import BoundLogger

_configured = False


def configure_logging(*, level: int = logging.INFO) -> None:
    """Configure structlog for JSON output. Idempotent.

    Safe to call multiple times (e.g. from both the API app factory and the ARQ
    worker startup); only the first call takes effect.
    """
    global _configured
    if _configured:
        return

    logging.basicConfig(format="%(message)s", level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> BoundLogger:
    """Return a structlog logger, configuring logging on first use."""
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)


def bind_context(
    logger: BoundLogger,
    *,
    installation_id: int | None = None,
    delivery_id: str | None = None,
    repo: str | None = None,
) -> BoundLogger:
    """Bind the standard audit fields onto ``logger``, omitting any that are ``None``."""
    fields: dict[str, object] = {}
    if installation_id is not None:
        fields["installation_id"] = installation_id
    if delivery_id is not None:
        fields["delivery_id"] = delivery_id
    if repo is not None:
        fields["repo"] = repo
    return logger.bind(**fields)
