import os
import sys
import hashlib
from pathlib import Path
from dotenv import load_dotenv

APP_ROOT = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent.parent
)

# User work must live outside the install directory so an updater can replace
# the application without touching downloaded sessions or pending offline edits.
_user_data_root = Path(os.getenv("LOCALAPPDATA", "")).expanduser()
if not str(_user_data_root) or str(_user_data_root) == ".":
    _user_data_root = Path.home() / ".xcal"
USER_DATA_DIR = Path(os.getenv("XCAL_DATA_DIR") or (_user_data_root / "xCAL-community"))
ENV_PATH = USER_DATA_DIR / "settings.env"


def _default_oauth_client_file() -> Path:
    """Use a maintainer-injected public client in packaged builds.

    Desktop OAuth client configuration identifies the application; user tokens
    remain private in USER_DATA_DIR. Source checkouts deliberately do not carry
    the client configuration.
    """
    candidates = []
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / "resources" / "oauth-client.json")
    candidates.append(APP_ROOT / "resources" / "oauth-client.json")
    # Public users never choose or persist an OAuth client file. Maintainers
    # inject the Community Desktop client while building the package, and the
    # frozen app reads that bundled resource directly.
    return next((path for path in candidates if path.is_file()),
                APP_ROOT / "resources" / "oauth-client.json")


def clean_drive_folder_id(raw: str) -> str:
    """Extract a bare Drive folder ID from a pasted value or full URL.

    Accepts things like:
      - "1AbC...XyZ"
      - "1AbC...XyZ?dmr=1&ec=..."
      - "https://drive.google.com/drive/folders/1AbC...XyZ?usp=sharing"
    """
    value = (raw or "").strip()
    if not value:
        return ""
    if "/folders/" in value:
        value = value.split("/folders/", 1)[1]
    # Drop any query string or fragment noise from a pasted browser URL.
    for sep in ("?", "#", "&", "/"):
        value = value.split(sep, 1)[0]
    return value.strip()


def reload_environment() -> None:
    global GOOGLE_DRIVE_FOLDER_ID, GOOGLE_OAUTH_CLIENT_FILE
    global DATA_DIR, RECOVERY_MODE
    load_dotenv(ENV_PATH, override=True)
    GOOGLE_DRIVE_FOLDER_ID = clean_drive_folder_id(
        os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    )
    GOOGLE_OAUTH_CLIENT_FILE = str(_default_oauth_client_file())
    DATA_DIR = workspace_dir(GOOGLE_DRIVE_FOLDER_ID)
    RECOVERY_MODE = os.getenv("XCAL_RECOVERY_MODE", "").strip() == "1"
    if RECOVERY_MODE:
        GOOGLE_DRIVE_FOLDER_ID = ""
        DATA_DIR = workspace_dir("")


def workspace_dir(folder_id):
    workspace = hashlib.sha256(folder_id.encode()).hexdigest()[:24] if folder_id else "local"
    return USER_DATA_DIR / "teams" / workspace


reload_environment()

APP_TITLE = "xCAL - Community Preview"
APP_VERSION = "0.2.1"
UPDATE_CHANNEL = "beta"
APP_GEOMETRY = "1280x800"
APP_MIN_SIZE = (1100, 700)

# Team-owned Google Drive permissions control access; no shared app password.

# Verbose logging + an xcal_debug.log file when XCAL_DEBUG is set (dev aid).
DEBUG = os.getenv("XCAL_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# Asset paths (dev: src/assets; frozen: PyInstaller _MEIPASS/assets or exe/assets)
_asset_candidates = []
if getattr(sys, "_MEIPASS", None):
    _asset_candidates.append(Path(sys._MEIPASS) / "assets")
_asset_candidates += [APP_ROOT / "src" / "assets", APP_ROOT / "assets"]
ASSETS_DIR = next((p for p in _asset_candidates if p.exists()), _asset_candidates[-1])
LOGO_PNG = ASSETS_DIR / "logo.png"
LOGO_SQUARE_PNG = ASSETS_DIR / "logo_square.png"
LOGO_ICO = ASSETS_DIR / "logo.ico"
KART_TOP_PNG = ASSETS_DIR / "kart_top.png"

# UI colours â€” Excalibur Racing theme (gray background, red elements)
COLOR_BG_DARK = "#2b2d31"       # main gray background
COLOR_SIDEBAR = "#1f2124"       # darker gray sidebar
COLOR_ACCENT = "#a01729"        # Excalibur deep ruby red
COLOR_ACCENT_HOVER = "#7c0f1e"
COLOR_TEXT = "#f0f0f2"
COLOR_TEXT_MUTED = "#a2a4a9"
COLOR_SUCCESS = "#3fb765"
COLOR_WARNING = "#e6a020"
COLOR_CARD = "#34363b"          # gray card
COLOR_CARD_HOVER = "#3f4147"
COLOR_BORDER = "#45474d"
