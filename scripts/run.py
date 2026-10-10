"""Stable local launcher; data never defaults to this source checkout."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'serve'
    if command == 'serve':
        from service.http import main as serve
        serve(sys.argv[2:])
    elif command == 'mcp':
        from service.mcp import main as mcp
        mcp(sys.argv[2:])
    elif command == 'package':
        from scripts.build_release import main as package
        package(sys.argv[2:])
    elif command in {'backup', 'restore'}:
        from service.backup import main as transfer
        raise SystemExit(transfer([command, *sys.argv[2:]]))
    elif command == 'check':
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        raise SystemExit(0 if result.wasSuccessful() else 1)
    else:
        raise SystemExit('Usage: run.py serve | mcp | backup | restore | check | package (use --help for command options)')


if __name__ == '__main__':
    main()
