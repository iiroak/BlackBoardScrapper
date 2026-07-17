import os
from pathlib import Path

BASE_URL = "https://campusvirtual.umayor.cl"
API_BASE = f"{BASE_URL}/learn/api/v1"
PUBLIC_API_BASE = f"{BASE_URL}/learn/api/public/v1"
COLLAB_BASE = "https://us-lti.bbcollab.com/collab/api/csa"

DEFAULT_OUTPUT_DIR = Path(os.environ.get("BB_OUTPUT_DIR", "./backup")).resolve()
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
MANIFEST_FILE = OUTPUT_DIR / "manifest.json"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0"

REQUEST_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 300
MAX_RETRIES = 3
RETRY_DELAY = 2
PAGE_SIZE = 100

ONEDRIVE_ROOT_PATH = os.environ.get("ONEDRIVE_ROOT_PATH", "Campus Archive").strip("/")
