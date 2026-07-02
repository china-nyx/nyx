"""Centralized logging — first get_logger() call lazily sets up handlers."""
import logging
import sys


_root = logging.getLogger("nyx")
_root.setLevel(logging.INFO)
_root.propagate = False

_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] [%(_version)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_initialized = False


def _init():
    """Lazy init — runs once on first get_logger call."""
    global _initialized
    if _initialized:
        return
    from core import config
    from sdk.fs import ensure_dir
    from logging.handlers import TimedRotatingFileHandler

    vf = logging.Filter(_version_filter)
    ensure_dir(config.LOG_DIR)
    fh = TimedRotatingFileHandler(config.LOG_FILE, when="midnight", interval=1,
                                  backupCount=config.LOG_KEEP_DAYS, encoding="utf-8")
    fh.setFormatter(_fmt)
    fh.addFilter(vf)
    _root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(_fmt)
    sh.addFilter(vf)
    _root.addHandler(sh)
    _initialized = True


def _version_filter(record):
    """Lazy version filter — defers git call until first log."""
    try:
        from core.git import Git
        from core import config
        record._version = Git(config.REPO).short()
    except Exception:
        record._version = "???"
    return True


def get_logger(name: str) -> logging.Logger:
    """Return a logger with the given module name.

    Usage in each module: ``logger = get_logger(__name__)``
    First call lazily sets up file + console handlers."""
    _init()
    child = logging.getLogger(name)
    child.setLevel(_root.level)
    for h in _root.handlers:
        if h not in child.handlers:
            child.addHandler(h)
    return child
