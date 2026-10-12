"""Install a pinned browser library in the user's cache, without npm or Codex."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
import urllib.request

VERSION = "1.62.1"
MAX_DOWNLOAD = 16 * 1024 * 1024


def runtime_root():
    return Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "job-search-skill" / "browser-runtime" / VERSION


def _fetch(url, limit):
    with urllib.request.urlopen(url, timeout=30) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Browser dependency exceeds its download limit.")
    return raw


def _unpack(raw, target):
    expanded = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if not path.parts or path.parts[0] != "package" or path.is_absolute() or any(p in {"..", "."} or "\\" in p or ":" in p for p in path.parts):
                raise ValueError("Invalid browser dependency member.")
            relative = path.parts[1:]
            if not relative:
                continue
            destination = target.joinpath(*relative)
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                expanded += member.size
                if expanded > 80 * 1024 * 1024:
                    raise ValueError("Expanded browser dependency exceeds its limit.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    output.write(archive.extractfile(member).read())
            else:
                raise ValueError("Linked browser dependency members are not supported.")


def setup():
    root = runtime_root()
    entry = root / "node_modules" / "playwright" / "index.mjs"
    if entry.is_file() and (root / "node_modules" / "playwright-core" / "package.json").is_file():
        return {"version": VERSION, "module_path": str(entry), "installed": True}
    root.parent.mkdir(parents=True, exist_ok=True)
    if root.exists():
        raise ValueError("The browser runtime is incomplete; choose a new cache directory or repair it locally.")
    with tempfile.TemporaryDirectory(prefix=".browser-", dir=root.parent) as directory:
        stage = Path(directory) / "runtime"
        for name in ("playwright-core", "playwright"):
            metadata = json.loads(_fetch(f"https://registry.npmjs.org/{name}/{VERSION}", 1024 * 1024))
            expected_url = f"https://registry.npmjs.org/{name}/-/{name}-{VERSION}.tgz"
            integrity = metadata.get("dist", {}).get("integrity", "")
            if metadata.get("name") != name or metadata.get("version") != VERSION or metadata.get("dist", {}).get("tarball") != expected_url or not integrity.startswith("sha512-"):
                raise ValueError("Unexpected browser dependency metadata.")
            raw = _fetch(expected_url, MAX_DOWNLOAD)
            digest = "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode("ascii")
            if digest != integrity:
                raise ValueError("Browser dependency integrity check failed.")
            _unpack(raw, stage / "node_modules" / name)
        stage.rename(root)
    return {"version": VERSION, "module_path": str(entry), "installed": True}
