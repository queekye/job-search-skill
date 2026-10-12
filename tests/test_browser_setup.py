"""Synthetic package archives verify outside-source installation and member bounds."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from service import browser_setup


def archive(members):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, raw in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return output.getvalue()


class BrowserSetupTests(unittest.TestCase):
    def test_install_is_pinned_verified_external_and_idempotent(self):
        blobs = {name: archive({"package/package.json": json.dumps({"name": name, "version": browser_setup.VERSION}).encode(),
                               "package/index.mjs": b"// synthetic dependency"}) for name in ("playwright-core", "playwright")}
        calls = []
        def fetch(url, limit):
            calls.append(url)
            name = "playwright-core" if "/playwright-core/" in url else "playwright"
            blob = blobs[name]
            if url.endswith(".tgz"):
                return blob
            return json.dumps({"name": name, "version": browser_setup.VERSION, "dist": {
                "tarball": f"https://registry.npmjs.org/{name}/-/{name}-{browser_setup.VERSION}.tgz",
                "integrity": "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()}}).encode()
        with tempfile.TemporaryDirectory(prefix="career-browser-setup-") as temp:
            with patch.dict(os.environ, {"LOCALAPPDATA": temp}), patch.object(browser_setup, "_fetch", side_effect=fetch):
                result = browser_setup.setup()
                self.assertTrue(Path(result["module_path"]).is_file())
                self.assertTrue(Path(result["module_path"]).is_relative_to(Path(temp)))
                self.assertEqual(browser_setup.setup(), result)
                self.assertEqual(len(calls), 4)

    def test_rejects_traversal_and_does_not_write_outside_staging(self):
        with tempfile.TemporaryDirectory(prefix="career-browser-members-") as temp:
            target = Path(temp) / "stage"
            for name in ("package/../../escaped.js", "/package/x.js", "package/C:/x.js", "other/x.js"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    browser_setup._unpack(archive({name: b"synthetic"}), target)
            self.assertFalse((Path(temp) / "escaped.js").exists())

    def test_rejects_linked_members(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as tar:
            info = tarfile.TarInfo("package/link.js")
            info.type = tarfile.SYMTYPE
            info.linkname = "outside.js"
            tar.addfile(info)
        with tempfile.TemporaryDirectory(prefix="career-browser-links-") as temp:
            with self.assertRaises(ValueError):
                browser_setup._unpack(output.getvalue(), Path(temp) / "stage")


if __name__ == "__main__":
    unittest.main()
