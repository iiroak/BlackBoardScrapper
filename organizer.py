import json
import re
from pathlib import Path

from downloader import sanitize_filename
import storage


def _safe_dir_name(name: str) -> str:
    name = name.strip().replace("/", "-").replace("\\", "-")
    name = re.sub(r'[<>:"|?*]', "", name)
    if len(name) > 200:
        name = name[:200]
    return name or "unnamed"


def course_dir(term_name: str, course_name: str) -> Path:
    term = _safe_dir_name(term_name)
    course = _safe_dir_name(course_name)
    return storage.current_root() / term / course


def content_dir(base: Path) -> Path:
    return base / "content"


def recording_dir(base: Path) -> Path:
    return base / "recordings"


def get_path_for_content(base: Path, content_title: str, content_id: str) -> Path:
    safe = sanitize_filename(content_title) if content_title else content_id
    return content_dir(base) / safe


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
