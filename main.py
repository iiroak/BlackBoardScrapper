import sys
import time
from pathlib import Path

from config import API_BASE, BASE_URL
from auth import login_flow, validate_session
from bb_client import BBClient
from collab_client import CollabClient
from downloader import download_file, sanitize_filename
from manifest import Manifest
from organizer import course_dir, content_dir, save_json
from content_sync import download_asset, item_assets
import storage


def process_content_tree(
    client: BBClient,
    course_id: str,
    content_id: str,
    base_path: Path,
    manifest: Manifest,
    depth: int = 0,
):
    indent = "  " * depth
    children = client.get_content_children(course_id, content_id)
    for item in children:
        cid = item.get("id", "")
        title = item.get("title", cid)
        handler = item.get("contentHandler", "")
        detail = item.get("contentDetail", {}) or {}
        modified = item.get("modifiedDate")

        safe_title = sanitize_filename(title) or cid

        if handler in ("resource/x-bb-folder", "resource/x-bb-lesson"):
            folder_path = base_path / safe_title
            folder_path.mkdir(parents=True, exist_ok=True)
            print(f"{indent}📁 {title}/")
            save_json(folder_path / "_metadata.json", item)
            for asset in item_assets(item, base_path):
                asset["modified"] = modified
                download_asset(
                    client.session, manifest, course_id, asset,
                    callback=lambda kind, message: print(f"{indent}  {message}"),
                )
            process_content_tree(client, course_id, cid, folder_path, manifest, depth + 1)

        elif handler == "resource/x-bb-file":
            assets = item_assets(item, base_path)
            if not assets:
                print(f"{indent}⚠ {title} (sin URL)")
                continue
            for asset in assets:
                asset["modified"] = modified
                print(f"{indent}⬇ {asset['name']} ({_fmt_size(asset.get('size', 0))})")
                download_asset(
                    client.session, manifest, course_id, asset,
                    callback=lambda kind, message: print(f"{indent}  {message}"),
                )
        else:
            print(f"{indent}❓ {title} ({handler})")
            save_json(base_path / f"{safe_title}.json", item)
            for asset in item_assets(item, base_path):
                asset["modified"] = modified
                download_asset(
                    client.session, manifest, course_id, asset,
                    callback=lambda kind, message: print(f"{indent}  {message}"),
                )


def _guess_filename(name: str, mime: str) -> str:
    ext_map = {
        "application/pdf": ".pdf",
        "application/x-7z-compressed": ".7z",
        "application/zip": ".zip",
        "application/x-zip-compressed": ".zip",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "text/plain": ".txt",
        "text/html": ".html",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "video/mp4": ".mp4",
    }
    ext = ext_map.get(mime, "")
    if ext and not name.endswith(ext):
        return name + ext
    return name


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _fmt_duration(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


def main():
    print()
    print("=" * 60)
    print("  BLACKBOARD BACKUP SCRAPER - v1.0")
    print("  Universidad Mayor - Campus Virtual")
    print("=" * 60)
    print()

    session, xsrf = login_flow()
    if not session:
        print("✗ No se pudo autenticar. Saliendo.")
        sys.exit(1)

    client = BBClient(session, xsrf)
    manifest = Manifest()
    storage.current_root().mkdir(parents=True, exist_ok=True)

    print("\n🔍 Obteniendo perfil del usuario...")
    user = client.get_user()
    user_id = user.get("id", "")
    user_name = f"{user.get('givenName', '')} {user.get('familyName', '')}".strip()
    print(f"  Usuario: {user_name} ({user.get('userName', '')})")
    print(f"  Email: {user.get('emailAddress', '')}")
    save_json(storage.current_root() / "_meta" / "user.json", user)

    print("\n📚 Obteniendo lista de cursos...")
    memberships = client.get_courses(user_id)
    print(f"  Cursos encontrados: {len(memberships)}")

    stats = {
        "total": len(memberships),
        "processed": 0,
        "files_downloaded": 0,
        "files_skipped": 0,
        "errors": [],
    }

    for idx, membership in enumerate(memberships, 1):
        course = membership.get("course", {})
        course_id = course.get("id", "")
        course_name = course.get("name", "Sin nombre")
        term_info = course.get("term", {}) or {}
        term_name = term_info.get("name", "Sin período")

        print(f"\n{'='*60}")
        print(f"  [{idx}/{stats['total']}] {course_name}")
        print(f"  ID: {course_id} | Período: {term_name}")
        print(f"{'='*60}")

        try:
            course_detail = client.get_course(course_id)
        except Exception as e:
            print(f"  ✗ Error obteniendo detalles: {e}")
            stats["errors"].append(f"{course_id}: {e}")
            continue

        base = course_dir(term_name, course_name)
        base.mkdir(parents=True, exist_ok=True)

        save_json(base / "course.json", course_detail)
        manifest.set_sync_metadata(
            course_id,
            name=course_name,
            term=term_name,
            last_sync=time.time(),
        )

        content_path = content_dir(base)

        print(f"\n  📄 Procesando contenido...")
        try:
            children = client.get_content_children(course_id, "ROOT")
            if children:
                process_content_tree(
                    client, course_id, "ROOT", content_path, manifest
                )
            else:
                print("    (sin contenido)")
        except Exception as e:
            print(f"  ✗ Error en contenido: {e}")
            stats["errors"].append(f"{course_id}/content: {e}")

        print(f"\n  📢 Procesando anuncios...")
        try:
            announcements = client.get_course_announcements(course_id)
            if announcements:
                save_json(base / "announcements.json", announcements)
                print(f"    {len(announcements)} anuncios guardados")
            else:
                print("    (sin anuncios)")
        except Exception as e:
            print(f"  ✗ Error en anuncios: {e}")
            stats["errors"].append(f"{course_id}/announcements: {e}")

        print(f"  💬 Procesando mensajes...")
        try:
            conversations = client.get_conversations(course_id)
            if conversations:
                msg_dir = base / "messages"
                msg_dir.mkdir(parents=True, exist_ok=True)
                for conv in conversations:
                    conv_id = conv.get("id", "")
                    if conv_id:
                        try:
                            msgs = client.get_conversation_messages(course_id, conv_id)
                            conv["messages"] = msgs
                        except Exception:
                            pass
                    save_json(msg_dir / f"{conv_id}.json", conv)
                print(f"    {len(conversations)} conversaciones guardadas")
            else:
                print("    (sin mensajes)")
        except Exception as e:
            print(f"  ✗ Error en mensajes: {e}")
            stats["errors"].append(f"{course_id}/messages: {e}")

        print(f"  📊 Procesando notas...")
        try:
            grades = client.get_gradebook_grades(course_id, user_id)
            grade_data = {"grades": grades}
            try:
                grade_data["final"] = client.get_final_grade(course_id)
            except Exception:
                pass
            save_json(base / "grades.json", grade_data)
            print(f"    {len(grades)} notas encontradas")
        except Exception as e:
            print(f"  ✗ Error en notas: {e}")
            stats["errors"].append(f"{course_id}/grades: {e}")

        print(f"  🎥 Procesando grabaciones Collaborate...")
        try:
            collab = CollabClient(session, xsrf)
            if collab.authenticate(course_id):
                recordings = collab.get_recordings(course_id)
                if recordings:
                    rec_dir = base / "recordings"
                    rec_dir.mkdir(parents=True, exist_ok=True)
                    rec_meta = []
                    for rec in recordings:
                        rec_id = rec.get("id", "")
                        rec_name = rec.get("name", rec_id) or rec_id
                        duration = rec.get("duration", 0)
                        session_name = rec.get("sessionName", "")
                        rec_info = {
                            "id": rec_id,
                            "name": rec_name,
                            "session": session_name,
                            "duration": duration,
                            "duration_fmt": _fmt_duration(duration / 1000) if duration else "",
                            "created": rec.get("created"),
                            "modified": rec.get("modified"),
                            "storageSize": rec.get("storageSize"),
                        }
                        rec_meta.append(rec_info)

                        dest = rec_dir / f"{sanitize_filename(rec_name)}.mp4"
                        if dest.exists():
                            print(f"    ✓ {rec_name} (ya descargado)")
                            continue

                        url = collab.get_recording_url(rec_id)
                        if url:
                            print(f"    ⬇ {rec_name} ({_fmt_size(rec.get('storageSize', 0))})")
                            ok = download_file(
                                collab.collab_session, url, dest,
                                description=rec_name,
                            )
                            if ok:
                                print(f"      ✓ Descargado")
                            else:
                                print(f"      ✗ Error")
                        else:
                            print(f"    ⚠ {rec_name} (no se pudo obtener URL)")

                    save_json(rec_dir / "recordings.json", rec_meta)
                    print(f"    Total: {len(recordings)} grabaciones")
                else:
                    print("    (sin grabaciones disponibles)")
            else:
                print(f"    ⚠ No se pudo autenticar en Collaborate")
        except Exception as e:
            print(f"  ✗ Error en Collaborate: {e}")
            stats["errors"].append(f"{course_id}/collab: {e}")

        stats["processed"] += 1
        manifest.set_sync_metadata(course_id, last_sync=time.time())
        print(f"\n  ✅ {course_name} - Completado")

    manifest.save()

    print(f"\n{'='*60}")
    print(f"  RESUMEN")
    print(f"{'='*60}")
    print(f"  Cursos procesados: {stats['processed']}/{stats['total']}")
    if stats["errors"]:
        print(f"  Errores: {len(stats['errors'])}")
        for err in stats["errors"][:5]:
            print(f"    - {err}")
    print(f"\n  Backup en: {storage.current_root().absolute()}")
    print(f"  Manifest: {storage.manifest_path()}")
    print()


if __name__ == "__main__":
    main()
