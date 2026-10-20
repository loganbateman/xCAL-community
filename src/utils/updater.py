"""Community updates come only from the official GitHub release API.

Catalog records never provide executable code. GitHub's asset digest is required
and checked before installation. Windows publisher signing remains a release gate.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlparse, urljoin

import requests
import config

GITHUB_REPO = "loganbateman/xCAL-community"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
MAX_INSTALLER_BYTES = 250 * 1024 * 1024
_verified_downloads: dict[Path, str] = {}


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str = ""
    update_available: bool = False
    release_url: str = ""
    download_url: str = ""
    asset_name: str = ""
    sha256: str = ""
    message: str = ""


def _clean_version(value):
    return value.strip().lstrip("vV")


def _version_tuple(value):
    return tuple(int(part) for part in value.split(".")) if re.fullmatch(r"\d+\.\d+\.\d+", value) else (0,)


def _clean_sha256(value):
    value = value.removeprefix("sha256:").lower()
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else ""


def _official_asset(url):
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.netloc == "github.com"
            and parsed.path.startswith(f"/{GITHUB_REPO}/releases/download/"))


def _best_asset(assets, version):
    expected = f"xCAL-Setup-{version}.exe"
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("name") != expected:
            continue
        url = str(asset.get("browser_download_url") or "")
        digest = _clean_sha256(str(asset.get("digest") or ""))
        if _official_asset(url) and digest:
            return {"asset_name": expected, "download_url": url, "sha256": digest}
    return {}


def check_for_update() -> UpdateInfo:
    current = config.APP_VERSION
    try:
        response = requests.get(LATEST_RELEASE_URL,
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"xCAL/{current}"},
            timeout=8, allow_redirects=False)
        if response.status_code != 200:
            return UpdateInfo(current, message=f"GitHub release check returned HTTP {response.status_code}. No update was installed.")
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid release response")
    except (requests.RequestException, ValueError):
        return UpdateInfo(current, message="Could not verify Community release information. Try again later.")
    tag = str(data.get("tag_name") or "")
    version = _clean_version(tag)
    if not re.fullmatch(r"v?\d+\.\d+\.\d+", tag) or data.get("draft") or data.get("prerelease"):
        return UpdateInfo(current, message="No stable Community release was found.")
    assets = data.get("assets")
    asset = _best_asset(assets if isinstance(assets, list) else [], version)
    newer = _version_tuple(version) > _version_tuple(current)
    message = f"xCAL {version} is available." if newer else f"xCAL is up to date at {current}."
    if newer and not asset:
        message += " Automatic installation requires a release installer with a verified digest."
    return UpdateInfo(current, latest_version=version, update_available=newer,
        release_url=f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}",
        message=message, **asset)


def _download(url, handle):
    allowed = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
    for _attempt in range(6):
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in allowed:
            raise ValueError("The installer redirected outside GitHub's release service.")
        with requests.get(url, headers={"User-Agent": f"xCAL/{config.APP_VERSION}"},
                          stream=True, timeout=(10, 180), allow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("The installer redirect is missing its destination.")
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                raise ValueError(f"Installer download returned HTTP {response.status_code}.")
            digest, size = hashlib.sha256(), 0
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_INSTALLER_BYTES:
                    raise ValueError("The installer download is unexpectedly large.")
                digest.update(chunk)
                handle.write(chunk)
            if not size:
                raise ValueError("The installer download was empty.")
            return digest.hexdigest()
    raise ValueError("The installer redirected too many times.")


def download_installer(info: UpdateInfo) -> Path:
    if not info.update_available or not _official_asset(info.download_url):
        raise ValueError("The update must be an official xCAL release.")
    trusted = check_for_update()
    if (not trusted.update_available or not trusted.sha256
            or (info.latest_version, info.asset_name, info.download_url, info.sha256)
            != (trusted.latest_version, trusted.asset_name, trusted.download_url, trusted.sha256)):
        raise ValueError("The installer could not be verified against the Community release. Check for updates again.")
    directory = Path(tempfile.mkdtemp(prefix="xcal-community-update-"))
    partial = directory / "installer.part"
    try:
        with partial.open("xb") as handle:
            digest = _download(trusted.download_url, handle)
            handle.flush()
            os.fsync(handle.fileno())
        if digest != trusted.sha256:
            raise ValueError("The installer failed its SHA-256 integrity check.")
        target = directory / trusted.asset_name
        partial.replace(target)
        _verified_downloads[target.resolve()] = digest
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        directory.rmdir()
        raise


def launch_installer(path: Path) -> None:
    installer = Path(path).resolve()
    expected = _verified_downloads.pop(installer, None)
    if not expected or not installer.is_file():
        raise ValueError("Only a verified Community download can be installed.")
    with installer.open("rb") as handle:
        actual = hashlib.file_digest(handle, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("The installer changed after verification. Download it again.")
    subprocess.Popen([str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     close_fds=True, creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
