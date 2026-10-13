"""GitHub release update checks for xCAL."""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlparse

import requests

import config
import database.client as db

GITHUB_REPO = "loganbateman/xCAL-community"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
MANIFEST_DOC_KEY = "app_update"


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str = ""
    update_available: bool = False
    release_url: str = ""
    download_url: str = ""
    asset_name: str = ""
    sha256: str = ""
    chunk_keys: tuple[str, ...] = ()
    message: str = ""


def check_for_update() -> UpdateInfo:
    """Return latest GitHub Release information for the configured app version."""
    manifest = _manifest_update()
    if manifest is not None:
        return manifest

    current = config.APP_VERSION
    try:
        response = requests.get(
            LATEST_RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"xCAL/{current}",
            },
            timeout=8,
        )
    except requests.RequestException as exc:
        return UpdateInfo(
            current_version=current,
            message=f"Could not reach GitHub: {exc}",
        )

    if response.status_code in (401, 403, 404):
        return UpdateInfo(
            current_version=current,
            message=(
                "Could not check GitHub Releases. This usually means the repo "
                "or its releases are private."
            ),
        )

    if response.status_code != 200:
        return UpdateInfo(
            current_version=current,
            message=f"GitHub returned HTTP {response.status_code}.",
        )

    try:
        data = response.json()
    except ValueError:
        return UpdateInfo(
            current_version=current,
            message="GitHub returned an unreadable update response.",
        )

    latest = _clean_version(str(data.get("tag_name") or data.get("name") or ""))
    if not latest:
        return UpdateInfo(
            current_version=current,
            message="No version tag was found on the latest GitHub Release.",
        )

    asset = _best_asset(data.get("assets") or [])
    release_url = str(data.get("html_url") or "")
    is_newer = _version_tuple(latest) > _version_tuple(current)
    if is_newer:
        return UpdateInfo(
            current_version=current,
            latest_version=latest,
            update_available=True,
            release_url=release_url,
            download_url=asset.get("browser_download_url", ""),
            asset_name=asset.get("name", ""),
            sha256=asset.get("sha256", ""),
            message=f"xCAL {latest} is available.",
        )

    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        release_url=release_url,
        download_url=asset.get("browser_download_url", ""),
        asset_name=asset.get("name", ""),
        sha256=asset.get("sha256", ""),
        message=f"xCAL is up to date at {current}.",
    )


def _manifest_update() -> UpdateInfo | None:
    try:
        manifest = db.fetch_app_doc(MANIFEST_DOC_KEY)
    except Exception:
        return None
    if not manifest:
        return None

    current = config.APP_VERSION
    latest = _clean_version(str(manifest.get("version") or ""))
    if not latest:
        return None

    release_url = str(manifest.get("release_url") or "")
    download_url = str(manifest.get("download_url") or "")
    asset_name = str(manifest.get("asset_name") or "")
    sha256 = _clean_sha256(str(manifest.get("sha256") or ""))
    chunk_keys = tuple(
        str(key) for key in (manifest.get("chunk_keys") or []) if str(key))
    is_newer = _version_tuple(latest) > _version_tuple(current)
    if is_newer:
        return UpdateInfo(
            current_version=current,
            latest_version=latest,
            update_available=True,
            release_url=release_url,
            download_url=download_url,
            asset_name=asset_name,
            sha256=sha256,
            chunk_keys=chunk_keys,
            message=f"xCAL {latest} is available.",
        )

    return UpdateInfo(
        current_version=current,
        latest_version=latest,
        release_url=release_url,
        download_url=download_url,
        asset_name=asset_name,
        sha256=sha256,
        chunk_keys=chunk_keys,
        message=f"xCAL is up to date at {current}.",
    )


def _best_asset(assets: list[dict[str, Any]]) -> dict[str, str]:
    ranked: list[tuple[int, dict[str, str]]] = []
    for asset in assets:
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not name or not url:
            continue
        lowered = name.lower()
        score = 0
        if lowered.endswith(".exe"):
            score += 20
        if "setup" in lowered or "installer" in lowered:
            score += 10
        if lowered.endswith(".zip"):
            score += 5
        ranked.append((score, {
            "name": name,
            "browser_download_url": url,
            "sha256": _clean_sha256(str(asset.get("digest") or "")),
        }))
    if not ranked:
        return {}
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _clean_version(value: str) -> str:
    return value.strip().lstrip("vV")


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", _clean_version(value))
    if not parts:
        return (0,)
    return tuple(int(part) for part in parts)


def _clean_sha256(value: str) -> str:
    digest = value.strip().lower()
    if digest.startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""


def download_installer(info: UpdateInfo) -> Path:
    """Download and verify the installer selected by the update manifest."""
    if not info.update_available:
        raise ValueError("No newer xCAL update is available")
    asset_name = Path(info.asset_name or "").name
    if not re.fullmatch(r"xCAL-Setup-[0-9.]+\.exe", asset_name, re.I):
        raise ValueError("The update does not provide a valid xCAL installer")

    target_dir = Path(tempfile.gettempdir()) / "xcal-updates"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / asset_name
    partial = target.with_suffix(target.suffix + ".part")

    digest = hashlib.sha256()
    size = 0
    try:
        if info.chunk_keys:
            with partial.open("wb") as handle:
                for expected_index, key in enumerate(info.chunk_keys):
                    document = db.fetch_app_doc(key)
                    if not document:
                        raise ValueError(
                            f"Update package chunk {expected_index + 1} is missing")
                    if str(document.get("version") or "") != info.latest_version:
                        raise ValueError("The update package version is inconsistent")
                    if int(document.get("index", -1)) != expected_index:
                        raise ValueError("The update package chunk order is invalid")
                    try:
                        chunk = base64.b64decode(
                            str(document.get("content") or ""), validate=True)
                    except (ValueError, TypeError) as exc:
                        raise ValueError(
                            "The update package contains an invalid chunk") from exc
                    size += len(chunk)
                    if size > 250 * 1024 * 1024:
                        raise ValueError("The installer download is unexpectedly large")
                    digest.update(chunk)
                    handle.write(chunk)
        else:
            parsed = urlparse(info.download_url)
            expected_prefix = f"/{GITHUB_REPO}/releases/download/"
            if (parsed.scheme != "https" or parsed.hostname != "github.com"
                    or not parsed.path.startswith(expected_prefix)):
                raise ValueError("The installer URL is not an official xCAL release")
            with requests.get(
                    info.download_url,
                    headers={"User-Agent": f"xCAL/{config.APP_VERSION}"},
                    stream=True, timeout=(10, 180)) as response:
                response.raise_for_status()
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > 250 * 1024 * 1024:
                            raise ValueError(
                                "The installer download is unexpectedly large")
                        digest.update(chunk)
                        handle.write(chunk)
        if size == 0:
            raise ValueError("The installer download was empty")
        if info.sha256 and digest.hexdigest() != info.sha256:
            raise ValueError("The installer failed its SHA-256 integrity check")
        partial.replace(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def launch_installer(path: Path) -> None:
    """Start an in-place silent update; Inno Setup closes the running app."""
    installer = Path(path).resolve()
    if not installer.is_file() or installer.suffix.lower() != ".exe":
        raise ValueError("The downloaded installer could not be found")
    subprocess.Popen(
        [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
         "/CLOSEAPPLICATIONS"],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
