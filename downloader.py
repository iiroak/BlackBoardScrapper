import logging
import re
import hashlib
import time
from pathlib import Path

import requests

from config import DOWNLOAD_TIMEOUT

logger = logging.getLogger("campus-archive")


INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def sanitize_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = INVALID_CHARS.sub("_", name)
    if not name:
        name = "unnamed"
    if len(name) > 200:
        base, ext = Path(name).stem, Path(name).suffix
        name = base[:200] + ext
    return name


def get_file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_probably_login_page(
    resp: requests.Response, expected_size: int | None, expected_mime: str | None = None
) -> bool:
    """Reject a successful HTML login page returned instead of a file."""
    content_type = resp.headers.get("content-type", "").lower()
    if expected_mime and expected_mime.lower().startswith("text/html"):
        return False
    return "text/html" in content_type and expected_size not in (None, 0)


def download_file(
    session: requests.Session,
    url: str,
    dest_path: Path,
    expected_size: int | None = None,
    description: str = "",
    expected_sha256: str | None = None,
    max_retries: int = 3,
    expected_mime: str | None = None,
    on_error=None,
) -> bool:
    try:
        expected_size = int(expected_size or 0)
    except (TypeError, ValueError):
        expected_size = 0
    if dest_path.exists() and (expected_size in (None, 0) or dest_path.stat().st_size == expected_size):
        if not expected_sha256 or get_file_hash(dest_path) == expected_sha256:
            return True

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = dest_path.with_name(f".{dest_path.name}.part")

    for attempt in range(max_retries):
        offset = partial_path.stat().st_size if partial_path.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else None
        try:
            resp = session.get(url, headers=headers, stream=True, timeout=DOWNLOAD_TIMEOUT)
            if resp.status_code != 200:
                if resp.status_code == 206 and offset:
                    content_range = resp.headers.get("content-range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        resp.close()
                        raise ValueError("rango de reanudación inválido")
                elif resp.status_code == 416 and offset:
                    # The server no longer accepts the stale partial range.
                    resp.close()
                    partial_path.unlink(missing_ok=True)
                    if attempt < max_retries - 1:
                        continue
                    message = "HTTP 416 después de reiniciar la descarga"
                    logger.error("%s: %s", description, message)
                    if on_error:
                        on_error(message)
                    return False
                elif offset and resp.status_code == 200:
                    # The server ignored Range; restart cleanly from byte zero.
                    offset = 0
                else:
                    retryable = resp.status_code == 429 or resp.status_code >= 500
                    resp.close()
                    if retryable and attempt < max_retries - 1:
                        time.sleep(2 * (attempt + 1))
                        continue
                    message = f"HTTP {resp.status_code}"
                    logger.error("%s: %s", description, message)
                    if on_error:
                        on_error(message)
                    return False
            elif offset:
                offset = 0

            if resp.status_code == 206 and not offset:
                offset = 0

            if _is_probably_login_page(resp, expected_size, expected_mime):
                resp.close()
                message = "el servidor devolvió una página de login"
                logger.error("%s: %s", description, message)
                if on_error:
                    on_error(message)
                return False

            response_type = resp.headers.get("content-type", "").split(";", 1)[0].lower().strip()
            mode = "ab" if resp.status_code == 206 and partial_path.exists() else "wb"
            with partial_path.open(mode) as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)
            resp.close()

            actual_size = partial_path.stat().st_size
            if expected_size not in (None, 0) and actual_size != expected_size:
                expected_type = (expected_mime or "").split(";", 1)[0].lower().strip()
                if expected_type and response_type == expected_type:
                    logger.warning("%s: Blackboard indicó %s bytes, servidor entregó %s", description, expected_size, actual_size)
                else:
                    raise ValueError(f"tamaño incorrecto ({actual_size} vs {expected_size})")

            if expected_sha256 and get_file_hash(partial_path) != expected_sha256:
                raise ValueError("SHA-256 incorrecto")

            partial_path.replace(dest_path)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            partial_path.unlink(missing_ok=True)
            message = f"error - {e}"
            logger.error("%s: %s", description, message, exc_info=True)
            if on_error:
                on_error(message)
            return False

    return False
