import os
import sys
from pathlib import Path

BASE_URL = "https://campusvirtual.umayor.cl"
API_BASE = f"{BASE_URL}/learn/api/v1"
PUBLIC_API_BASE = f"{BASE_URL}/learn/api/public/v1"
COLLAB_BASE = "https://us-lti.bbcollab.com/collab/api/csa"

APP_NAME = "Campus Archive"
APP_VERSION = "1.2.1"
GITHUB_REPO = "iiroak/BlackBoardScrapper"


def user_data_dir() -> Path:
    """Return a writable per-user directory for session and app state."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "Campus Archive"


def documents_dir() -> Path:
    configured = os.environ.get("BB_DOCUMENTS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    documents = Path.home() / "Documents"
    return documents if documents.is_dir() else Path.home()


USER_DATA_DIR = user_data_dir()
STORAGE_STATE_FILE = USER_DATA_DIR / "storage.json"
DEFAULT_OUTPUT_DIR = Path(os.environ.get("BB_OUTPUT_DIR", documents_dir() / APP_NAME)).expanduser().resolve()
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0"

REQUEST_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 300
MAX_RETRIES = 3
RETRY_DELAY = 2
PAGE_SIZE = 100

ONEDRIVE_ROOT_PATH = os.environ.get("ONEDRIVE_ROOT_PATH", "Campus Archive").strip("/")
