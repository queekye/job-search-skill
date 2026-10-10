"""Run synthetic company fixtures and frontend regression suites without dependencies."""
from pathlib import Path
import os,shutil,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
def main():
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    node=os.environ.get('JOB_SEARCH_NODE') or (str(bundled) if bundled.is_file() else shutil.which('node'))
    if not node:raise SystemExit('Node.js is required for company fixture and frontend checks.')
    tests=sorted([*(ROOT/'companies').glob('*/fixtures/*.test.mjs'),*(ROOT/'tests').glob('*.test.mjs')])
    if not tests:raise SystemExit('No company fixture or frontend tests found.')
    result=subprocess.run([node,'--test',*[str(p) for p in tests]],cwd=ROOT)
    raise SystemExit(result.returncode)
if __name__=='__main__':main()
