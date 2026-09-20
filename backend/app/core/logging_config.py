"""Logging setup shared by the web app and the MCP server.

Both processes call setup_logging() once at startup, each with its own file name so two running
processes never write to the same file. Records go to stderr and to a rotating file in
config.LOG_DIR. Modules log through loggers named "marathon.<area>" and never configure anything.
"""
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

from backend.app.core import config

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# uvicorn gives "uvicorn" and "uvicorn.access" their own handlers and stops them reaching the root
# logger, so the file handler has to be attached to those directly to catch its startup lines, access
# log and tracebacks ("uvicorn.error" passes its records up to "uvicorn"). Attaching only where a
# logger doesn't already reach the root keeps each line from being written twice.
_UVICORN = ("uvicorn", "uvicorn.error", "uvicorn.access")

log = logging.getLogger("marathon.logging")


def setup_logging(filename: str) -> Optional[logging.Handler]:
    """Send log records to stderr and to config.LOG_DIR/<filename>, at config.LOG_LEVEL.

    Returns the file handler (pass it to detach() to undo this), or None if the file can't be
    opened: an unwritable log folder must not stop the app, so that only warns."""
    root = logging.getLogger()
    level = logging.getLevelNamesMapping().get(config.LOG_LEVEL)
    root.setLevel(level if level is not None else logging.INFO)
    if not root.handlers:  # the MCP library has already added one; don't print everything twice
        stderr = logging.StreamHandler()
        stderr.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(stderr)

    path = config.LOG_DIR / filename
    handler: Optional[logging.Handler] = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    except OSError as e:
        log.warning("Not writing a log file (%s): %s", path, e)
    else:
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
        for name in _UVICORN:
            if not logging.getLogger(name).propagate:
                logging.getLogger(name).addHandler(handler)

    if level is None:  # after the handlers exist, so it reaches the file too
        log.warning("Unknown MARATHON_LOG_LEVEL %r; using INFO", config.LOG_LEVEL)
    return handler


def detach(handler: logging.Handler) -> None:
    """Undo setup_logging's file handler and close it."""
    for name in ("", *_UVICORN):  # "" is the root logger; removing where it was never added does nothing
        logging.getLogger(name).removeHandler(handler)
    handler.close()
