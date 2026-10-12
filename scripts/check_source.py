"""Scan the public source allowlist separately from the smaller runtime release."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.build_release import public_paths
from scripts.check_release import check_release

SOURCE_FILES = {
    '.gitignore', '.gitattributes', 'AGENTS.md', 'CONTRIBUTING.md', 'Check.cmd',
    '.github/workflows/check.yml', '.github/ISSUE_TEMPLATE/company-resource.yml',
    'references/development-contract.md', 'references/development-handoff.md', 'references/resource-index.md', 'references/validation.md',
    'scripts/build_release.py', 'scripts/build_catalog.py', 'scripts/check_release.py',
    'scripts/check_source.py', 'scripts/check_node.py', 'scripts/validate_site_extension.py',
}


def source_paths(root=ROOT):
    paths = set(public_paths(root)) | SOURCE_FILES
    # Development tests live only in this known top-level directory.
    for extension in ('.py', '.mjs'):
        paths.update(path.relative_to(root).as_posix() for path in (root / 'tests').glob('*' + extension))
    return sorted(paths)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-patterns-file', type=Path)
    args = parser.parse_args(argv)
    patterns = []
    if args.private_patterns_file:
        path = args.private_patterns_file.resolve()
        if path == ROOT or ROOT in path.parents:
            raise SystemExit('Private scan patterns must stay outside source.')
        patterns = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(patterns, list) or not all(isinstance(value, str) and len(value) > 1 for value in patterns):
            raise SystemExit('Use an external JSON array of nonempty patterns.')
    result = check_release(ROOT, source_paths(), forbidden_values=patterns)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
