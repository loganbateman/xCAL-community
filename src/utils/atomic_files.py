"""Same-directory, flushed file replacement for private application state."""
from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from threading import RLock

_settings_lock = RLock()


@contextmanager
def atomic_target(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path, text):
    with atomic_target(path) as temporary:
        temporary.write_text(text, encoding="utf-8")


def update_settings(path, values):
    """Commit related dotenv settings together; leave the old file on failure."""
    from dotenv import set_key
    path = Path(path)
    with _settings_lock, atomic_target(path) as temporary:
        if path.exists():
            temporary.write_bytes(path.read_bytes())
        for key, value in values.items():
            set_key(str(temporary), key, str(value))
