"""Check only an explicit public release allowlist; never enumerate private data.

Checks are a release gate, not a claim that automated scanning proves privacy.
The packager owns the allowlist and passes exactly the files it will publish.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

REQUIRED = {
    ".codex-plugin/plugin.json", "skills/career/SKILL.md", "scripts/run.py",
    "scripts/start.ps1", "service/app.py", "service/http.py", "service/mcp.py",
    "service/otp.py", "service/packages.py", "web/index.html", "web/app.js",
    "web/styles.css", "README.md", "LICENSE", "references/company-pack.md",
    "references/otp.md", "schemas/company-pack.schema.json",
    "Install.cmd", "Start.cmd", ".mcp.json", "scripts/install.ps1", "scripts/verify_release.py",
}
ALLOWED_SUFFIXES = {".py", ".ps1", ".cmd", ".mjs", ".js", ".json", ".md", ".html", ".css", ".txt", ".toml", ".yml", ".yaml"}
ALLOWED_EXTENSIONLESS = {"LICENSE", "NOTICE", ".gitignore", ".gitattributes"}
PRIVATE_COMPONENTS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".job-search-skill", "package-review", "package-receipts"}
PRIVATE_NAMES = {"career.db", "packages.lock.json", "otp.json", "otp-state.json", "sync.json", "sync-state.json", "credentials.json", "secrets.json", "profile.json", "applications.json", ".env", "config.toml"}
MAX_FILE = 2 * 1024 * 1024
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@([\w.-]+\.[A-Za-z]{2,})(?![\w.-])")
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDENTITY = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
WINDOWS_HOME = re.compile(r"(?i)\b[A-Z]:[\\/]+Users[\\/]+([A-Za-z0-9_.-]+)[\\/]")
POSIX_HOME = re.compile(r"(?<![\w])/(?:home|Users)/([A-Za-z0-9_.-]+)/")
KNOWN_SECRET = re.compile(r"(?:\bsk-[A-Za-z0-9_-]{24,}|\bgh[pousr]_[A-Za-z0-9]{24,}|\bglpat-[A-Za-z0-9_-]{20,}|\bAKIA[A-Z0-9]{16}\b)")
POPULATED_SECRET = re.compile(r'''(?ix)["'](?:api[_-]?key|access[_-]?token|client[_-]?secret|password|private[_-]?key)["']\s*:\s*["']([^"'\s]{12,})["']''')
INSTALL_MANIFEST = "INSTALL-MANIFEST.json"
SHA_VALUE = re.compile(r'("sha256"\s*:\s*")([0-9a-f]{64})(")')


def _issue(issues, path, code, message):
    # Never include the matched value in an issue or exception.
    issues.append({"path": path, "code": code, "message": message})


def _example(value):
    value = value.casefold()
    return value.endswith((".invalid", ".test", ".example")) or value in {"example.com", "example.org", "example.net"}


def _links(path):
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return True
    return False


def _install_shape_text(root, name, text, allowlisted):
    """Exclude genuine generated hashes only from phone/identity heuristics."""
    if name != INSTALL_MANIFEST:
        return text
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        def invalid_constant(value):
            raise ValueError()
        manifest = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
        if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "version", "files"} or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or not isinstance(manifest["version"], str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", manifest["version"]) or not isinstance(manifest["files"], list) or not manifest["files"]:
            return text
        seen = set()
        for entry in manifest["files"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
                return text
            member = entry["path"]
            if not isinstance(member, str) or member not in allowlisted or member == INSTALL_MANIFEST or "\\" in member or ":" in member or len(member) > 240:
                return text
            parts = PurePosixPath(member).parts
            if not parts or member.startswith("/") or "/".join(parts) != member or any(part in {".", ".."} for part in parts) or member.casefold() in seen:
                return text
            seen.add(member.casefold())
            if not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) or type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= MAX_FILE:
                return text
            target = root.joinpath(*parts)
            if any(part.casefold() in PRIVATE_COMPONENTS for part in parts) or target.name.casefold() in PRIVATE_NAMES or target.name.casefold().startswith(".env.") or (target.suffix.lower() not in ALLOWED_SUFFIXES and target.name not in ALLOWED_EXTENSIONLESS):
                return text
            if _links(target) or not target.resolve().is_relative_to(root.resolve()) or not target.is_file() or target.stat().st_size != entry["bytes"]:
                return text
            with target.open("rb") as handle:
                raw = handle.read(MAX_FILE + 1)
            if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                return text
        return SHA_VALUE.sub(lambda match: match[1] + "x" * 64 + match[3], text)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError):
        return text


def _scan_text(path, text, forbidden, issues, *, shape_text=None):
    checks = (("personal_phone", PHONE, "A personal-phone-shaped value requires removal or explicit synthetic review."),
              ("identity_number", IDENTITY, "An identity-number-shaped value is not allowed in public files."),
              ("credential_value", KNOWN_SECRET, "A recognizable credential value is not allowed."),
              ("absolute_user_path", WINDOWS_HOME, "A hardcoded Windows user path is not portable."),
              ("absolute_user_path", POSIX_HOME, "A hardcoded user-home path is not portable."))
    for code, pattern, message in checks:
        value = shape_text if shape_text is not None and code in {"personal_phone", "identity_number"} else text
        if pattern.search(value):
            _issue(issues, path, code, message)
    def private_email(match):
        if _example(match.group(1)):
            return False
        # Host-guard tests use synthetic URL userinfo such as user@host;
        # these are not applicant mail addresses. Real emails remain flagged.
        local = match.group(0).rsplit("@", 1)[0]
        prefix = text[max(0, match.start() - 12):match.start()]
        return not (local in {"user", "x", "y", "synthetic", "example", "test"} and
                    (prefix.endswith(("https://", "http://")) or prefix.endswith("https://x@")))
    if any(private_email(match) for match in EMAIL.finditer(text)):
        _issue(issues, path, "email_requires_review", "A non-example email address requires public-source review.")
    # Build the marker in parts so the checker can itself be distributed.
    if "-----BEGIN " + "PRIVATE KEY-----" in text or "-----BEGIN " + "RSA PRIVATE KEY-----" in text:
        _issue(issues, path, "private_key", "Private key material is not allowed.")
    for match in POPULATED_SECRET.finditer(text):
        value = match.group(1)
        if not value.startswith(("${", "<")) and not value.casefold().startswith(("synthetic-", "example-", "test-", "fake-")):
            _issue(issues, path, "populated_secret", "A populated secret field requires removal.")
            break
    folded = text.casefold()
    if any(value.casefold() in folded for value in forbidden):
        _issue(issues, path, "private_identifier", "A configured private identifier was found; the value is not echoed.")
    if path.endswith(".json"):
        try:
            def invalid_constant(value):
                raise ValueError()
            data = json.loads(text, parse_constant=invalid_constant)
        except (ValueError, RecursionError):
            _issue(issues, path, "invalid_json", "A public JSON resource is invalid.")
            return
        if isinstance(data, dict):
            records = data.get("records")
            if isinstance(records, list) and any(isinstance(r, dict) and r.get("kind") in {"basic", "education", "experience", "family", "preference", "application"} and r.get("data") for r in records):
                _issue(issues, path, "private_export", "An applicant-record export is not a public resource.")
            if isinstance(data.get("profile"), dict) and any(data["profile"].get(k) for k in ("name", "phone", "email", "education", "family")):
                _issue(issues, path, "private_profile", "A populated applicant profile is not a public resource.")


def check_release(root, relative_paths, forbidden_values=(), *, require_files=True):
    """Return safe diagnostics for the exact allowlist supplied by the packager.

    forbidden_values can be read from an external private acceptance file. The
    scanner never persists or prints those values. No directory is auto-included.
    """
    root = Path(root).absolute()
    if not isinstance(relative_paths, (list, tuple, set)):
        raise TypeError("Pass an explicit collection of release-relative file paths.")
    issues, seen, selected = [], set(), set()
    checked, total = 0, 0
    forbidden = [str(value) for value in forbidden_values if isinstance(value, str) and len(value.strip()) >= 2]
    allowlisted = {name for item in relative_paths
                   if isinstance(name := item.get("path") if isinstance(item, dict) else item, str)}
    for item in relative_paths:
        name = item.get("path") if isinstance(item, dict) else item
        if not isinstance(name, str) or "\\" in name or ":" in name or len(name) > 240:
            _issue(issues, "<invalid-member>", "unsafe_path", "Release paths must be normalized relative paths.")
            continue
        parts = PurePosixPath(name).parts
        if not parts or name.startswith("/") or "/".join(parts) != name or any(p in {".", ".."} for p in parts):
            _issue(issues, "<invalid-member>", "unsafe_path", "Release paths must stay within the source checkout.")
            continue
        if name.casefold() in seen:
            _issue(issues, name, "duplicate_path", "A release path is duplicated or differs only in case.")
            continue
        seen.add(name.casefold())
        selected.add(name)
        file = root.joinpath(*parts)
        if any(p.casefold() in PRIVATE_COMPONENTS for p in parts) or file.name.casefold() in PRIVATE_NAMES or file.name.casefold().startswith(".env."):
            _issue(issues, name, "private_resource", "Private state, environment files or development caches cannot be released.")
            continue
        if file.suffix.lower() not in ALLOWED_SUFFIXES and file.name not in ALLOWED_EXTENSIONLESS:
            _issue(issues, name, "unsupported_type", "This release accepts only reviewed source and text resources.")
            continue
        try:
            if _links(file) or not file.resolve().is_relative_to(root.resolve()):
                _issue(issues, name, "linked_path", "Release files cannot traverse a link or reparse point.")
                continue
            if not file.is_file():
                _issue(issues, name, "missing_file", "An allowlisted source file is missing.")
                continue
            if file.stat().st_size > MAX_FILE:
                _issue(issues, name, "oversized_file", "A source resource exceeds the public file limit.")
                continue
            with file.open("rb") as handle:
                raw = handle.read(MAX_FILE + 1)
            if len(raw) > MAX_FILE:
                _issue(issues, name, "oversized_file", "A source resource exceeds the public file limit.")
                continue
            text = raw.decode("utf-8-sig")
            if "\x00" in text:
                _issue(issues, name, "binary_content", "Binary or opaque data cannot be released as text.")
                continue
            checked += 1
            total += len(raw)
            _scan_text(name, text, forbidden, issues,
                       shape_text=_install_shape_text(root, name, text, allowlisted))
        except (OSError, UnicodeError):
            _issue(issues, name, "unreadable_file", "A source file cannot be safely read as UTF-8 text.")
    if require_files:
        for name in sorted(REQUIRED - selected):
            _issue(issues, name, "required_file_missing", "The release allowlist omits a required public entrypoint.")
    # Filenames may themselves contain personal identifiers. Detect and redact
    # those without returning the matched value in diagnostics.
    for name in sorted(selected):
        if any(value.casefold() in name.casefold() for value in forbidden) or PHONE.search(name) or IDENTITY.search(name):
            _issue(issues, name, "private_filename", "An allowlisted filename contains a private identifier.")
    for issue in issues:
        label = issue["path"]
        for value in forbidden:
            label = re.sub(re.escape(value), "<redacted>", label, flags=re.I)
        label = PHONE.sub("<redacted>", label)
        label = IDENTITY.sub("<redacted>", label)
        issue["path"] = label
    return {"ok": not issues, "files_checked": checked, "total_bytes": total, "issues": issues}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, required=True, help="Exact public release allowlist JSON; list or {files:[...]}.")
    parser.add_argument("--private-patterns-file", type=Path, help="External private JSON array; values are never echoed.")
    args = parser.parse_args()
    try:
        data = json.loads(args.manifest.read_text("utf-8"))
        paths = data["files"] if isinstance(data, dict) else data
        forbidden = json.loads(args.private_patterns_file.read_text("utf-8")) if args.private_patterns_file else []
        if not isinstance(forbidden, list) or not all(isinstance(v, str) for v in forbidden):
            raise ValueError()
        result = check_release(args.root, paths, forbidden)
    except (OSError, ValueError, TypeError, KeyError):
        result = {"ok": False, "files_checked": 0, "total_bytes": 0, "issues": [{"path": "<manifest>", "code": "invalid_allowlist", "message": "The release allowlist or private pattern file is invalid."}]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
