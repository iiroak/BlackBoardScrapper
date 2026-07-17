import json
import os
import shutil
import tempfile
from pathlib import Path

from config import DEFAULT_OUTPUT_DIR, ONEDRIVE_ROOT_PATH, STORAGE_STATE_FILE
from downloader import get_file_hash


STATE_FILE = STORAGE_STATE_FILE
LEGACY_STATE_FILE = Path.home() / ".blackboard-scraper-storage.json"


class StorageError(RuntimeError):
    pass


class StorageConflict(StorageError):
    def __init__(self, paths):
        self.paths = paths
        super().__init__(f"Hay {len(paths)} archivos en conflicto en el nuevo destino")


def current_root() -> Path:
    for state_file in (STATE_FILE, LEGACY_STATE_FILE):
        try:
            value = json.loads(state_file.read_text(encoding="utf-8")).get("root", "")
            if value:
                return Path(value).expanduser().resolve()
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
    return DEFAULT_OUTPUT_DIR


def default_root() -> Path:
    return DEFAULT_OUTPUT_DIR


def manifest_path() -> Path:
    return current_root() / "manifest.json"


def save_selection(root: Path, kind: str):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"root": str(root), "kind": kind}), encoding="utf-8")
    os.chmod(STATE_FILE, 0o600)


def detect_onedrive_folder() -> Path | None:
    candidates = []
    for variable in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value).expanduser())
    candidates.extend([Path.home() / "OneDrive", Path.home() / "OneDrive - Personal"])
    windows_users = Path("/mnt/c/Users")
    if windows_users.is_dir():
        for user_dir in windows_users.iterdir():
            if user_dir.is_dir() and user_dir.name not in {"Public", "Default", "Default User"}:
                candidates.extend([user_dir / "OneDrive", user_dir / "OneDrive - Personal"])
    for candidate in candidates:
        if candidate.is_dir() and not _inside(candidate, current_root()):
            return candidate.resolve()
    return None


def onedrive_root() -> Path | None:
    folder = detect_onedrive_folder()
    return (folder / ONEDRIVE_ROOT_PATH).resolve() if folder else None


def choose_folder() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title="Selecciona la carpeta de almacenamiento")
        root.destroy()
        return Path(selected).expanduser().resolve() if selected else None
    except Exception:
        return None


def info() -> dict:
    root = current_root()
    onedrive = onedrive_root()
    if root == DEFAULT_OUTPUT_DIR:
        kind = "local"
    elif onedrive and root == onedrive:
        kind = "onedrive"
    else:
        kind = "custom"
    try:
        usage = shutil.disk_usage(root if root.exists() else root.parent)
        free = usage.free
        total = usage.total
    except OSError:
        free = total = 0
    return {
        "kind": kind,
        "root": str(root),
        "local_root": str(DEFAULT_OUTPUT_DIR),
        "onedrive_root": str(onedrive) if onedrive else "",
        "free_bytes": free,
        "total_bytes": total,
        "exists": root.exists(),
    }


def migrate_to(destination: Path, kind: str, callback=None):
    source = current_root().resolve()
    destination = destination.expanduser().resolve()
    if source == destination:
        save_selection(destination, kind)
        return {"moved": 0, "total": 0}
    if _inside(destination, source):
        raise StorageError("El destino no puede estar dentro del almacenamiento actual")

    source_files = files(source)
    conflicts = []
    for item in source_files:
        target = destination / item.relative_to(source)
        relative = item.relative_to(source)
        if target.exists() and relative.as_posix() != "manifest.json" and not _same_file(item, target):
            conflicts.append(str(item.relative_to(source)))
    if conflicts:
        raise StorageConflict(conflicts)

    destination.mkdir(parents=True, exist_ok=True)
    moved = 0
    for item in source_files:
        relative = item.relative_to(source)
        target = destination / relative
        if target.exists() and relative.as_posix() == "manifest.json":
            _copy_atomic(item, target)
            item.unlink()
        elif target.exists():
            item.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_atomic(item, target)
            if not _same_file(item, target):
                raise StorageError(f"No se pudo verificar {relative}")
            item.unlink()
        moved += 1
        if callback:
            callback("progress", str(relative), moved, len(source_files))

    for directory in sorted((path for path in source.rglob("*") if path.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        source.rmdir()
    except OSError:
        pass
    save_selection(destination, kind)
    return {"moved": moved, "total": len(source_files)}


def files(root: Path | None = None):
    root = root or current_root()
    return [path for path in root.rglob("*") if path.is_file() and not path.name.endswith((".part", ".tmp"))]


def _same_file(left: Path, right: Path) -> bool:
    if not right.is_file() or left.stat().st_size != right.stat().st_size:
        return False
    return get_file_hash(left) == get_file_hash(right)


def _copy_atomic(source: Path, target: Path):
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        shutil.copy2(source, temporary_path)
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
