"""Public static downloads use only local synthetic HTTP and temporary storage."""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import zipfile

from scripts.build_catalog import catalog_files
from service import packages
from service.app import AppError

ROOT = Path(__file__).resolve().parents[1]


class PublicRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-public-registry-")
        self.app = type("PublicApp", (), {"root_dir": ROOT, "data_dir": Path(self.tmp.name)})()
        self.app.execute = lambda operation: {"settings": {"registry_url": self.url}} if operation == "state" else None
        self.raw = packages._zip(packages._bundled(self.app, "oppo"))
        manifest = json.loads(packages._bundled(self.app, "oppo")["manifest.json"])
        self.item = {key: manifest[key] for key in ("id", "name", "version", "description", "origin", "capabilities", "verification")}
        self.item["download"] = {"path": f"packages/{manifest['id']}-{manifest['version']}.zip",
                                 "sha256": hashlib.sha256(self.raw).hexdigest(), "bytes": len(self.raw)}
        self.index = {"schema_version": 1, "packages": [self.item]}
        self.requests, self.responses = [], {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.requests.append((self.path, dict(self.headers)))
                default = packages._json(owner.index) if self.path == "/index.json" else owner.raw
                code, body, headers = owner.responses.get(self.path, (200, default, {}))
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (ConnectionResetError, BrokenPipeError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/index.json"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def call(self, operation, args=None, actor="user"):
        return packages.execute(self.app, operation, args or {}, actor)

    def download(self, **changes):
        args = {"id": self.item["id"], "version": self.item["version"], "sha256": self.item["download"]["sha256"]}
        return self.call("resource.download", args | changes)

    def error(self, code, action):
        with self.assertRaises(AppError) as caught:
            action()
        self.assertEqual(code, caught.exception.code)

    def test_static_site_has_pinned_versioned_archives_and_is_deterministic(self):
        first = catalog_files()
        self.assertEqual(first, catalog_files())
        index = json.loads(first["index.json"])
        self.assertEqual(len(index["packages"]), 15)
        self.assertEqual(first[".nojekyll"], b"")
        self.assertIn(b"pack.download?.path", first["catalog.js"])
        for item in index["packages"]:
            download = item["download"]
            raw = first[download["path"]]
            self.assertEqual(download["bytes"], len(raw))
            self.assertEqual(download["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertLess(len(raw), packages.MAX_ARCHIVE)
            manifest = json.loads(packages._unzip(raw)["manifest.json"])
            self.assertEqual((item["id"], item["version"]), (manifest["id"], manifest["version"]))
        self.assertEqual(len(first), 21)

    def test_refresh_is_read_only_and_an_unconfigured_index_is_explicit(self):
        result = self.call("resource.refresh", actor="codex")
        self.assertTrue(result["configured"])
        self.assertEqual(self.item, result["items"][0])
        self.assertEqual([], list(self.app.data_dir.iterdir()))
        self.assertEqual({"configured": False, "url": None, "items": []}, self.call("resource.refresh", {"url": ""}))
        self.assertEqual(1, len(self.requests))

    def test_static_cli_outputs_only_public_files_without_opening_private_storage(self):
        base = Path(self.tmp.name)
        private = base / 'synthetic-private'
        private.mkdir()
        marker = b'SYNTHETIC_PRIVATE_DATABASE_MUST_NOT_BE_READ'
        (private / 'career.db').write_bytes(marker)
        output = base / 'dist' / 'catalog'
        command = [sys.executable, '-B', '-X', 'utf8', str(ROOT / 'scripts/build_catalog.py'), '--output', str(output)]
        env = os.environ | {'JOB_SEARCH_DATA_DIR': str(private)}
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(0, result.returncode, result.stderr)
        actual = {path.relative_to(output).as_posix(): path.read_bytes() for path in output.rglob('*') if path.is_file()}
        self.assertEqual(catalog_files(), actual)
        self.assertEqual(21, len(actual))
        self.assertTrue(all(marker not in value for value in actual.values()))
        self.assertEqual(marker, (private / 'career.db').read_bytes())
        (output / 'synthetic-extra.txt').write_bytes(marker)
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('new or empty directory', result.stderr)
        self.assertEqual(marker, (output / 'synthetic-extra.txt').read_bytes())

    def test_download_enters_existing_complete_review_without_enabling_or_execution(self):
        result = self.download()
        self.assertTrue(result["review_required"])
        self.assertEqual("pending_user_review", result["trust"])
        self.assertEqual(self.url, result["registry_url"])
        self.assertTrue(all(not item["truncated"] for item in result["review_files"]))
        candidate = self.app.data_dir / "package-review" / (result["candidate_id"] + ".zip")
        self.assertEqual(self.raw, candidate.read_bytes())
        self.assertFalse((self.app.data_dir / "packages").exists())
        self.assertFalse((self.app.data_dir / "packages.lock.json").exists())
        self.error("user_review_required", lambda: self.call("resource.approve", {"candidate_id": result["candidate_id"]}, actor="codex"))
        installed = self.call("resource.approve", {"candidate_id": result["candidate_id"]})
        self.assertEqual("user_reviewed", installed["trust"])
        self.assertEqual("community_import", installed["source"])
        for _, headers in self.requests:
            self.assertFalse({"Authorization", "Cookie"} & set(headers))

    def test_codex_cannot_download_and_rejects_before_any_network_request(self):
        self.error("user_review_required", lambda: self.call("resource.download", {"id": "oppo"}, actor="codex"))
        self.assertEqual([], self.requests)

    def test_index_changes_do_not_silently_download_a_new_hash(self):
        self.call("resource.refresh")
        previous = self.item["download"]["sha256"]
        self.item["download"]["sha256"] = "0" * 64
        self.error("registry_version_changed", lambda: self.download(sha256=previous))
        self.assertEqual(["/index.json", "/index.json"], [path for path, _ in self.requests])
        self.assertEqual([], list(self.app.data_dir.iterdir()))

    def test_download_size_and_digest_are_checked_before_review_storage(self):
        self.responses["/" + self.item["download"]["path"]] = (200, self.raw + b"changed", {})
        self.error("package_hash_mismatch", self.download)
        self.assertEqual([], list(self.app.data_dir.iterdir()))

    def test_zip_identity_cannot_differ_from_the_public_index(self):
        self.item["id"] = "synthetic-company"
        self.item["download"]["path"] = f"packages/synthetic-company-{self.item['version']}.zip"
        self.error("package_hash_mismatch", self.download)
        self.assertEqual([], list(self.app.data_dir.iterdir()))

    def test_paths_sizes_duplicates_and_schemas_are_bounded(self):
        original = copy.deepcopy(self.index)
        for change in (lambda index: index["packages"][0]["download"].update(path="../outside.zip"),
                       lambda index: index["packages"][0]["download"].update(path="https://other.example.test/oppo.zip"),
                       lambda index: index["packages"][0]["download"].update(bytes=packages.MAX_ARCHIVE + 1),
                       lambda index: index["packages"].append(copy.deepcopy(index["packages"][0])),
                       lambda index: index.update(schema_version=2)):
            self.index = copy.deepcopy(original)
            change(self.index)
            self.error("invalid_registry", lambda: self.call("resource.refresh"))
        self.assertEqual([], list(self.app.data_dir.iterdir()))

    def test_both_index_and_zip_redirects_are_rejected_without_following(self):
        for target in ("/index.json", "/" + self.item["download"]["path"]):
            self.responses = {target: (302, b"", {"Location": "http://127.0.0.1:1/private"})}
            self.requests.clear()
            self.error("registry_redirect_rejected", self.download)
            self.assertTrue(all(path != "/private" for path, _ in self.requests))
        self.assertEqual([], list(self.app.data_dir.iterdir()))

    def test_credentials_queries_schemes_and_private_dns_are_rejected(self):
        for url in ("file:///private", "http://192.168.1.1/index.json", "https://user:password@example.test/index.json",
                    "https://example.test/index.json?token=secret", "https://example.test/index.json#secret"):
            self.error("invalid_registry_url", lambda: self.call("resource.refresh", {"url": url}))
        with patch("service.packages.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]), \
             patch("service.packages.socket.create_connection") as connect:
            self.error("invalid_registry_url", lambda: self.call("resource.refresh", {"url": "https://example.test/index.json"}))
            connect.assert_not_called()
        self.assertEqual([], self.requests)

    def test_index_limit_and_duplicate_json_keys_are_rejected(self):
        for raw, code in ((b"x" * (packages.MAX_INDEX + 1), "registry_size_limit"),
                          (b'{"schema_version":1,"schema_version":1,"packages":[]}', "invalid_package")):
            self.responses["/index.json"] = (200, raw, {})
            self.error(code, lambda: self.call("resource.refresh"))

    def test_malformed_remote_capabilities_and_broken_deflate_are_fixed_errors(self):
        for value in ([], {}):
            self.item["capabilities"] = {"applications": {"status": value, "detail": "synthetic"}}
            self.error("invalid_package", lambda: self.call("resource.refresh"))
        self.item["capabilities"] = {}
        raw = bytearray(self.raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            member = archive.getinfo("guide.md")
        start = member.header_offset + 30 + len(member.filename.encode("utf-8")) + len(member.extra)
        raw[start] |= 7  # Deflate block type 3 is invalid; archive headers remain intact.
        self.raw = bytes(raw)
        self.item["download"].update(sha256=hashlib.sha256(self.raw).hexdigest(), bytes=len(self.raw))
        self.error("invalid_package", self.download)
        self.assertEqual([], list(self.app.data_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
