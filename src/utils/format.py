"""Small formatting helpers shared across views."""
from __future__ import annotations


def format_size(size, empty: str = "—") -> str:
    """Human-readable byte size (MB/KB), or `empty` when unknown."""
    if not size:
        return empty
    mb = size / (1024 * 1024)
    return f"{mb:.1f} MB" if mb >= 1 else f"{size / 1024:.0f} KB"
