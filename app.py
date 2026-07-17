import json
import logging
import queue
import threading
import time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request

from config import USER_DATA_DIR, APP_VERSION, GITHUB_REPO
from auth import (
    console_session_setup,
    get_console_script,
    load_session,
    make_session,
    normalize_cookie_header,
    save_session,
    validate_session,
)
from bb_client import BBClient
from collab_client import CollabClient
from config import BASE_URL
from downloader import download_file, sanitize_filename
from content_sync import (
    asset_path,
    download_asset,
    extract_embedded_attachments,
    download_content_tree,
    item_assets,
    resolve_url,
)
from manifest import Manifest
import storage
from organizer import content_dir, course_dir, save_json

LOG_DIR = USER_DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / "campus-archive.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("campus-archive")

app = Flask(__name__)

state = {
    "session": None,
    "xsrf": None,
    "user": None,
    "connected": False,
}

progress_streams: dict[str, queue.Queue] = {}
tasks: dict[str, threading.Thread] = {}
task_progress: dict[str, dict] = {}
next_task_id = 0
_lock = threading.Lock()

def _get_client():
    if state["session"]:
        return BBClient(state["session"], state["xsrf"])
    return None


def _set_session(cookies, xsrf):
    session = make_session(cookies, xsrf)
    user = validate_session(session)
    if user:
        save_session(cookies, xsrf)
        state["session"] = session
        state["xsrf"] = xsrf
        state["user"] = user
        state["connected"] = True
        return True
    return False


# ── Auth ──────────────────────────────────────────────

def _get_session_expiry(cookies_list: list[dict]) -> int | None:
    for c in cookies_list:
        if c.get("name") == "BbRouter":
            val = c.get("value", "")
            m = __import__("re").search(r'expires:(\d+)', val)
            if m:
                return int(m.group(1))
    return None


@app.route("/api/auth/status")
def auth_status():
    s = state["session"]
    if s:
        user = validate_session(s)
        if user:
            state["connected"] = True
            state["user"] = user
            return jsonify({"connected": True, "user": _user_summary(user)})
        state["connected"] = False
        state["session"] = None

    # Auto-try cached session.json
    from auth import load_session as _try_load_session, SESSION_FILE
    loaded_s, loaded_xsrf = _try_load_session()
    if loaded_s:
        user = validate_session(loaded_s)
        if user:
            _set_session_from_loaded(loaded_s, loaded_xsrf)
            # Parse expiry from stored cookies
            expiry = None
            try:
                data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                expiry = _get_session_expiry(data.get("cookies", []))
            except Exception:
                pass
            return jsonify({
                "connected": True,
                "user": _user_summary(user),
                "restored": True,
                "expires_at": expiry,
            })
        # Session expired — delete stale file
        from auth import SESSION_FILE
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
        legacy = Path("./session.json")
        if legacy.exists():
            legacy.unlink()

    return jsonify({"connected": False, "user": None})


def _user_summary(user):
    return {
        "name": f"{user.get('givenName', '')} {user.get('familyName', '')}".strip(),
        "email": user.get("emailAddress", ""),
    }


@app.route("/api/auth/connect", methods=["POST"])
def auth_connect():
    # Try session file first
    s, x = load_session()
    if s:
        user = validate_session(s)
        if user:
            _set_session_from_loaded(s, x)
            return jsonify({"success": True, "method": "session_file", "user": _user_summary(user)})

    state["connected"] = False
    state["session"] = None
    return jsonify({"success": False, "error": "No hay una sesión guardada. Pega las cookies de Blackboard primero."}), 400


@app.route("/api/auth/connect_manual", methods=["POST"])
def auth_connect_manual():
    data = request.get_json()
    raw = normalize_cookie_header((data or {}).get("cookies", ""))
    cookies = []
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies.append({"name": k, "value": v, "domain": "campusvirtual.umayor.cl", "path": "/"})
    if not any(cookie.get("name") == "BbRouter" for cookie in cookies):
        return jsonify({"success": False, "error": "No se encontró la cookie BbRouter. Copia una solicitud de campusvirtual.umayor.cl, no de umayor.eesysoft.com."}), 400
    from auth import _get_xsrf_from_cookies
    xsrf = _get_xsrf_from_cookies(cookies)
    if _set_session(cookies, xsrf):
        return jsonify({"success": True, "user": _user_summary(state["user"])})
    return jsonify({"success": False, "error": "Cookies inválidas"}), 400


@app.route("/api/auth/console", methods=["POST"])
def auth_console():
    data = request.get_json()
    user_data = (data or {}).get("user", {})
    cookies_str = (data or {}).get("cookies", "")
    s, x = console_session_setup(user_data, cookies_str)
    if s:
        state["session"] = s
        state["xsrf"] = x
        state["user"] = user_data
        state["connected"] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "No se pudo validar la sesión"}), 400


@app.route("/api/auth/script")
def auth_script():
    return jsonify({"script": get_console_script()})


@app.route("/api/auth/disconnect", methods=["POST"])
def auth_disconnect():
    state["session"] = None
    state["xsrf"] = None
    state["user"] = None
    state["connected"] = False
    return jsonify({"success": True})


# ── Storage ────────────────────────────────────────────

def _storage_status():
    current = storage.info()
    cloud = Manifest().data.get("cloud", {})
    if cloud.get("folder"):
        cloud_root = (Path(cloud["folder"]) / cloud.get("root_path", "Campus Archive")).resolve()
        if cloud_root != Path(current["root"]).resolve():
            cloud = {}
    current["cloud"] = cloud
    return current


def _has_active_task():
    return any(thread.is_alive() for thread in tasks.values())


@app.route("/api/storage/status")
def storage_status():
    return jsonify(_storage_status())


@app.route("/api/storage/select", methods=["POST"])
def storage_select():
    if _has_active_task():
        return jsonify({"error": "Espera a que termine la operación actual antes de cambiar el almacenamiento"}), 409
    data = request.get_json() or {}
    kind = data.get("kind", "custom")
    if kind == "local":
        destination = storage.default_root()
    elif kind == "onedrive":
        destination = storage.onedrive_root()
        if not destination:
            return jsonify({"error": "No se encontró la carpeta de OneDrive en este equipo"}), 400
    else:
        raw_path = str(data.get("path", "")).strip()
        destination = Path(raw_path).expanduser().resolve() if raw_path else storage.choose_folder()
        if not destination:
            return jsonify({"error": "Selecciona una carpeta válida"}), 400
    if destination == storage.current_root():
        storage.save_selection(destination, kind)
        return jsonify(_storage_status())

    global next_task_id
    total = max(1, len(storage.files()))
    with _lock:
        task_id = str(next_task_id)
        next_task_id += 1
        progress_streams[task_id] = queue.Queue()
        task_progress[task_id] = {"total": total, "completed": 0, "status": "running"}

    def run(tid):
        try:
            result = storage.migrate_to(
                destination,
                kind,
                callback=lambda ptype, message, completed, total: _push_progress(
                    tid, ptype, message, completed=completed, total=total
                ),
            )
            _push_progress(tid, "complete", f"Almacenamiento cambiado: {result['moved']} archivos trasladados", completed=total, total=total)
        except Exception as error:
            _push_progress(tid, "error", str(error))
            _push_progress(tid, "complete", "No se cambió el almacenamiento", completed=0, total=total)
        finally:
            with _lock:
                if task_progress.get(tid):
                    task_progress[tid]["status"] = "done"

    thread = threading.Thread(target=run, args=(task_id,), daemon=True)
    tasks[task_id] = thread
    thread.start()
    return jsonify({"task_id": task_id, "total": total})


def _set_session_from_loaded(s, x):
    state["session"] = s
    state["xsrf"] = x
    user = validate_session(s)
    if user:
        state["user"] = user
        state["connected"] = True


# ── Courses ───────────────────────────────────────────

@app.route("/api/courses")
def get_courses():
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401

    user_id = state["user"]["id"]
    memberships = client.get_courses(user_id)
    manifest_data = Manifest().data.get("courses", {})
    courses = []
    for m in memberships:
        c = m.get("course", {})
        course_id = c.get("id", "")
        try:
            detail = client.get_course(course_id)
        except Exception:
            detail = c
        entries = list((manifest_data.get(course_id, {}) or {}).get("files", {}).values())
        courses.append({
            "id": course_id,
            "name": c.get("name", "Sin nombre"),
            "display_id": c.get("courseId", ""),
            "term": (c.get("term", {}) or {}).get("name", "Sin período"),
            "instructors": [
                {"name": f"{i.get('user', {}).get('givenName', '')} {i.get('user', {}).get('familyName', '')}".strip()}
                for i in (detail.get("instructorsMembership", []) if isinstance(detail, dict) else [])
            ],
            "is_available": c.get("isAvailable", True),
            "file_count": len(entries),
            "verified_files": sum(1 for entry in entries if entry.get("status") == "verified"),
            "error_files": sum(1 for entry in entries if entry.get("status") in ("corrupt", "failed")),
            "total_size": sum(entry.get("actual_size", entry.get("size", 0)) or 0 for entry in entries),
        })

    courses.sort(key=lambda x: x["name"])
    return jsonify(courses)


@app.route("/api/courses/<course_id>/content")
def get_course_content(course_id):
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401

    # Get course info for path resolution
    from downloader import sanitize_filename
    try:
        detail = client.get_course(course_id)
    except Exception:
        detail = {}
    course_name = detail.get("name", "Sin nombre")
    term = detail.get("term", {}) or {}
    term_name = term.get("name", "Sin período")

    manifest = Manifest()
    base_path = content_dir(course_dir(term_name, course_name))

    def asset_is_downloaded(asset: dict, asset_type: str = "file") -> bool:
        """Use the manifest first, then repair a valid file found on disk."""
        ref = asset.get("ref", "")
        try:
            size = int(asset.get("size", 0) or 0)
        except (TypeError, ValueError):
            size = 0
        modified = asset.get("modified")
        if manifest.is_downloaded(course_id, ref, size, modified):
            return True
        path = Path(asset.get("path", ""))
        if not path.is_file() or size not in (0, path.stat().st_size):
            return False
        manifest.mark_downloaded(
            course_id,
            ref,
            asset.get("name", ""),
            size,
            str(path),
            modified,
            asset_type,
            asset.get("url", ""),
            persist=False,
        )
        return True

    def _get_item_body(item: dict) -> str:
        """Extract the html body from a content item if present."""
        detail_inner = item.get("contentDetail", {}) or {}
        body = detail_inner.get("body", "")
        if not body:
            body = item.get("body", "")
        if isinstance(body, dict):
            body = body.get("text", "") or body.get("html", "")
        return body or ""

    def build_tree(content_id: str, current_base: Path) -> list[dict]:
        nodes = []
        try:
            children = client.get_content_children(course_id, content_id)
        except Exception:
            return nodes

        for item in children:
            handler = item.get("contentHandler", "")
            cid = item.get("id", "")
            title = item.get("title", cid)
            detail_item = (item.get("contentDetail", {}) or {})
            modified = item.get("modifiedDate")

            is_container = handler in ("resource/x-bb-folder", "resource/x-bb-lesson")
            is_file = handler == "resource/x-bb-file"
            content_type = "folder" if is_container else "file" if is_file else "other"

            node = {
                "id": cid,
                "title": title,
                "handler": handler,
                "type": content_type,
                "modified": modified,
                "children": [],
                "attachments": [],
                "path": str(current_base / (sanitize_filename(title or cid) if is_container else "")),
            }

            if is_container:
                safe = sanitize_filename(title) or cid
                node["children"] = build_tree(cid, current_base / safe)
            elif is_file:
                fd = (detail_item.get("resource/x-bb-file", {}) or {}).get("file", {}) or {}
                file_ref = fd.get("existingFileReference", cid)
                file_name = fd.get("fileName", title)
                try:
                    file_size = int(fd.get("fileSize", 0) or 0)
                except (TypeError, ValueError):
                    file_size = 0
                file_url = resolve_url(fd.get("permanentUrl", ""))
                if fd.get("forceDownload") and "xythos-download=" not in file_url:
                    file_url += "&" if "?" in file_url else "?"
                    file_url += "xythos-download=true"
                node["file"] = {
                    "name": file_name,
                    "size": file_size,
                    "mime": fd.get("mimeType", ""),
                    "ref": file_ref,
                    "url": file_url,
                    "path": str(current_base / sanitize_filename(file_name)),
                }
                node["type"] = "file"

                node["file"]["modified"] = modified
                node["downloaded"] = asset_is_downloaded(node["file"])
            else:
                # Non-file content: externallink, asmt-test-link, courselink, document, etc.
                node["type"] = "other"
                node["data"] = _extract_item_data(item, detail_item, cid, title)
                # Check if we already saved this item on disk
                saved_path = current_base / f"{sanitize_filename(title or cid)}.json"
                node["downloaded"] = saved_path.exists()

            for attachment in extract_embedded_attachments(item):
                attachment["path"] = str(asset_path(current_base, title, attachment["name"], True))
                attachment["modified"] = modified
                attachment["downloaded"] = asset_is_downloaded(attachment, "embedded")
                node["attachments"].append(attachment)

            if not node["attachments"]:
                node.pop("attachments")

            nodes.append(node)
        return nodes

    def _extract_item_data(item: dict, detail_item: dict, content_id: str, title: str) -> dict:
        """Extract metadata from any content item for saving."""
        data = {
            "content_id": content_id,
            "title": title,
            "handler": item.get("contentHandler", ""),
            "modified": item.get("modifiedDate"),
        }
        handler = item.get("contentHandler", "")
        body = _get_item_body(item)
        if body:
            data["body"] = body
        if handler == "resource/x-bb-externallink":
            link_data = detail_item.get("resource/x-bb-externallink", {}) or {}
            data["url"] = link_data.get("url", "")
            data["description"] = link_data.get("description", "")
            data["type_label"] = "🔗 Enlace externo"
        elif handler == "resource/x-bb-asmt-test-link" or handler == "resource/x-bb-asmt-survey-link":
            test_data = detail_item.get(handler, {}) or {}
            data["description"] = test_data.get("description", "")
            data["points"] = test_data.get("pointsPossible", 0)
            data["due_date"] = test_data.get("dueDate", "")
            data["type_label"] = "📝 Prueba" if "test" in handler else "📋 Encuesta"
        elif handler == "resource/x-bb-courselink":
            link_data = detail_item.get("resource/x-bb-courselink", {}) or {}
            data["link_id"] = link_data.get("linkSourceId", "")
            data["target_type"] = link_data.get("targetType", "")
            data["type_label"] = "🔗 Enlace interno"
        elif handler == "resource/x-bb-forumlink":
            data["type_label"] = "💬 Foro"
        elif handler == "resource/x-bb-blti-link":
            data["type_label"] = "🔧 Herramienta externa"
        elif handler == "resource/x-plugin-scormengine":
            data["type_label"] = "📦 Paquete SCORM"
        elif handler in ("resource/x-bb-document", "resource/x-bb-lesson") or (handler and "document" in handler):
            data["type_label"] = "📄 Documento"
        else:
            data["type_label"] = "📄 Otro"
        return data

    tree = build_tree("ROOT", base_path)
    manifest.save()
    return jsonify(tree)


@app.route("/api/courses/<course_id>/announcements")
def get_course_announcements(course_id):
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401
    try:
        return jsonify(client.get_course_announcements(course_id))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/courses/<course_id>/grades")
def get_course_grades(course_id):
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401
    try:
        user_id = state["user"]["id"]
        grades = client.get_gradebook_grades(course_id, user_id)
        final = {}
        try:
            final = client.get_final_grade(course_id)
        except Exception:
            pass
        return jsonify({"grades": grades, "final": final})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/courses/<course_id>/messages")
def get_course_messages(course_id):
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401
    try:
        convs = client.get_conversations(course_id)
        for conv in convs:
            conv_id = conv.get("id", "")
            if conv_id:
                try:
                    conv["messages"] = client.get_conversation_messages(course_id, conv_id)
                except Exception:
                    pass
        return jsonify(convs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/courses/<course_id>/extra")
def get_course_extra(course_id):
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401
    result = {}
    try:
        result["announcements"] = client.get_course_announcements(course_id)
    except Exception as e:
        result["announcements"] = {"error": str(e)}
    try:
        user_id = state["user"]["id"]
        result["grades"] = client.get_gradebook_grades(course_id, user_id)
        try:
            result["final_grade"] = client.get_final_grade(course_id)
        except Exception:
            result["final_grade"] = {}
    except Exception as e:
        result["grades"] = {"error": str(e)}
    try:
        convs = client.get_conversations(course_id)
        for conv in convs:
            conv_id = conv.get("id", "")
            if conv_id:
                try:
                    conv["messages"] = client.get_conversation_messages(course_id, conv_id)
                except Exception:
                    pass
        result["messages"] = convs
    except Exception as e:
        result["messages"] = {"error": str(e)}
    return jsonify(result)


@app.route("/api/courses/<course_id>/recordings")
def get_course_recordings(course_id):
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401
    try:
        collab = CollabClient(client.session, state["xsrf"])
        if not collab.authenticate(course_id):
            return jsonify({"error": "No se pudo autenticar con Collaborate"}), 400
        recordings = collab.get_recordings(course_id)
        return jsonify(recordings)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/courses/missing", methods=["POST"])
def find_missing():
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401
    manifest = Manifest()

    memberships = client.get_courses(state["user"]["id"])
    missing = []
    app.logger.info("Escaneo de pendientes iniciado: %d cursos", len(memberships))

    for course_index, m in enumerate(memberships, start=1):
        c = m.get("course", {})
        course_id = c.get("id", "")
        course_name = c.get("name", "Sin nombre")
        term_name = (c.get("term", {}) or {}).get("name", "Sin período")
        app.logger.info("Escaneando curso %d/%d: %s", course_index, len(memberships), course_name)

        course_base = content_dir(course_dir(term_name, course_name))

        def scan_content(content_id: str, current_base: Path):
            try:
                children = client.get_content_children(course_id, content_id)
            except Exception:
                return
            for item in children:
                handler = item.get("contentHandler", "")
                cid = item.get("id", "")
                title = item.get("title", cid)
                detail = (item.get("contentDetail", {}) or {})
                modified = item.get("modifiedDate")

                if handler in ("resource/x-bb-folder", "resource/x-bb-lesson"):
                    scan_content(cid, current_base / sanitize_filename(title or cid))
                    continue

                for asset in item_assets(item, current_base):
                    asset["modified"] = modified
                    asset_path_on_disk = Path(asset["path"])
                    asset_size = asset.get("size", 0) or 0
                    if not manifest.file_needs_download(
                        course_id,
                        asset["ref"],
                        asset["name"],
                        asset_size,
                        modified,
                        verify_hash=False,
                    ):
                        continue
                    cached_status = manifest.file_status(course_id, asset["ref"])
                    disk_size_matches = (
                        asset_size in (0, asset_path_on_disk.stat().st_size)
                        if asset_path_on_disk.is_file()
                        else False
                    )
                    disk_size_matches = disk_size_matches or (
                        asset_path_on_disk.is_file()
                        and manifest.accepted_size_matches(
                            course_id, asset["ref"], asset_path_on_disk.stat().st_size
                        )
                    )
                    if (
                        asset_path_on_disk.is_file()
                        and disk_size_matches
                        and cached_status not in ("corrupt", "missing", "failed")
                    ):
                        manifest.mark_downloaded(
                            course_id,
                            asset["ref"],
                            asset["name"],
                            asset_size,
                            str(asset_path_on_disk),
                            modified,
                            asset.get("type", "file"),
                            asset.get("url", ""),
                            persist=False,
                        )
                        continue
                    if asset.get("url"):
                        missing.append({
                            "course_id": course_id,
                            "course_name": course_name,
                            "term_name": term_name,
                            "content_id": cid,
                            "handler": handler,
                            "title": title,
                            "file_ref": asset["ref"],
                            "file_name": asset["name"],
                            "file_size": asset.get("size", 0),
                            "file_url": asset["url"],
                            "file_path": str(asset["path"]),
                            "asset_type": asset.get("type", "file"),
                            "mime": asset.get("mime", ""),
                            "modified": modified,
                        })

        scan_content("ROOT", course_base)

    manifest.save()
    app.logger.info("Escaneo de pendientes terminado: %d archivos pendientes", len(missing))
    return jsonify(missing)


# ── Download ──────────────────────────────────────────

@app.route("/api/download", methods=["POST"])
def download():
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401
    if _has_active_task():
        return jsonify({"error": "Espera a que termine el cambio de almacenamiento"}), 409

    data = request.get_json()
    items = (data or {}).get("items", [])
    if not items:
        return jsonify({"error": "No items"}), 400

    global next_task_id
    with _lock:
        task_id = str(next_task_id)
        next_task_id += 1
        q = queue.Queue()
        progress_streams[task_id] = q
        task_progress[task_id] = {"total": len(items), "completed": 0, "status": "running"}

    def run(items_to_process, tid):
        try:
            _do_download(client, items_to_process, tid)
        except Exception as error:
            logger.error("Descarga detenida: %s", error)
            _push_progress(tid, "failed", f"Descarga detenida: {error}")
            with _lock:
                if task_progress.get(tid):
                    task_progress[tid]["status"] = "failed"
        finally:
            with _lock:
                if task_progress.get(tid) and task_progress[tid].get("status") == "running":
                    task_progress[tid]["status"] = "done"

    t = threading.Thread(target=run, args=(items, task_id), daemon=True)
    tasks[task_id] = t
    t.start()

    return jsonify({"task_id": task_id})


def _do_download(client, items, task_id):
    manifest = Manifest()
    total = len(items)
    completed = 0

    for item in items:
        course_id = item["course_id"]
        content_id = item["content_id"]
        title = item.get("title", content_id)
        handler = item.get("handler", "")
        course_name = item.get("course_name", "")
        term_name = item.get("term_name", "")
        file_ref = item.get("file_ref", content_id)
        file_name = item.get("file_name", title)
        file_size = item.get("file_size", 0)
        file_url = item.get("file_url", "")
        modified = item.get("modified")

        base_path = content_dir(course_dir(term_name, course_name))

        if handler in ("resource/x-bb-folder", "resource/x-bb-lesson"):
            try:
                download_base = Path(item.get("file_path")) if item.get("file_path") else base_path
                download_content_tree(
                    client, course_id, content_id, download_base, manifest,
                    callback=lambda ptype, message: _push_progress(task_id, ptype, message),
                )
            except Exception as e:
                _push_progress(task_id, "error", f"Error en {title}: {e}")
        elif item.get("asset_type"):
            asset = {
                "ref": file_ref,
                "name": file_name,
                "size": file_size,
                "url": resolve_url(file_url),
                "path": item.get("file_path") or str(base_path / sanitize_filename(file_name)),
                "type": item.get("asset_type", "embedded"),
                "mime": item.get("mime", ""),
                "modified": modified,
            }
            result = download_asset(
                client.session, manifest, course_id, asset,
                callback=lambda ptype, message: _push_progress(task_id, ptype, message),
            )
        elif handler == "resource/x-bb-file" and file_url:
            asset = {
                "ref": file_ref,
                "name": file_name,
                "size": file_size,
                "url": resolve_url(file_url),
                "path": str(base_path / sanitize_filename(file_name)),
                "type": "file",
                "mime": item.get("mime", ""),
                "modified": modified,
            }
            result = download_asset(
                client.session, manifest, course_id, asset,
                callback=lambda ptype, message: _push_progress(task_id, ptype, message),
            )
        else:
            # Non-file content: save metadata/body/URL
            dest = base_path / sanitize_filename(title or content_id)
            result = _save_metadata_item(item, dest)
            if result:
                _push_progress(task_id, "ok", f"{title} guardado")
            else:
                _push_progress(task_id, "skip", f"{title} ya guardado")

        completed += 1
        _push_progress(task_id, "progress", "", completed=completed, total=total)

    manifest.save()
    _push_progress(task_id, "complete", "Descarga completada", completed=completed, total=total)


@app.route("/api/download/recordings", methods=["POST"])
def download_recordings():
    client = _get_client()
    if not client:
        return jsonify({"error": "Not connected"}), 401
    if _has_active_task():
        return jsonify({"error": "Espera a que termine el cambio de almacenamiento"}), 409

    data = request.get_json()
    items = (data or {}).get("items", [])
    if not items:
        return jsonify({"error": "No items"}), 400

    global next_task_id
    with _lock:
        task_id = str(next_task_id)
        next_task_id += 1
        q = queue.Queue()
        progress_streams[task_id] = q
        task_progress[task_id] = {"total": len(items), "completed": 0, "status": "running"}

    def run(items_to_process, tid):
        try:
            _do_download_recordings(client, items_to_process, tid)
        finally:
            with _lock:
                if task_progress.get(tid):
                    task_progress[tid]["status"] = "done"

    t = threading.Thread(target=run, args=(items, task_id), daemon=True)
    tasks[task_id] = t
    t.start()

    return jsonify({"task_id": task_id})


def _do_download_recordings(client, items, task_id):
    from organizer import course_dir as course_dir_fn
    from organizer import recording_dir
    manifest = Manifest()
    total = len(items)
    completed = 0

    collab_cache: dict[str, CollabClient] = {}

    for item in items:
        course_id = item["course_id"]
        recording_id = item["recording_id"]
        recording_name = item.get("recording_name", recording_id)
        course_name = item.get("course_name", "")
        term_name = item.get("term_name", "")

        if course_id not in collab_cache:
            collab = CollabClient(client.session, state["xsrf"])
            if not collab.authenticate(course_id):
                _push_progress(task_id, "error", f"No se pudo autenticar Collaborate para {course_name}")
                completed += 1
                _push_progress(task_id, "progress", "", completed=completed, total=total)
                continue
            collab_cache[course_id] = collab

        collab = collab_cache[course_id]
        base_path = recording_dir(course_dir_fn(term_name, course_name))
        dest = base_path / f"{sanitize_filename(recording_name)}.mp4"

        _push_progress(task_id, "file", f"Descargando {recording_name}")

        def cb(msg):
            _push_progress(task_id, "file", msg)

        ok = collab.download_recording(recording_id, recording_name, dest, progress_callback=cb)
        if ok:
            manifest.save()
            _push_progress(task_id, "ok", f"{recording_name} descargado")
        else:
            _push_progress(task_id, "error", f"Error descargando {recording_name}")

        completed += 1
        _push_progress(task_id, "progress", "", completed=completed, total=total)

    _push_progress(task_id, "complete", "Descarga de grabaciones completada", completed=completed, total=total)


def _download_tree(client, course_id, content_id, base_path, manifest, depth=0):
    download_content_tree(client, course_id, content_id, base_path, manifest)


def _get_item_data(item: dict, detail_item: dict, content_id: str, title: str) -> dict:
    """Extract metadata from any content item for saving."""
    data = {
        "content_id": content_id,
        "title": title,
        "handler": item.get("contentHandler", ""),
        "modified": item.get("modifiedDate"),
    }
    handler = item.get("contentHandler", "")
    body = ""
    detail_inner = detail_item or {}
    body = detail_inner.get("body", "") or item.get("body", "")
    if isinstance(body, dict):
        body = body.get("text", "") or body.get("html", "")
    if body:
        data["body"] = body
    if handler == "resource/x-bb-externallink":
        link_data = detail_inner.get("resource/x-bb-externallink", {}) or {}
        data["url"] = link_data.get("url", "")
        data["description"] = link_data.get("description", "")
    elif handler == "resource/x-bb-asmt-test-link" or handler == "resource/x-bb-asmt-survey-link":
        test_data = detail_inner.get(handler, {}) or {}
        data["description"] = test_data.get("description", "")
        data["points"] = test_data.get("pointsPossible", 0)
        data["due_date"] = test_data.get("dueDate", "")
    elif handler == "resource/x-bb-courselink":
        link_data = detail_inner.get("resource/x-bb-courselink", {}) or {}
        data["link_id"] = link_data.get("linkSourceId", "")
        data["target_type"] = link_data.get("targetType", "")
    return data


def _save_metadata_item(item: dict, dest_path: Path) -> bool:
    """Save a non-file content item (test, link, document, etc.) to disk.
    Returns True if saved, False if already exists.
    """
    handler = item.get("handler", "")
    title = item.get("title", "unnamed")
    data = item.get("data", {}) or {}
    body = data.get("body", "")
    url = data.get("url", "")
    safe_name = sanitize_filename(title or "unnamed")

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    json_path = dest_path.parent / f"{safe_name}.json"
    if json_path.exists():
        return False

    # Save HTML body if present
    if body:
        html_path = dest_path.parent / f"{safe_name}.html"
        if not html_path.exists():
            html_content = f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><title>{title}</title></head><body>{body}</body></html>"""
            html_path.write_text(html_content, encoding="utf-8")

    # Save URL shortcut if external link
    if url:
        url_path = dest_path.parent / f"{safe_name}.url"
        if not url_path.exists():
            url_path.write_text(f"[InternetShortcut]\nURL={url}\n", encoding="utf-8")

    # Always save metadata JSON
    from organizer import save_json as _save_json
    _save_json(json_path, data)
    return True


def _push_progress(task_id: str, ptype: str, msg: str, completed: int = 0, total: int = 0):
    with _lock:
        q = progress_streams.get(task_id)
        if q:
            q.put({"type": ptype, "message": msg, "completed": completed, "total": total})
        if task_progress.get(task_id):
            task_progress[task_id]["last"] = msg


@app.route("/api/progress/<task_id>")
def progress_stream(task_id):
    def generate():
        q = progress_streams.get(task_id)
        if not q:
            yield f"data: {json.dumps({'type': 'error', 'message': 'unknown task'})}\n\n"
            return

        q.put({"type": "start", "message": "Iniciando..."})
        while True:
            try:
                data = q.get(timeout=30)
                yield f"data: {json.dumps(data)}\n\n"
                if data.get("type") in ("complete", "failed"):
                    break
            except queue.Empty:
                t = tasks.get(task_id)
                if t and not t.is_alive():
                    break
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.route("/api/manifest")
def get_manifest():
    return jsonify(Manifest().data)


@app.route("/api/manifest/audit", methods=["POST"])
def audit_manifest():
    result = Manifest().audit()
    logger.info("Auditoría: %d verificados, %d corruptos, %d faltantes", result["verified"], result["corrupt"], result["missing"])
    for cf in result.get("corrupt_files", []):
        logger.info("  → %s: %s", cf["name"], cf["reason"])
    return jsonify(result)


@app.route("/api/manifest/repair", methods=["POST"])
def repair_corrupt():
    manifest = Manifest()
    audit = manifest.audit()
    removed = 0
    for course_id, course in manifest.data.get("courses", {}).items():
        to_remove = []
        for ref, entry in course.get("files", {}).items():
            if entry.get("status") in ("corrupt", "missing"):
                path = manifest._resolve_path(entry.get("path", ""))
                if path and path.exists():
                    try:
                        path.unlink()
                        logger.info("Reparación: eliminado %s", path)
                    except OSError as exc:
                        logger.warning("No se pudo eliminar %s: %s", path, exc)
                to_remove.append(ref)
        for ref in to_remove:
            del course["files"][ref]
            removed += 1
    manifest.save()
    logger.info("Reparación: %d archivos corruptos eliminados del manifiesto", removed)
    return jsonify({"removed": removed, "audit": audit})


@app.route("/api/stats")
def get_stats():
    path = storage.current_root()
    if not path.exists():
        return jsonify({"total_size": 0, "total_files": 0, "courses_count": 0})
    total_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    total_files = sum(1 for f in path.rglob("*") if f.is_file())
    courses_count = sum(1 for f in path.rglob("course.json"))
    manifest = Manifest().data
    entries = [entry for course in manifest.get("courses", {}).values() for entry in course.get("files", {}).values()]
    verified = sum(1 for entry in entries if entry.get("status") == "verified")
    corrupt = sum(1 for entry in entries if entry.get("status") in ("corrupt", "failed"))
    pending = sum(1 for entry in entries if entry.get("status") in ("missing", "pending") or not entry.get("sha256"))
    return jsonify({
        "total_size": total_size,
        "total_size_fmt": _fmt_size(total_size),
        "total_files": total_files,
        "courses_count": courses_count,
        "manifest_checked": len(entries),
        "verified_files": verified,
        "pending_files": pending,
        "corrupt_files": corrupt,
        "last_audit": manifest.get("last_audit"),
        "last_run": manifest.get("last_run"),
    })


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


# ── Update checker ─────────────────────────────────────

def _check_update():
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        tag = data.get("tag_name", "").lstrip("v").strip()
        if not tag:
            return None
        current_parts = tuple(int(x) for x in APP_VERSION.split(".") if x.isdigit())
        latest_parts = tuple(int(x) for x in tag.split(".") if x.isdigit())
        if latest_parts > current_parts:
            return {
                "current": APP_VERSION,
                "latest": tag,
                "url": data.get("html_url", ""),
                "published": data.get("published_at", ""),
            }
    except Exception:
        pass
    return None


@app.route("/api/update")
def check_update():
    result = _check_update()
    if result:
        return jsonify({"update_available": True, **result})
    return jsonify({"update_available": False, "current": APP_VERSION})


# ── Main page ─────────────────────────────────────────

@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    import webbrowser
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(debug=False, port=5000, threaded=True)
