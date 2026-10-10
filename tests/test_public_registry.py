"""Public static downloads use only local synthetic HTTP and temporary storage."""
import copy
from contextlib import ExitStack
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock, patch
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


class PublicTransportTests(unittest.TestCase):
    URL = "https://registry.example.test/index.json"

    def network(self, stack, addresses, *, certificate_error=None):
        answers = [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
                    socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443)) for ip in addresses]
        raw_socket, tls_socket = Mock(), Mock()
        response = Mock(status=200)
        response.getheader.return_value = "2"
        response.read.return_value = b"{}"
        connection = Mock()
        connection.getresponse.return_value = response
        # Keep the actual default verification policy; only the network handshake is mocked.
        context = ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(ssl.CERT_REQUIRED, context.verify_mode)
        resolve = stack.enter_context(patch("service.packages.socket.getaddrinfo", return_value=answers))
        connect = stack.enter_context(patch("service.packages.socket.create_connection", return_value=raw_socket))
        https = stack.enter_context(patch("service.packages.http.client.HTTPSConnection", return_value=connection))
        default_context = stack.enter_context(patch("service.packages.ssl.create_default_context", return_value=context))
        wrap = stack.enter_context(patch.object(context, "wrap_socket", return_value=tls_socket,
                                               side_effect=certificate_error))
        return resolve, connect, https, default_context, wrap, connection, raw_socket, tls_socket

    def test_domain_fakeip_pins_numeric_address_and_verifies_original_tls_hostname(self):
        for addresses in (("198.18.0.102",), ("198.19.255.254",), ("198.18.0.102", "8.8.8.8")):
            with self.subTest(addresses=addresses), ExitStack() as stack:
                resolve, connect, https, default_context, wrap, connection, raw, tls = self.network(stack, addresses)
                self.assertEqual(b"{}", packages._fetch_public(self.URL, 1024))
                resolve.assert_called_once_with("registry.example.test", 443, type=socket.SOCK_STREAM)
                connect.assert_called_once_with((addresses[0], 443), timeout=10)
                https.assert_called_once_with("registry.example.test", 443, timeout=10)
                default_context.assert_called_once_with()
                wrap.assert_called_once_with(raw, server_hostname="registry.example.test")
                self.assertIs(connection.sock, tls)
                connection.request.assert_called_once_with("GET", "/index.json", headers={
                    "Accept-Encoding": "identity", "User-Agent": "Career-Public-Resources/1"})
                connection.close.assert_called_once_with()

    def test_fakeip_certificate_failure_sends_no_http_request(self):
        with ExitStack() as stack:
            _, connect, _, default_context, wrap, connection, raw, _ = self.network(
                stack, ("198.18.0.102",), certificate_error=ssl.SSLCertVerificationError("synthetic mismatch"))
            with self.assertRaises(AppError) as caught:
                packages._fetch_public(self.URL, 1024)
            self.assertEqual("registry_unavailable", caught.exception.code)
            connect.assert_called_once_with(("198.18.0.102", 443), timeout=10)
            default_context.assert_called_once_with()
            wrap.assert_called_once_with(raw, server_hostname="registry.example.test")
            connection.request.assert_not_called()
            connection.getresponse.assert_not_called()
            connection.close.assert_called_once_with()

    def test_literal_fakeip_private_ipv4_ipv6_and_mixed_dns_are_rejected_before_connect(self):
        cases = [
            ("https://198.18.0.102/index.json", ("198.18.0.102",)),
            ("https://198.19.255.254/index.json", ("198.19.255.254",)),
            ("http://registry.example.test/index.json", ("198.18.0.102",)),
            *((self.URL, (ip,)) for ip in ("10.0.0.1", "127.0.0.1", "fc00::1", "::1", "fe80::1", "::ffff:198.18.0.102")),
            (self.URL, ("198.18.0.102", "10.0.0.1")),
            (self.URL, ("8.8.8.8", "127.0.0.1")),
            (self.URL, ("198.18.0.102", "fc00::1")),
            (self.URL, ("10.0.0.1", "198.18.0.102")),
        ]
        for url, addresses in cases:
            with self.subTest(url=url, addresses=addresses), ExitStack() as stack:
                _, connect, https, default_context, wrap, connection, _, _ = self.network(stack, addresses)
                with self.assertRaises(AppError) as caught:
                    packages._fetch_public(url, 1024)
                self.assertEqual("invalid_registry_url", caught.exception.code)
                connect.assert_not_called()
                https.assert_not_called()
                default_context.assert_not_called()
                wrap.assert_not_called()
                connection.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
