"""Create, validate and export public company resources without private App storage."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.app import AppError
from service.form_contract import template, validate_authoring
from service import packages


def _write_directory(path, files):
    path = packages._no_links(Path(path).absolute())
    if path.exists():
        raise AppError("output_exists", "Choose a new output directory.")
    path.mkdir(parents=True, exist_ok=False)
    for name, content in sorted(files.items()):
        target = packages._safe_path(path, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("template")
    for key in ("id", "name", "origin"):
        create.add_argument("--" + key, required=True)
    create.add_argument("--version", default="0.1.0")
    create.add_argument("--path", default="/resume")
    create.add_argument("--output", type=Path, required=True)
    for command in ("validate", "export"):
        sub = commands.add_parser(command)
        sub.add_argument("--directory", type=Path, required=True)
        sub.add_argument("--rehash", action="store_true")
        sub.add_argument("--output", type=Path, required=command == "export")
    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            result = template({key: getattr(args, key) for key in ("id", "name", "origin", "version", "path")})
            _write_directory(args.output, result.pop("files"))
            result["output"] = str(args.output.absolute())
        else:
            files = packages._directory_files(args.directory, validate=False)
            result = validate_authoring({"files": {name: value.decode("utf-8") for name, value in files.items()}, "rehash": args.rehash})
            normalized = result.pop("files", None)
            if normalized is not None:
                files = {name: content.encode("utf-8") for name, content in normalized.items()}
            if args.command == "validate" and args.output:
                _write_directory(args.output, {name: value.decode("utf-8") for name, value in files.items()})
                result["output"] = str(args.output.absolute())
            elif args.command == "export":
                target = packages._no_links(args.output.absolute())
                if target == args.directory.absolute() or target.is_relative_to(args.directory.absolute()):
                    raise AppError("invalid_output", "Write the ZIP outside the source package directory.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    handle.write(packages._zip(files))
                result["output"] = str(target)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (AppError, OSError, UnicodeError) as error:
        message = {"code": error.code, "message": error.message} if isinstance(error, AppError) else {"code": "resource_io_error", "message": "Public resource files could not be read or written; existing outputs are preserved."}
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
