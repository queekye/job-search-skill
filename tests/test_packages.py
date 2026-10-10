"""Real filesystem/ZIP tests; fixtures contain no applicant data."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from service.app import AppError
from service import packages


ROOT = Path(__file__).resolve().parents[1]


class FakeApp:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.root_dir = ROOT

    def execute(self, op):
        if op == "state":
            return {"settings": {}}
        raise AssertionError(op)


def altered(files, version="0.2.0", extra=None):
    files = dict(files)
    manifest = json.loads(files["manifest.json"])
    manifest["version"] = version
    if extra:
        files.update(extra)
    manifest["files"] = [{"path": name, "sha256": hashlib.sha256(data).hexdigest()}
                         for name, data in sorted(files.items()) if name != "manifest.json"]
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode()
    return files


def archive(files, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for name, content in files.items():
            target.writestr(name, content)
        if extra:
            target.writestr(*extra)
    return output.getvalue()


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-pack-tests-")
        self.app = FakeApp(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, operation, args=None, actor="user"):
        return packages.execute(self.app, operation, args or {}, actor)

    def import_zip(self, raw):
        return self.call("resource.import", {"filename": "contribution.zip", "content_base64": base64.b64encode(raw).decode()})

    def test_bundled_offline_install_read_and_deterministic_public_export(self):
        listed = self.call("resource.list")
        self.assertEqual(len(listed["items"]), 15)
        self.assertFalse(listed["registry_configured"])
        self.assertEqual(sum(i["executable"] for i in listed["items"]), 10)
        (self.app.data_dir / "private.txt").write_text("DO_NOT_EXPORT_PRIVATE_DATA")
        installed = self.call("resource.install", {"id": "bytedance-campus"}, "codex")
        self.assertTrue(installed["installed"])
        value = self.call("resource.read", {"id": "bytedance-campus"})
        self.assertIn("字节", value["content"])
        self.assertTrue(Path(value["installed_path"]).is_dir())
        first = self.call("resource.export", {"id": "bytedance-campus"})
        second = self.call("resource.export", {"id": "bytedance-campus"})
        self.assertEqual(first, second)
        raw = base64.b64decode(first["content_base64"])
        contents = packages._unzip(raw)
        self.assertNotIn("private.txt", contents)
        self.assertNotIn(b"DO_NOT_EXPORT_PRIVATE_DATA", b"".join(contents.values()))

    def test_review_immutable_versions_and_receipt_bound_rollback(self):
        bundled = self.call("resource.install", {"id": "oppo"})
        files = altered(packages._bundled(self.app, "oppo"))
        review = self.import_zip(archive(files))
        self.assertFalse(review["installed"])
        self.assertEqual(review["trust"], "pending_user_review")
        with self.assertRaises(AppError) as caught:
            self.call("resource.approve", {"candidate_id": review["candidate_id"]}, "codex")
        self.assertEqual(caught.exception.code, "user_review_required")
        enabled = self.call("resource.approve", {"candidate_id": review["candidate_id"]})
        self.assertEqual(enabled["version"], "0.2.0")
        self.assertEqual(enabled["trust"], "user_reviewed")
        older = self.call("resource.rollback", {"id": "oppo", "version": bundled["version"]})
        self.assertEqual(older["trust"], "bundled")
        newer = self.call("resource.rollback", {"id": "oppo", "version": "0.2.0"})
        self.assertEqual(newer["trust"], "user_reviewed")
        review2 = self.import_zip(archive(altered(files, extra={"guide.md": b"Different content"})))
        with self.assertRaises(AppError) as caught:
            self.call("resource.approve", {"candidate_id": review2["candidate_id"]})
        self.assertEqual(caught.exception.code, "immutable_version_conflict")

    def test_import_never_executes_script(self):
        files = altered(packages._bundled(self.app, "oppo"), extra={"adapter.mjs": b'throw new Error("must never execute");'})
        review = self.import_zip(archive(files))
        self.assertTrue(review["executable"])
        accepted = self.call("resource.approve", {"candidate_id": review["candidate_id"]})
        self.assertTrue(accepted["installed"])

    def test_review_exposes_complete_script_not_truncated_preview(self):
        source = b"\n".join(b"// " + hashlib.sha256(str(i).encode()).hexdigest().encode() for i in range(300))
        files = altered(packages._bundled(self.app, "oppo"), extra={"adapter.mjs": source})
        review = self.import_zip(archive(files))
        scripts = {item["path"]: item for item in review["review_files"]}
        self.assertGreater(len(files["adapter.mjs"]), 16000)
        self.assertEqual(scripts["adapter.mjs"]["content"], files["adapter.mjs"].decode("utf-8"))
        self.assertFalse(scripts["adapter.mjs"]["truncated"])

    def test_public_bundled_export_ignores_user_imported_version(self):
        bundled_files = packages._bundled(self.app, "oppo")
        bundled_version = json.loads(bundled_files["manifest.json"])["version"]
        changed = altered(bundled_files, extra={"guide.md": b"PRIVATE_USER_LOCAL_DRAFT"})
        review = self.import_zip(archive(changed))
        self.call("resource.approve", {"candidate_id": review["candidate_id"]})
        result = self.call("resource.export", {"id": "oppo", "source": "bundled"})
        files = packages._unzip(base64.b64decode(result["content_base64"]))
        self.assertNotIn(b"PRIVATE_USER_LOCAL_DRAFT", b"".join(files.values()))
        self.assertEqual(json.loads(files["manifest.json"])["version"], bundled_version)

    def test_traversal_duplicate_case_absolute_and_special_members_rejected(self):
        files = packages._bundled(self.app, "oppo")
        for name in ("../outside.md", "/outside.md", "C:/outside.md", "helpers\\outside.mjs", "guide.md",
                     "GUIDE.md", "nul.md", "helpers/con.txt", "outside.py", "helpers/./test.md", "helpers/test.. /x.md"):
            with self.subTest(name=name), self.assertRaises(AppError):
                self.import_zip(archive(files, (name, b"unsafe")))
        info = zipfile.ZipInfo("helpers/link.md")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(AppError):
            self.import_zip(archive(files, (info, b"/outside")))
        info = zipfile.ZipInfo("helpers/reparse.md")
        info.external_attr = 0x400
        with self.assertRaises(AppError):
            self.import_zip(archive(files, (info, b"target")))
        self.assertFalse((self.app.data_dir.parent / "outside.md").exists())

    def test_hash_tampering_and_undeclared_members_blocked(self):
        files = packages._bundled(self.app, "oppo")
        files["guide.md"] = b"tampered"
        with self.assertRaises(AppError) as caught:
            self.import_zip(archive(files))
        self.assertEqual(caught.exception.code, "package_hash_mismatch")
        with self.assertRaises(AppError):
            self.import_zip(archive(packages._bundled(self.app, "oppo"), ("extra.md", b"unlisted")))
        installed = self.call("resource.install", {"id": "oppo"})
        (Path(installed["installed_path"]) / "guide.md").write_text("modified")
        with self.assertRaises(AppError):
            self.call("resource.read", {"id": "oppo"})

    def test_review_archive_changed_before_approval_is_blocked(self):
        review = self.import_zip(archive(packages._bundled(self.app, "oppo")))
        path = self.app.data_dir / "package-review" / (review["candidate_id"] + ".zip")
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaises(AppError) as caught:
            self.call("resource.approve", {"candidate_id": review["candidate_id"]})
        self.assertEqual(caught.exception.code, "package_hash_mismatch")

    def test_expansion_ratio_and_size_limits(self):
        files = altered(packages._bundled(self.app, "oppo"), extra={"large.md": b"a" * 200000})
        with self.assertRaises(AppError):
            self.import_zip(archive(files))
        with self.assertRaises(AppError):
            self.import_zip(b"x" * (packages.MAX_ARCHIVE + 1))
        files = altered(packages._bundled(self.app, "oppo"), extra={"large.md": b"a" * (packages.MAX_FILE + 1)})
        with self.assertRaises(AppError):
            packages.validate_files(files)

    def test_json_duplicate_keys_and_unsafe_filename_rejected(self):
        files = packages._bundled(self.app, "oppo")
        files["manifest.json"] = files["manifest.json"].replace(b'"id": "oppo"', b'"id": "oppo", "id": "other"')
        with self.assertRaises(AppError):
            self.import_zip(archive(files))
        with self.assertRaises(AppError):
            self.call("resource.import", {"filename": "../../outside.zip", "content_base64": "aGVsbG8="})

    def test_storage_link_refused(self):
        outside = self.app.data_dir / "outside"
        outside.mkdir()
        link = self.app.data_dir / "packages"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation not permitted on this host")
        with self.assertRaises(AppError) as caught:
            self.call("resource.install", {"id": "oppo"})
        self.assertEqual(caught.exception.code, "unsafe_package_path")
        self.assertEqual(list(outside.iterdir()), [])

    def test_mutation_contention_is_explicit(self):
        with packages._mutation(self.app):
            with self.assertRaises(AppError) as caught:
                self.call("resource.install", {"id": "oppo"})
        self.assertEqual(caught.exception.code, "resource_busy")

    def test_remote_registry_support_does_not_fabricate_an_unconfigured_index(self):
        self.assertTrue(self.call("resource.list")["remote_registry_supported"])
        self.assertEqual({"configured": False, "url": None, "items": []}, self.call("resource.refresh"))


if __name__ == "__main__":
    unittest.main()
