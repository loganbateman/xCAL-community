"""XRK helpers for downloading/uploading files directly to Google Drive."""
from __future__ import annotations
import os
import sys
import threading
import webbrowser
import tempfile
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from utils import google_drive_client


def download_and_open(
    file_id: str,
    filename: str,
    on_done: Optional[Callable[[Path], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
) -> None:
    """Download a Drive file into a temp folder and reveal it in Explorer."""
    def _worker():
        try:
            dest = Path(tempfile.gettempdir()) / "xcal_xrk" / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            google_drive_client.download_file(file_id, dest)
            webbrowser.open(str(dest.parent))
            if on_done:
                on_done(dest)
        except Exception as exc:
            if on_error:
                on_error(str(exc))

    threading.Thread(target=_worker, daemon=True).start()


def upload_to_folder(
    local_path: str,
    parent_id: str,
    on_done: Optional[Callable[[str], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
) -> None:
    """Upload a local file into a specific Drive folder in a background thread."""
    def _worker():
        try:
            file_id = google_drive_client.upload_file(local_path, parent_id)
            if on_done:
                on_done(file_id)
        except Exception as exc:
            if on_error:
                on_error(str(exc))

    threading.Thread(target=_worker, daemon=True).start()
