"""Centralized logging — handler config lives here, each module gets its own logger."""
import logging
from logging.handlers import TimedRotatingFileHandler
import sys

from core import config

_root = logging.getLogger("nyx")
_root.setLevel(logging.INFO)
_root.propagate = False


class _VersionFilter(logging.Filter):
    def filter(self, record):
        try:
            from core.git import Git
            record._version = Git().short()[:7]
        except Exception:
            record._version = "unknown"
        return True


_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] [%(_version)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_vf = _VersionFilter()

_fh = TimedRotatingFileHandler(config.LOG_FILE, when="midnight", interval=1, backupCount=config.LOG_KEEP_DAYS, encoding="utf-8")
_fh.setFormatter(_fmt)
_fh.addFilter(_vf)
_root.addHandler(_fh)

_sh = logging.StreamHandler(sys.stderr)
_sh.setFormatter(_fmt)
_sh.addFilter(_vf)
_root.addHandler(_sh)


def get_logger(name: str) -> logging.Logger:
    """Return a logger with the given module name.

    Usage in each module: ``logger = get_logger(__name__)``
    Log output will show e.g. [app.agent] [c848b09] message."""
    child = logging.getLogger(name)
    child.setLevel(_root.level)
    # Share handlers and filters from root so child loggers output the same format
    for h in _root.handlers:
        if h not in child.handlers:
            child.addHandler(h)
    return child
