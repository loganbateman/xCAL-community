"""Versioned backups and isolated offline recovery; never overwrite a workspace."""
from __future__ import annotations
from contextlib import closing

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
from uuid import uuid4
import zipfile

import config
from utils.atomic_files import atomic_target, atomic_write_text

MANIFEST = "xcal-backup.json"
MAX_FILES = 100_000
MAX_BYTES = 50 * 1024 ** 3
_PRIVATE = {"settings.env", ".env", "credentials.json", "oauth-client.json", "google_token.json", "google_identity.json"}


def _digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _safe_name(name):
    if not name or "\\" in name or ":" in name:
        raise ValueError("The backup contains an unsafe filename.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in (".", "..", "") for part in name.split("/")):
        raise ValueError("The backup contains an unsafe path.")
    for part in path.parts:
        if part.rstrip(" .") != part or any(c in part for c in '<>"|?*') or any(ord(c) < 32 for c in part):
            raise ValueError("The backup contains an unsupported filename.")
        if part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
            raise ValueError("The backup contains a reserved Windows filename.")
        if part.casefold() in _PRIVATE or "token" in part.casefold() or "identity" in part.casefold() or part.endswith(".env"):
            raise ValueError("Credentials and configuration are not workspace backup data.")
    return path


def export_workspace(workspace, destination, check_cancel=lambda: None):
    workspace, destination = Path(workspace).resolve(), Path(destination).resolve()
    if not workspace.is_dir():
        raise ValueError("The workspace does not exist yet.")
    if destination.is_relative_to(workspace):
        raise ValueError("Save the backup outside the active workspace.")
    with tempfile.TemporaryDirectory(prefix="xcal-backup-") as temporary:
        staging = Path(temporary)
        entries, total = {}, 0
        for source in workspace.rglob("*"):
            check_cancel()
            if source.is_symlink() or source.is_junction():
                raise ValueError("Workspace links are not supported in backups.")
            if not source.is_file() or source.name.endswith(("-wal", "-shm", "-journal", ".tmp")):
                continue
            relative = source.relative_to(workspace).as_posix()
            _safe_name(relative)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix.lower() in (".db", ".sqlite3"):
                # SQLite backup captures committed WAL contents without copying a live DB.
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as origin, closing(sqlite3.connect(target)) as saved:
                    origin.backup(saved, pages=256, progress=lambda *args: check_cancel())
            else:
                shutil.copyfile(source, target)
            total += target.stat().st_size
            if len(entries) >= MAX_FILES or total > MAX_BYTES:
                raise ValueError("The workspace exceeds backup size limits.")
            entries[relative] = {"sha256": _digest(target), "size": target.stat().st_size}
        manifest = {"format": "xcal-workspace", "version": 1,
                    "source_root": str(workspace), "files": entries}
        with atomic_target(destination) as archive_path:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                archive.writestr(MANIFEST, json.dumps(manifest))
                for name in entries:
                    check_cancel()
                    archive.write(staging / name, name)
            check_cancel()
    return destination


def _relocate_files(workspace, original):
    database = workspace / "xcal_offline.db"
    if not database.exists():
        return
    original_path = PureWindowsPath(original) if "\\" in original or ":" in original else PurePosixPath(original)
    with closing(sqlite3.connect(database)) as connection, connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "offline_files" not in tables:
            return
        for local_id, filename in connection.execute("SELECT local_id,local_path FROM offline_files").fetchall():
            old = type(original_path)(filename)
            try:
                relative = old.relative_to(original_path)
            except ValueError as exc:
                raise ValueError("A backed-up media path points outside the original workspace.") from exc
            _safe_name(relative.as_posix())
            connection.execute("UPDATE offline_files SET local_path=? WHERE local_id=?",
                               (str(workspace.joinpath(*relative.parts)), local_id))


def restore_workspace(archive_path, parent, check_cancel=lambda: None):
    """Return a new recovery user-data root. Current config/data are untouched."""
    parent = Path(parent).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    final = parent / f"xCAL-recovery-{uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix=".xcal-restore-", dir=parent) as temporary:
        staging = Path(temporary)
        workspace = staging / "teams" / "local"
        workspace.mkdir(parents=True)
        with zipfile.ZipFile(archive_path) as archive:
            listing = archive.infolist()
            if len(listing) > MAX_FILES + 1 or sum(item.file_size for item in listing) > MAX_BYTES:
                raise ValueError("The backup exceeds recovery size limits.")
            names = [item.filename for item in listing]
            if len({name.casefold() for name in names}) != len(names):
                raise ValueError("The backup contains duplicate filenames.")
            for item in listing:
                if item.is_dir() or stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK or item.flag_bits & 1:
                    raise ValueError("The backup contains unsupported links, directories or encryption.")
                if item.filename != MANIFEST:
                    _safe_name(item.filename)
            if MANIFEST not in names or archive.getinfo(MANIFEST).file_size > 16 * 1024 * 1024:
                raise ValueError("This backup has no supported recovery manifest. Re-export it with the current app.")
            manifest = json.loads(archive.read(MANIFEST))
            if (not isinstance(manifest, dict) or manifest.get("format") != "xcal-workspace"
                    or manifest.get("version") != 1 or not isinstance(manifest.get("files"), dict)
                    or not isinstance(manifest.get("source_root"), str)):
                raise ValueError("The backup manifest is invalid or unsupported.")
            if set(names) != {MANIFEST, *manifest["files"]}:
                raise ValueError("Backup contents do not match the manifest.")
            for name, metadata in manifest["files"].items():
                check_cancel()
                if not isinstance(metadata, dict):
                    raise ValueError("Invalid backup file metadata.")
                target = workspace / name
                target.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with archive.open(name) as source, target.open("xb") as saved:
                    while chunk := source.read(1024 * 1024):
                        check_cancel()
                        digest.update(chunk)
                        size += len(chunk)
                        saved.write(chunk)
                    saved.flush()
                    os.fsync(saved.fileno())
                if size != metadata.get("size") or digest.hexdigest() != metadata.get("sha256"):
                    raise ValueError("A backup file failed its integrity check.")
                if target.suffix.lower() in (".db", ".sqlite3"):
                    with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as connection:
                        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                            raise ValueError("A backed-up database failed its integrity check.")
        # Resolve media references to their FINAL location before publishing.
        _relocate_files(workspace, manifest["source_root"])
        database = workspace / "xcal_offline.db"
        if database.exists():
            with closing(sqlite3.connect(database)) as connection, connection:
                exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='offline_files'").fetchone()
                if exists:
                    for key, filename in connection.execute("SELECT local_id,local_path FROM offline_files").fetchall():
                        relative = Path(filename).relative_to(staging)
                        connection.execute("UPDATE offline_files SET local_path=? WHERE local_id=?", (str(final / relative), key))
        atomic_write_text(staging / "settings.env", "GOOGLE_DRIVE_FOLDER_ID=''\nXCAL_RECOVERY_MODE='1'\n")
        check_cancel()
        os.replace(staging, final)
    return final


def open_recovery(root):
    environment = dict(os.environ, XCAL_DATA_DIR=str(Path(root).resolve()),
                       GOOGLE_DRIVE_FOLDER_ID="", XCAL_RECOVERY_MODE="1")
    command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(config.APP_ROOT / "src" / "main.py")]
    return subprocess.Popen(command + ["--preview-local"], env=environment, cwd=config.APP_ROOT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
