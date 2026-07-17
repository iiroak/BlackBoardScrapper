import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import manifest as manifest_module
import storage as storage_module
from content_sync import download_asset, extract_embedded_attachments
from downloader import download_file, get_file_hash
from manifest import Manifest


class FakeResponse:
    def __init__(self, body, status=200, content_type="application/octet-stream"):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.body = body

    def iter_content(self, chunk_size=8192):
        yield self.body

    def close(self):
        pass


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


class CoreTests(unittest.TestCase):
    def test_extracts_and_deduplicates_embedded_attachment(self):
        item = {
            "contentDetail": {
                "body": {
                    "rawText": (
                        '<a href="/bbcswebdav/pid-1/xid-2" '
                        'data-bbtype="attachment" '
                        'data-bbfile="{&quot;fileName&quot;:&quot;guia.pdf&quot;,'
                        '&quot;fileSize&quot;:12,&quot;mimeType&quot;:&quot;application/pdf&quot;}">Guia</a>'
                    ),
                    "displayText": (
                        '<a href="/bbcswebdav/pid-1/xid-2" '
                        'data-bbtype="attachment" data-bbfile="{}">Guia</a>'
                    ),
                }
            }
        }
        attachments = extract_embedded_attachments(item)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["name"], "guia.pdf")
        self.assertEqual(attachments[0]["size"], 12)

    def test_download_is_atomic_and_does_not_replace_on_bad_size(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "file.bin"
            destination.write_bytes(b"original")
            session = FakeSession(FakeResponse(b"short"))
            self.assertFalse(download_file(session, "https://example.test/file", destination, 99))
            self.assertEqual(destination.read_bytes(), b"original")
            self.assertFalse((destination.parent / ".file.bin.part").exists())

    def test_manifest_migrates_windows_path_and_records_sha256(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "backup"
            actual = output / "Term" / "course" / "file.pdf"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"content")
            manifest_path = output / "manifest.json"
            manifest_path.write_text(json.dumps({
                "version": 1,
                "courses": {"course": {"files": {"ref": {
                    "name": "file.pdf",
                    "size": 7,
                    "path": "C:\\Users\\old\\BlackBoardScraper\\backup\\Term\\course\\file.pdf",
                }}}},
            }))
            with patch.object(storage_module, "current_root", return_value=output), patch.object(
                storage_module, "manifest_path", return_value=manifest_path
            ):
                result = Manifest().audit()
                data = json.loads(manifest_path.read_text())

            self.assertEqual(result["verified"], 1)
            entry = data["courses"]["course"]["files"]["ref"]
            self.assertEqual(entry["path"], "Term/course/file.pdf")
            self.assertEqual(entry["sha256"], get_file_hash(actual))

    def test_manifest_reconcile_registers_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "backup"
            actual = output / "Term" / "course" / "guide.pdf"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"guide")
            manifest_path = output / "manifest.json"
            with patch.object(storage_module, "current_root", return_value=output), patch.object(
                storage_module, "manifest_path", return_value=manifest_path
            ):
                manifest = Manifest()
                missing = manifest.reconcile("course", [{
                    "ref": "remote-ref",
                    "name": "guide.pdf",
                    "size": 5,
                    "path": str(actual),
                    "type": "file",
                }])

            self.assertEqual(missing, [])
            self.assertEqual(manifest.data["courses"]["course"]["files"]["remote-ref"]["status"], "verified")

    def test_manifest_accepts_remote_file_size_as_string(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "backup"
            actual = output / "Term" / "course" / "guide.pdf"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"guide")
            manifest_path = output / "manifest.json"
            with patch.object(storage_module, "current_root", return_value=output), patch.object(
                storage_module, "manifest_path", return_value=manifest_path
            ):
                manifest = Manifest()
                manifest.mark_downloaded("course", "remote-ref", "guide.pdf", 5, str(actual))
                self.assertFalse(manifest.file_needs_download("course", "remote-ref", "guide.pdf", "5", None))

    def test_manifest_corrupt_status_forces_redownload(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "backup"
            actual = output / "Term" / "course" / "guide.pdf"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"guide")
            manifest_path = output / "manifest.json"
            with patch.object(storage_module, "current_root", return_value=output), patch.object(
                storage_module, "manifest_path", return_value=manifest_path
            ):
                manifest = Manifest()
                manifest.mark_downloaded("course", "remote-ref", "guide.pdf", 5, str(actual))
                manifest.data["courses"]["course"]["files"]["remote-ref"]["status"] = "corrupt"

                self.assertTrue(manifest.file_needs_download("course", "remote-ref", "guide.pdf", 5, None))

    def test_manifest_accepts_mime_valid_size_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "backup"
            actual = output / "Term" / "course" / "guide.pdf"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"guide")
            manifest_path = output / "manifest.json"
            with patch.object(storage_module, "current_root", return_value=output), patch.object(
                storage_module, "manifest_path", return_value=manifest_path
            ):
                manifest = Manifest()
                manifest.mark_downloaded("course", "remote-ref", "guide.pdf", 99, str(actual))
                result = manifest.audit()

                self.assertEqual(result["corrupt"], 0)
                self.assertEqual(result["verified"], 1)
                self.assertTrue(manifest.accepted_size_matches("course", "remote-ref", 5))

    def test_manifest_can_defer_persistence_during_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "backup"
            actual = output / "Term" / "course" / "guide.pdf"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"guide")
            manifest_path = output / "manifest.json"
            with patch.object(storage_module, "current_root", return_value=output), patch.object(
                storage_module, "manifest_path", return_value=manifest_path
            ):
                manifest = Manifest()
                with patch.object(manifest, "save") as save:
                    manifest.mark_downloaded("course", "remote-ref", "guide.pdf", 5, str(actual), persist=False)
                save.assert_not_called()

    def test_download_restarts_stale_partial_and_accepts_matching_mime(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "report.pdf"
            partial = destination.with_name(".report.pdf.part")
            partial.write_bytes(b"stale-partial")

            class SequenceSession:
                def __init__(self):
                    self.responses = [
                        FakeResponse(b"", status=416),
                        FakeResponse(b"%PDF-valid", content_type="application/pdf"),
                    ]

                def get(self, *args, **kwargs):
                    return self.responses.pop(0)

            self.assertTrue(
                download_file(
                    SequenceSession(),
                    "https://example.test/report.pdf",
                    destination,
                    expected_size=5,
                    expected_mime="application/pdf",
                )
            )
            self.assertEqual(destination.read_bytes(), b"%PDF-valid")

    def test_download_does_not_skip_when_manifest_path_differs(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "new" / "guide.pdf"

            class ExistingManifest:
                def file_needs_download(self, *args):
                    return False

                def mark_downloaded(self, *args):
                    self.marked_path = Path(args[4])

            manifest = ExistingManifest()
            result = download_asset(
                FakeSession(FakeResponse(b"guide")),
                manifest,
                "course",
                {"ref": "remote-ref", "name": "guide.pdf", "size": 5, "url": "https://example.test/guide", "path": str(destination)},
            )

            self.assertEqual(result, "downloaded")
            self.assertEqual(manifest.marked_path, destination)

    def test_download_replaces_corrupt_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "guide.pdf"
            destination.write_bytes(b"wrong")

            class CorruptManifest:
                def file_needs_download(self, *args):
                    return True

                def file_status(self, *args):
                    return "corrupt"

                def mark_downloaded(self, *args):
                    self.marked_path = Path(args[4])

            manifest = CorruptManifest()
            result = download_asset(
                FakeSession(FakeResponse(b"guide")),
                manifest,
                "course",
                {"ref": "remote-ref", "name": "guide.pdf", "size": 5, "url": "https://example.test/guide", "path": str(destination)},
            )

            self.assertEqual(result, "downloaded")
            self.assertEqual(destination.read_bytes(), b"guide")

    def test_storage_files_excludes_transient_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "backup"
            (output / "course").mkdir(parents=True)
            (output / "course" / "course.json").write_text("{}")
            (output / "course" / ".hidden").write_text("hidden")
            (output / "course" / "partial.part").write_text("partial")
            (output / "manifest.json").write_text("{}")
            files = storage_module.files(output)
            self.assertEqual(files, [output / "manifest.json", output / "course" / ".hidden", output / "course" / "course.json"])

    def test_storage_migration_moves_backup_without_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "backup"
            destination = root / "OneDrive" / "Campus Archive"
            (output / "course").mkdir(parents=True)
            (output / "course" / "course.json").write_text("{}")
            (output / "manifest.json").write_text("{}")
            manifest_path = output / "manifest.json"
            with patch.object(storage_module, "current_root", return_value=output), patch.object(
                storage_module, "save_selection"
            ) as save_selection:
                summary = storage_module.migrate_to(destination, "onedrive")

            self.assertEqual(summary["moved"], 2)
            self.assertFalse(output.exists())
            self.assertTrue((destination / "course" / "course.json").exists())
            self.assertTrue((destination / "manifest.json").exists())
            save_selection.assert_called_once_with(destination, "onedrive")


if __name__ == "__main__":
    unittest.main()
