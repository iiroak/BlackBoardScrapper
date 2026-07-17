import hashlib
import html
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from config import BASE_URL
from downloader import download_file, sanitize_filename
from organizer import save_json

logger = logging.getLogger("campus-archive")


class _AttachmentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.attachments = []
        self._in_attachment = False
        self._text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() != "a":
            return
        if attrs.get("data-bbtype", "").lower() != "attachment":
            return
        self._in_attachment = True
        self._text = []
        self.attachments.append({
            "href": attrs.get("href", ""),
            "metadata": attrs.get("data-bbfile", ""),
        })

    def handle_data(self, data):
        if self._in_attachment:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._in_attachment:
            self.attachments[-1]["text"] = "".join(self._text).strip()
            self._in_attachment = False


def resolve_url(url: str) -> str:
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL.rstrip("/") + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return BASE_URL.rstrip("/") + "/" + url.lstrip("/")


def _stable_url(url: str) -> str:
    """Remove temporary signed query parameters from an asset identity."""
    parts = urlsplit(resolve_url(url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _metadata(value) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        return json.loads(html.unescape(value))
    except (TypeError, ValueError):
        return {}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def extract_embedded_attachments(item: dict) -> list[dict]:
    """Extract Blackboard HTML attachments from any content item."""
    found = {}
    for value in _strings(item):
        if "data-bbtype" not in value or "attachment" not in value.lower():
            continue
        parser = _AttachmentParser()
        try:
            parser.feed(value)
        except Exception:
            continue
        for raw in parser.attachments:
            meta = _metadata(raw.get("metadata"))
            source = raw.get("href") or meta.get("resourceUrl") or meta.get("viewerUrl")
            source = resolve_url(source)
            if not source:
                continue
            identity = _stable_url(source)
            name = (
                meta.get("fileName")
                or meta.get("displayName")
                or meta.get("linkName")
                or raw.get("text")
                or Path(urlsplit(source).path).name
                or "attachment"
            )
            name = html.unescape(str(name)).strip() or "attachment"
            candidate = {
                "ref": "embedded:" + hashlib.sha256(identity.encode()).hexdigest(),
                "name": name,
                "size": int(meta.get("fileSize") or 0),
                "mime": meta.get("mimeType", ""),
                "url": source,
                "identity": identity,
                "type": "embedded",
            }
            if identity not in found:
                found[identity] = candidate
            else:
                # rawText normally has richer metadata than displayText.
                for key in ("name", "size", "mime"):
                    if not found[identity].get(key) and candidate.get(key):
                        found[identity][key] = candidate[key]
    return list(found.values())


def direct_file(item: dict) -> dict | None:
    if item.get("contentHandler") != "resource/x-bb-file":
        return None
    detail = item.get("contentDetail", {}) or {}
    fd = (detail.get("resource/x-bb-file", {}) or {}).get("file", {}) or {}
    url = resolve_url(fd.get("permanentUrl", ""))
    if not url:
        return None
    if fd.get("forceDownload") and "xythos-download=" not in url:
        url += "&" if "?" in url else "?"
        url += "xythos-download=true"
    return {
        "ref": fd.get("existingFileReference") or item.get("id", ""),
        "name": fd.get("fileName") or item.get("title", "unnamed"),
        "size": int(fd.get("fileSize") or 0),
        "mime": fd.get("mimeType", ""),
        "url": url,
        "type": "file",
    }


def asset_path(base_path: Path, item_title: str, name: str, embedded: bool) -> Path:
    if not embedded:
        return base_path / sanitize_filename(name)
    return (
        base_path
        / sanitize_filename(item_title or "attachments")
        / "attachments"
        / sanitize_filename(name)
    )


def item_assets(item: dict, base_path: Path) -> list[dict]:
    assets = []
    direct = direct_file(item)
    if direct:
        direct["path"] = asset_path(base_path, item.get("title", ""), direct["name"], False)
        assets.append(direct)
    title = item.get("title", item.get("id", "unnamed"))
    for attachment in extract_embedded_attachments(item):
        attachment["path"] = asset_path(base_path, title, attachment["name"], True)
        assets.append(attachment)
    return assets


def _decorate_asset(asset: dict, manifest, course_id: str, modified=None) -> dict:
    asset = dict(asset)
    asset["modified"] = modified
    asset["downloaded"] = manifest.is_downloaded(
        course_id, asset["ref"], asset.get("size", 0), modified
    )
    asset["path"] = str(asset["path"])
    return asset


def build_content_tree(client, course_id: str, manifest, base_path: Path, content_id="ROOT") -> list[dict]:
    nodes = []
    children = client.get_content_children(course_id, content_id)
    for item in children:
        cid = item.get("id", "")
        title = item.get("title", cid)
        handler = item.get("contentHandler", "")
        is_container = handler in ("resource/x-bb-folder", "resource/x-bb-lesson")
        is_file = handler == "resource/x-bb-file"
        node = {
            "id": cid,
            "title": title,
            "handler": handler,
            "type": "folder" if is_container else "file" if is_file else "other",
            "modified": item.get("modifiedDate"),
            "children": [],
            "attachments": [],
        }
        current_base = base_path
        if is_container:
            current_base = base_path / sanitize_filename(title or cid)
            node["children"] = build_content_tree(client, course_id, manifest, current_base, cid)
        else:
            direct = direct_file(item)
            if direct:
                direct["path"] = asset_path(base_path, title, direct["name"], False)
                node["file"] = _decorate_asset(direct, manifest, course_id, item.get("modifiedDate"))
        for attachment in extract_embedded_attachments(item):
            attachment["path"] = asset_path(base_path, title, attachment["name"], True)
            node["attachments"].append(
                _decorate_asset(attachment, manifest, course_id, item.get("modifiedDate"))
            )
        if not node["attachments"]:
            node.pop("attachments")
        nodes.append(node)
    return nodes


def download_asset(session, manifest, course_id: str, asset: dict, callback=None) -> str:
    path = Path(asset["path"])
    size = asset.get("size", 0)
    modified = asset.get("modified")
    needs_download = manifest.file_needs_download(
        course_id, asset["ref"], asset["name"], size, modified
    )
    if path.is_file() and not needs_download:
        manifest.mark_downloaded(
            course_id, asset["ref"], asset["name"], size, str(path), modified,
            asset.get("type", "file"), asset.get("url", ""),
        )
        if callback:
            callback("skip", f"{asset['name']} ya verificado")
        return "skipped"

    if path.is_file() and getattr(manifest, "file_status", lambda *_: None)(course_id, asset["ref"]) in (
        "corrupt", "failed"
    ):
        path.unlink()

    if callback:
        callback("file", f"Descargando {asset['name']}")
    ok = download_file(
        session,
        asset["url"],
        path,
        size,
        asset.get("name", ""),
        expected_mime=asset.get("mime"),
        on_error=(lambda message: callback("error", f"Error descargando {asset['name']}: {message}")) if callback else None,
    )
    if not ok:
        logger.error("Descarga fallida: %s (curso=%s, ref=%s, size=%s)", asset["name"], course_id, asset["ref"], size)
        return "failed"
    logger.info("Descargado: %s (%s bytes)", asset["name"], size)
    manifest.mark_downloaded(
        course_id, asset["ref"], asset["name"], size, str(path), modified,
        asset.get("type", "file"), asset.get("url", ""),
    )
    if callback:
        callback("ok", f"{asset['name']} descargado")
    return "downloaded"


def download_content_tree(client, course_id: str, content_id: str, base_path: Path, manifest, callback=None):
    for item in client.get_content_children(course_id, content_id):
        title = item.get("title", item.get("id", "unnamed"))
        handler = item.get("contentHandler", "")
        current_base = base_path
        if handler in ("resource/x-bb-folder", "resource/x-bb-lesson"):
            current_base = base_path / sanitize_filename(title or item.get("id", "unnamed"))
            current_base.mkdir(parents=True, exist_ok=True)
            save_json(current_base / "_metadata.json", item)
            download_content_tree(client, course_id, item.get("id", ""), current_base, manifest, callback)
        else:
            if handler != "resource/x-bb-file":
                save_json(base_path / f"{sanitize_filename(title or item.get('id', 'unnamed'))}.json", item)
            for asset in item_assets(item, current_base):
                asset["modified"] = item.get("modifiedDate")
                download_asset(client.session, manifest, course_id, asset, callback)
