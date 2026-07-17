import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from downloader import get_file_hash
import storage

logger = logging.getLogger("campus-archive")


_MANIFEST_LOCK = threading.RLock()


class Manifest:
    """Persistent index of downloaded assets.

    Paths are stored relative to OUTPUT_DIR so the backup remains portable
    between Windows and WSL. Legacy v1 paths are resolved transparently.
    """

    VERSION = 2

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        manifest_file = storage.manifest_path()
        if not manifest_file.exists():
            return {"version": self.VERSION, "courses": {}}
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("manifest is not an object")
            data.setdefault("courses", {})
            data["version"] = self.VERSION
            return data
        except Exception:
            return {"version": self.VERSION, "courses": {}}

    @staticmethod
    def _portable_path(path: str | Path) -> str:
        text = str(path).replace("\\", "/")
        output = str(storage.current_root()).replace("\\", "/").rstrip("/")
        if text.startswith(output + "/"):
            return text[len(output) + 1:]

        # Windows paths from the old manifest: C:/.../BlackBoardScraper/backup/...
        match = re.search(r"(?:^|/)backup/(.*)$", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return text.lstrip("/")

    def _normalize_paths(self):
        for course in self.data.get("courses", {}).values():
            for entry in course.get("files", {}).values():
                if entry.get("path"):
                    entry["path"] = self._portable_path(entry["path"])

    def save(self):
        with _MANIFEST_LOCK:
            self._normalize_paths()
            manifest_file = storage.manifest_path()
            manifest_file.parent.mkdir(parents=True, exist_ok=True)
            self.data["version"] = self.VERSION
            self.data["last_run"] = datetime.now().isoformat()
            payload = json.dumps(self.data, indent=2, ensure_ascii=False)
            tmp = manifest_file.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            for attempt in range(5):
                try:
                    tmp.replace(manifest_file)
                    return
                except PermissionError:
                    # OneDrive can briefly lock the destination during sync.
                    time.sleep(0.5 * (attempt + 1))

            # The WSL OneDrive mount may reject rename while allowing overwrite.
            # Keep the temp file if this fallback also fails so it can be recovered.
            manifest_file.write_text(payload, encoding="utf-8")
            tmp.unlink(missing_ok=True)

    def get_course_state(self, course_id: str) -> dict:
        return self.data.setdefault("courses", {}).setdefault(course_id, {})

    def set_course_state(self, course_id: str, state: dict):
        self.data.setdefault("courses", {})[course_id] = state

    def _resolve_path(self, stored_path: str) -> Path | None:
        if not stored_path:
            return None
        raw = str(stored_path).replace("\\", "/")
        candidate = Path(raw)
        if candidate.is_absolute() and candidate.exists():
            return candidate.resolve()
        portable = self._portable_path(raw)
        output = storage.current_root()
        resolved = (output / portable).resolve()
        try:
            resolved.relative_to(output.resolve())
        except ValueError:
            return None
        return resolved if resolved.exists() else None

    def _entry(self, course_id: str, file_ref: str) -> dict | None:
        return self.get_course_state(course_id).setdefault("files", {}).get(file_ref)

    def file_needs_download(
        self,
        course_id: str,
        file_ref: str,
        file_name: str,
        file_size: int,
        modified_date: int | None,
        verify_hash: bool = True,
    ) -> bool:
        try:
            file_size = int(file_size or 0)
        except (TypeError, ValueError):
            file_size = 0
        cached = self._entry(course_id, file_ref)
        if cached is None:
            return True
        cached_expected_size = cached.get("expected_size", cached.get("size"))
        if file_size not in (None, 0) and cached_expected_size not in (None, 0, file_size):
            return True
        if modified_date and cached.get("modified") not in (None, modified_date):
            return True

        path = self._resolve_path(cached.get("path", ""))
        if path is None:
            return True
        cached_actual_size = cached.get("actual_size")
        if cached_actual_size not in (None, 0):
            if path.stat().st_size != cached_actual_size:
                return True
        elif file_size not in (None, 0) and path.stat().st_size != file_size:
            return True

        stored_hash = cached.get("sha256")
        if verify_hash and stored_hash and get_file_hash(path) != stored_hash:
            return True
        return False

    def is_downloaded(
        self, course_id: str, file_ref: str, file_size: int = 0, modified_date: int | None = None
    ) -> bool:
        return not self.file_needs_download(
            course_id, file_ref, "", file_size, modified_date
        )

    def mark_downloaded(
        self,
        course_id: str,
        file_ref: str,
        file_name: str,
        file_size: int,
        dest_path: str,
        modified_date: int | None = None,
        asset_type: str = "file",
        source_url: str = "",
        persist: bool = True,
    ):
        path = Path(dest_path)
        actual_size = path.stat().st_size
        try:
            file_size = int(file_size or 0)
        except (TypeError, ValueError):
            file_size = 0
        course = self.get_course_state(course_id)
        files = course.setdefault("files", {})
        previous = files.get(file_ref, {})
        files[file_ref] = {
            "name": file_name,
            "size": file_size or actual_size,
            "expected_size": file_size or actual_size,
            "actual_size": actual_size,
            "path": self._portable_path(path),
            "modified": modified_date,
            "sha256": get_file_hash(path),
            "type": asset_type,
            "source_url": source_url,
            "downloaded_at": previous.get("downloaded_at") or datetime.now().isoformat(),
            "verified_at": datetime.now().isoformat(),
            "status": "verified",
        }
        if persist:
            self.save()

    def set_sync_metadata(self, course_id: str, **kwargs):
        course = self.get_course_state(course_id)
        course.update(kwargs)
        self.save()

    def reconcile(self, course_id: str, expected_files: list[dict]) -> list[dict]:
        """Reconcile expected assets with disk and return assets still missing."""
        missing = []
        files = self.get_course_state(course_id).setdefault("files", {})
        for expected in expected_files:
            ref = expected.get("ref", "")
            if not ref:
                continue
            cached = files.get(ref)
            path = Path(expected.get("path", ""))
            if not path.is_absolute():
                path = storage.current_root() / self._portable_path(path)
            if path.exists() and expected.get("size", 0) in (0, path.stat().st_size):
                if not cached or cached.get("sha256") != get_file_hash(path):
                    self.mark_downloaded(
                        course_id,
                        ref,
                        expected.get("name", ""),
                        expected.get("size", 0),
                        str(path),
                        expected.get("modified"),
                        expected.get("type", "file"),
                        expected.get("url", ""),
                    )
                continue
            if self.file_needs_download(
                course_id, ref, expected.get("name", ""), expected.get("size", 0), expected.get("modified")
            ):
                missing.append(expected)
        return missing

    def reconcile_all(self, courses_tree: dict[str, list[dict]]) -> dict[str, list[dict]]:
        return {
            course_id: self.reconcile(course_id, files)
            for course_id, files in courses_tree.items()
        }

    def audit(self) -> dict[str, int]:
        """Verify every indexed file and migrate legacy paths/hashes in place."""
        result = {"checked": 0, "verified": 0, "missing": 0, "corrupt": 0, "rehash": 0, "repaired": 0, "corrupt_files": []}
        for course_id, course in self.data.get("courses", {}).items():
            for ref, entry in course.get("files", {}).items():
                result["checked"] += 1
                entry["path"] = self._portable_path(entry.get("path", ""))
                path = self._resolve_path(entry["path"])
                if path is None:
                    expected = storage.current_root() / entry["path"]
                    alternate = Path(str(expected) + ".txt")
                    if alternate.exists() and entry.get("size", 0) in (0, alternate.stat().st_size):
                        alternate.replace(expected)
                        path = expected
                        result["repaired"] += 1
                if path is None:
                    entry["status"] = "missing"
                    result["missing"] += 1
                    continue
                actual_size = path.stat().st_size
                entry["actual_size"] = actual_size
                expected_size = entry.get("size", 0)
                if expected_size not in (0, actual_size):
                    entry["status"] = "corrupt"
                    result["corrupt"] += 1
                    result["corrupt_files"].append({
                        "name": entry.get("name", ""),
                        "path": entry["path"],
                        "reason": f"tamaño: esperado {expected_size}, disco {actual_size}",
                    })
                    logger.warning("Corrupto (tamaño): %s — esperado %s, disco %s", entry.get("name"), expected_size, actual_size)
                    continue
                digest = get_file_hash(path)
                if entry.get("sha256") and entry["sha256"] != digest:
                    entry["status"] = "corrupt"
                    result["corrupt"] += 1
                    result["corrupt_files"].append({
                        "name": entry.get("name", ""),
                        "path": entry["path"],
                        "reason": "hash SHA-256 no coincide",
                    })
                    logger.warning("Corrupto (hash): %s", entry.get("name"))
                    continue
                if not entry.get("sha256"):
                    result["rehash"] += 1
                entry["sha256"] = digest
                entry["status"] = "verified"
                entry["verified_at"] = datetime.now().isoformat()
                result["verified"] += 1
        self.data["last_audit"] = datetime.now().isoformat()
        self.save()
        return result
