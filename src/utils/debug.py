"""Development logging for xCAL.

Set the ``XCAL_DEBUG=1`` environment variable (``config.DEBUG``) to get verbose
output and an ``xcal_debug.log`` file next to the app. Off by default so shipping
builds stay quiet. Import ``log`` anywhere and call ``setup_logging()`` once at
startup.
"""
from __future__ import annotations
import logging
import sys

import config

log = logging.getLogger("xcal")
_configured = False


def setup_logging() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    level = logging.DEBUG if config.DEBUG else logging.INFO
    log.setLevel(level)
    log.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")

    # Windowed (frozen) builds have no console, so stderr can be None.
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        console.setLevel(level)
        log.addHandler(console)

    if config.DEBUG:
        try:
            path = config.APP_ROOT / "xcal_debug.log"
            file_handler = logging.FileHandler(path, encoding="utf-8")
            file_handler.setFormatter(fmt)
            file_handler.setLevel(logging.DEBUG)
            log.addHandler(file_handler)
            log.debug("Debug log -> %s", path)
        except Exception:
            pass

    log.debug("xCAL logging initialized (DEBUG=%s)", config.DEBUG)
