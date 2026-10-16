import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_checker", ROOT / "scripts" / "check_release.py")
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="career-release-tests-")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
        return name

    def scan(self, names, forbidden=()):
        return CHECKER.check_release(self.root, names, forbidden, require_files=False)

    def codes(self, result):
        return {item["code"] for item in result["issues"]}

    def test_only_explicit_allowlist_is_read(self):
        self.put("public.md", "A public company guide with synthetic@example.invalid.")
        self.put("credentials.json", '{"private":true}')
        result = self.scan(["public.md"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["files_checked"], 1)
        self.assertIn("private_resource", self.codes(self.scan(["credentials.json"])))

    def test_common_os_username_does_not_block_public_product_or_test_words(self):
        self.put("tests/public.md", "Public career test guidance.")
        for username in ("test", "career"):
            with self.subTest(username=username), patch("getpass.getuser", return_value=username):
                self.assertTrue(self.scan(["tests/public.md"])["ok"])
        self.assertIn("private_identifier", self.codes(self.scan(["tests/public.md"], ["career"])))

    def test_runtime_allowlist_excludes_source_development_material(self):
        from scripts.build_release import public_paths
        paths = public_paths()
        self.assertIn("scripts/verify_release.py", paths)
        self.assertIn("scripts/browser-session.mjs", paths)
        self.assertNotIn("AGENTS.md", paths)
        self.assertNotIn("Check.cmd", paths)
        self.assertNotIn("scripts/build_release.py", paths)
        self.assertNotIn("references/development-contract.md", paths)
        self.assertNotIn("references/validation.md", paths)
        self.assertFalse(any(name.startswith(("tests/", ".github/")) for name in paths))

    def test_public_source_scan_includes_development_files_but_not_unknown_directories(self):
        from scripts.check_source import SOURCE_FILES, source_paths
        self.put("README.md", "Public overview")
        for name in SOURCE_FILES:
            self.put(name, "Public source material")
        self.put("tests/test_example.py", "Public synthetic test")
        self.put("tests/fixture.test.mjs", "Public synthetic fixture")
        self.put("tests/unknown/nested.py", "An unknown subtree is not selected")
        self.put("private-data/profile.json", '{"private":true}')
        with patch("scripts.check_source.public_paths", return_value=["README.md"]):
            paths = source_paths(self.root)
        self.assertIn("AGENTS.md", paths)
        self.assertIn(".github/workflows/check.yml", paths)
        self.assertIn("tests/test_example.py", paths)
        self.assertIn("tests/fixture.test.mjs", paths)
        self.assertNotIn("tests/unknown/nested.py", paths)
        self.assertNotIn("private-data/profile.json", paths)
        self.assertTrue(self.scan(paths)["ok"])
        marker = "SYNTHETIC" + "_PRIVATE_MARKER"
        self.put("tests/test_example.py", marker)
        result = self.scan(paths, [marker])
        self.assertIn("private_identifier", self.codes(result))
        self.assertNotIn(marker, json.dumps(result))

    def test_each_standalone_company_zip_carries_public_license_notice(self):
        from types import SimpleNamespace
        from service.packages import _bundled, _unzip, _zip
        expected = (ROOT / "LICENSE").read_bytes()
        for path in sorted((ROOT / "companies").glob("*/manifest.json")):
            with self.subTest(company=path.parent.name):
                files = _bundled(SimpleNamespace(root_dir=ROOT), path.parent.name)
                members = _unzip(_zip(files))
                self.assertEqual(members["LICENSE.txt"], expected)

    def test_release_zip_bytes_match_across_platform_defaults_without_member_changes(self):
        from scripts.build_release import zip_bytes
        files = {"web/中文指引.md": "公开合成说明".encode("utf-8"),
                 "empty.txt": b"", "runtime/data.bin": bytes(range(256))}
        original_info = zipfile.ZipInfo
        archives = []
        for platform in (0, 3):
            class PlatformZipInfo(original_info):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.create_system = platform

            with patch.object(zipfile, "ZipInfo", PlatformZipInfo):
                archives.append(zip_bytes(files))
        self.assertEqual(archives[0], archives[1])
        for raw in archives:
            with zipfile.ZipFile(io.BytesIO(raw)) as packed:
                self.assertEqual(packed.namelist(), sorted(files))
                self.assertEqual({name: packed.read(name) for name in packed.namelist()}, files)
                self.assertTrue(all(info.create_system == 3 for info in packed.infolist()))

    def make_install_snapshot(self):
        from scripts.build_release import installation_files
        from scripts.verify_release import REQUIRED
        files = {name: b"public" for name in REQUIRED if name != ".agents/plugins/marketplace.json"}
        runtime = installation_files(files, "0.1.2")
        for name, raw in runtime.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return runtime

    def test_installation_snapshot_is_complete_and_marketplace_uses_its_root(self):
        from scripts.verify_release import verify
        runtime = self.make_install_snapshot()
        self.assertTrue(verify(self.root)["ok"])
        marketplace = json.loads(runtime[".agents/plugins/marketplace.json"])
        self.assertEqual(marketplace["plugins"][0]["source"]["path"], ".")
        (self.root / "service/app.py").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_installation_snapshot_rejects_extra_files_and_escape_paths(self):
        from scripts.verify_release import MANIFEST, verify
        self.make_install_snapshot()
        extra = self.root / "extra.py"
        extra.write_text("unexpected", encoding="utf-8")
        with self.assertRaises(ValueError):
            verify(self.root)
        extra.unlink()
        manifest_path = self.root / MANIFEST
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][0]["path"] = "../outside.py"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            verify(self.root)

    def install_phone_hash(self):
        from scripts.build_release import installation_files
        # Find an actual digest with a phone-shaped run; no private data fixture
        # or fabricated hash is needed for the generated-manifest regression.
        for number in range(10000):
            raw = f"public synthetic content {number}".encode()
            if CHECKER.PHONE.search(hashlib.sha256(raw).hexdigest()):
                break
        else:
            self.fail("Synthetic hash fixture was not found")
        runtime = installation_files({"public.txt": raw}, "0.2.1")
        for name, content in runtime.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return runtime

    def test_verified_install_manifest_sha_is_exempt_only_from_shape_checks(self):
        runtime = self.install_phone_hash()
        self.assertTrue(self.scan(sorted(runtime))["ok"])
        manifest = json.loads(runtime["INSTALL-MANIFEST.json"])
        digest = next(entry["sha256"] for entry in manifest["files"] if entry["path"] == "public.txt")
        phone = CHECKER.PHONE.search(digest).group()
        self.assertIn("private_identifier", self.codes(self.scan(sorted(runtime), [phone])))
        self.put("ordinary.json", runtime["INSTALL-MANIFEST.json"].decode())
        self.assertIn("personal_phone", self.codes(self.scan(["ordinary.json"])))
        self.assertIn("personal_phone", self.codes(self.scan(["INSTALL-MANIFEST.json"])))

    def test_install_manifest_bad_hash_or_structure_does_not_hide_phone(self):
        runtime = self.install_phone_hash()
        original = json.loads(runtime["INSTALL-MANIFEST.json"])
        for malformed in (False, True):
            with self.subTest(malformed=malformed):
                manifest = json.loads(runtime["INSTALL-MANIFEST.json"])
                entry = next(item for item in manifest["files"] if item["path"] == "public.txt")
                if malformed:
                    entry["sha256"] += "g"
                else:
                    entry["sha256"] = ("a" if entry["sha256"][0] != "a" else "b") + entry["sha256"][1:]
                self.put("INSTALL-MANIFEST.json", json.dumps(manifest))
                self.assertIn("personal_phone", self.codes(self.scan(sorted(runtime))))
        phone = "139" + "01234567"
        original["phone"] = phone
        self.put("INSTALL-MANIFEST.json", json.dumps(original))
        self.assertIn("personal_phone", self.codes(self.scan(sorted(runtime))))

    def test_private_content_and_secrets_still_block_valid_install_snapshot(self):
        from scripts.build_release import installation_files
        phone = "139" + "01234567"
        secret = "sk-" + "A" * 30
        runtime = installation_files({"public.txt": (phone + " " + secret).encode()}, "0.2.1")
        for name, content in runtime.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        self.assertTrue({"personal_phone", "credential_value"} <= self.codes(self.scan(sorted(runtime))))

    def test_manifest_hash_verification_never_reads_denied_private_members(self):
        from scripts.build_release import installation_files
        runtime = installation_files({"credentials.json": b"synthetic private state"}, "0.2.1")
        for name, content in runtime.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        private_path = self.root / "credentials.json"
        original_open = Path.open
        def public_only(path, *args, **kwargs):
            if path == private_path:
                self.fail("The scanner opened a denied private member")
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", public_only):
            self.assertIn("private_resource", self.codes(self.scan(sorted(runtime))))

    def test_installation_snapshot_rejects_links(self):
        from scripts.verify_release import verify
        self.make_install_snapshot()
        source = self.root / "service/app.py"
        target = self.root / "extra.py"
        try:
            target.symlink_to(source)
        except OSError:
            self.skipTest("Host cannot create symlinks")
        with self.assertRaises(ValueError):
            verify(self.root)

    def test_installation_snapshot_does_not_reject_a_user_selected_outer_junction(self):
        from types import SimpleNamespace
        import stat
        from scripts.verify_release import verify
        self.make_install_snapshot()
        original_lstat = Path.lstat
        chosen_root = self.root.resolve()
        outer_parent = chosen_root.parent
        def with_outer_junction(path):
            if path == outer_parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original_lstat(path)
        with patch.object(Path, "lstat", with_outer_junction):
            self.assertTrue(verify(chosen_root)["ok"])

    def test_installation_snapshot_rejects_an_internal_junction(self):
        from types import SimpleNamespace
        import stat
        from scripts.verify_release import verify
        self.make_install_snapshot()
        original_lstat = Path.lstat
        internal = self.root.resolve() / "service"
        def with_internal_junction(path):
            if path == internal:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original_lstat(path)
        with patch.object(Path, "lstat", with_internal_junction), self.assertRaises(ValueError):
            verify(self.root)

    def test_private_state_binary_and_unsupported_files_denied(self):
        for name in ("career.db", "state.sqlite3", ".env", "__pycache__/cache.pyc", "keys.pem", "profile.json"):
            with self.subTest(name=name):
                self.put(name, "test")
                self.assertFalse(self.scan([name])["ok"])
        self.put("data.txt", "binary\x00payload")
        self.assertIn("binary_content", self.codes(self.scan(["data.txt"])))

    def test_personal_values_and_paths_do_not_appear_in_report(self):
        name = "SYNTHETIC" + "_PRIVATE_PERSON"
        phone = "139" + "01234567"
        path = "C:/" + "Users/" + "release-test-user/private.txt"
        self.put("leak.md", name + " " + phone + " " + path)
        result = self.scan(["leak.md"], [name])
        self.assertTrue({"private_identifier", "personal_phone", "absolute_user_path"} <= self.codes(result))
        serialized = json.dumps(result)
        self.assertNotIn(name, serialized)
        self.assertNotIn(phone, serialized)
        self.assertNotIn(path, serialized)

    def test_credentials_and_nonexample_email_require_review(self):
        credential = "sk-" + "A" * 30
        email = "person" + "@private-mail.testdomain.org"
        self.put("leak.md", credential + " " + email)
        result = self.scan(["leak.md"])
        self.assertTrue({"credential_value", "email_requires_review"} <= self.codes(result))
        self.assertNotIn(credential, json.dumps(result))
        self.assertNotIn(email, json.dumps(result))

    def test_profile_export_and_nonfinite_json_rejected(self):
        self.put("export.json", json.dumps({"records": [{"kind": "basic", "data": {"name": "Synthetic person"}}]}))
        self.assertIn("private_export", self.codes(self.scan(["export.json"])))
        self.put("broken.json", "{not json}")
        self.assertIn("invalid_json", self.codes(self.scan(["broken.json"])))
        self.put("nonfinite.json", '{"value": NaN}')
        self.assertIn("invalid_json", self.codes(self.scan(["nonfinite.json"])))

    def test_synthetic_url_userinfo_is_not_misclassified_as_personal_email(self):
        self.put("guard.test.mjs", 'const badOrigin = "https://user' + '@join.qq.com";')
        self.assertTrue(self.scan(["guard.test.mjs"])["ok"])

    def test_private_filename_is_reported_without_echoing_identifier(self):
        identifier = "SYNTHETIC" + "_PRIVATE_PERSON"
        name = identifier + ".md"
        self.put(name, "Public contents cannot make a private filename safe.")
        result = self.scan([name], [identifier])
        self.assertIn("private_filename", self.codes(result))
        self.assertNotIn(identifier, json.dumps(result))

    def test_required_entrypoints_are_checked(self):
        self.put("public.md", "public")
        result = CHECKER.check_release(self.root, ["public.md"])
        self.assertIn("required_file_missing", self.codes(result))
        for name in CHECKER.REQUIRED:
            self.put(name, "{}" if name.endswith(".json") else "public")
        self.assertTrue(CHECKER.check_release(self.root, sorted(CHECKER.REQUIRED))["ok"])

    def test_release_validates_company_plan_binding_and_evidence(self):
        from scripts.build_release import MAX_PLAN_BYTES, validate_extension_plans
        from scripts.validate_site_extension import template
        plan = template()
        manifest = {'id': plan['company_pack']['id'], 'origin': plan['origin']}
        prefix = 'companies/' + manifest['id'] + '/'
        files = {prefix + 'manifest.json': json.dumps(manifest).encode(),
                 prefix + 'references/site-extension.json': json.dumps(plan).encode()}
        self.assertEqual(validate_extension_plans(files), 1)
        moved = {name.replace(prefix, 'companies/different-company/'): raw for name, raw in files.items()}
        with self.assertRaises(ValueError):
            validate_extension_plans(moved)
        oversized = dict(files)
        oversized[prefix + 'references/site-extension.json'] += b' ' * MAX_PLAN_BYTES
        with self.assertRaises(ValueError):
            validate_extension_plans(oversized)
        plan['origin'] = 'https://another.example.com'
        files[prefix + 'references/site-extension.json'] = json.dumps(plan).encode()
        with self.assertRaises(ValueError):
            validate_extension_plans(files)
        plan['origin'] = manifest['origin']
        plan['verification']['live_read']['status'] = 'passed'
        files[prefix + 'references/site-extension.json'] = json.dumps(plan).encode()
        with self.assertRaises(ValueError):
            validate_extension_plans(files)

    def test_bundled_company_plans_are_valid(self):
        from scripts.build_release import public_paths, validate_extension_plans
        files = {name: (ROOT / name).read_bytes() for name in public_paths()}
        declared = [name for name in files if name.startswith('companies/') and name.endswith('site-extension.json')]
        self.assertEqual(validate_extension_plans(files), len(declared))

    def test_traversal_duplicates_and_links_denied(self):
        self.put("public.md", "public")
        self.assertIn("unsafe_path", self.codes(self.scan(["../outside.md"])))
        self.assertIn("duplicate_path", self.codes(self.scan(["public.md", "PUBLIC.md"])))
        link = self.root / "link.md"
        try:
            link.symlink_to(self.root / "public.md")
        except OSError:
            self.skipTest("Host cannot create symlinks")
        self.assertIn("linked_path", self.codes(self.scan(["link.md"])))


if __name__ == "__main__":
    unittest.main()
