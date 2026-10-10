"""Verify the supplied runtime snapshot before installing its local marketplace."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import re
import stat

MANIFEST = "INSTALL-MANIFEST.json"
REQUIRED = {".codex-plugin/plugin.json", ".mcp.json", ".agents/plugins/marketplace.json",
            "Install.cmd", "Start.cmd", "scripts/install.ps1", "scripts/start.ps1",
            "scripts/run.py", "scripts/verify_release.py",
            "service/app.py", "service/http.py", "service/mcp.py", "web/index.html",
            "skills/career/SKILL.md", "LICENSE"}


def _regular(path, root):
    for item in (path, *path.parents):
        if item == root:
            break
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Snapshot paths must not contain links or reparse points.")
    if not path.is_file():
        raise ValueError("Snapshot member is missing or is not a regular file.")


def verify(root):
    root = Path(root).resolve()
    manifest_path = root / MANIFEST
    _regular(manifest_path, root)
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError("Installation manifest is too large.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("Unsupported installation manifest.")
    expected, folded = set(), set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
            raise ValueError("Invalid installation member.")
        name = entry["path"]
        if not isinstance(name, str) or not name or "\\" in name or ":" in name or len(name) > 240:
            raise ValueError("Invalid installation member path.")
        parts = PurePosixPath(name).parts
        if name.startswith("/") or "/".join(parts) != name or any(part in {".", ".."} for part in parts) or name == MANIFEST:
            raise ValueError("Installation member escapes the snapshot.")
        if name.casefold() in folded:
            raise ValueError("Duplicate installation member.")
        folded.add(name.casefold())
        expected.add(name)
        if not isinstance(entry["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", entry["sha256"]) or type(entry["bytes"]) is not int or not 0 <= entry["bytes"] <= 2 * 1024 * 1024:
            raise ValueError("Invalid installation member digest or size.")
        path = root.joinpath(*parts)
        _regular(path, root)
        if not path.resolve().is_relative_to(root.resolve()) or path.stat().st_size != entry["bytes"]:
            raise ValueError("Installation member path or size changed.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Installation member hash changed.")
    if not REQUIRED <= expected:
        raise ValueError("Installation snapshot omits a required runtime entrypoint.")
    actual = set()
    for base, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            path = Path(base) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Installation snapshot contains a linked member.")
        actual.update((Path(base) / name).relative_to(root).as_posix() for name in names)
    if actual != expected | {MANIFEST}:
        raise ValueError("Installation snapshot contains unexpected or missing files.")
    return {"ok": True, "version": manifest.get("version"), "files_checked": len(expected)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("Installation snapshot verification failed. Extract a fresh reviewed release.")
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
