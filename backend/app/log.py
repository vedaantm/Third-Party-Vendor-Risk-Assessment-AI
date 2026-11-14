# backend/app/log.py

"""
Structured logging with structlog.

    from app.log import get_logger
    log = get_logger(__name__)
    log.info("ofac.screened", vendor=name, status=status)

Every line is a JSON object (LOG_FORMAT=json, the default) carrying the
event name, level, timestamp, logger name, any key-value fields, and the
context variables bound for the current request or job:

    request_id  — set per HTTP request by main_api's middleware
    job_id      — set for the duration of an evaluation pipeline run

Library loggers (uvicorn, alembic, sqlalchemy) go through the same
formatter, so all output has one shape. LOG_FORMAT=console renders the
same events readably for local development.
"""

import logging
import sys

import structlog

# Fields structlog adds to every event, before rendering
_SHARED_PROCESSORS: list = [
    structlog.contextvars.merge_contextvars,      # request_id, job_id, …
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def configure_logging(level: str = "INFO", fmt: str = "json", stream=None) -> None:
    """Configure structlog and the stdlib root logger. Safe to call again."""
    renderer = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            # Records from plain stdlib loggers get the same fields added
            foreign_pre_chain=_SHARED_PROCESSORS,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn installs its own handlers; route its loggers through ours instead
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lib = logging.getLogger(name)
        lib.handlers = []
        lib.propagate = True
    # main_api's middleware logs every request (with request_id); uvicorn's
    # access line would be a duplicate without it
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str | None = None):
    return structlog.stdlib.get_logger(name)
