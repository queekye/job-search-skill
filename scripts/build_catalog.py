"""Generate a public-only static catalog from company manifests."""
from pathlib import Path
import argparse
import json
import hashlib
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.packages import _bundled, _json, _zip, validate_files


def _catalog(root):
    version = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']
    items, archives = [], {}
    for path in sorted((root / 'companies').glob('*/manifest.json')):
        files = _bundled(SimpleNamespace(root_dir=root), path.parent.name)
        value, _ = validate_files(files)
        raw = _zip(files)
        download_path = f"packages/{value['id']}-{value['version']}.zip"
        archives[download_path] = raw
        items.append({k: value[k] for k in ('id','name','version','description','origin','capabilities','verification')} | {
            'download': {'path': download_path, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}})
    return version, items, archives


def _javascript(version, items):
    return ''''use strict';
const version = VERSION;
document.getElementById('catalog-version').textContent = version;
const catalog = DATA;
const container = document.getElementById('catalog');
for (const pack of catalog) {
 const article=document.createElement('article'); article.className='resource-card';
 const title=document.createElement('h2'); title.textContent=pack.name; article.append(title);
 const desc=document.createElement('p'); desc.textContent=pack.description; article.append(desc);
 const tags=document.createElement('div'); tags.className='capabilities';
 for (const [name,cap] of Object.entries(pack.capabilities||{})) {
  if (cap.status==='unavailable') continue;
  const tag=document.createElement('span');tag.className='badge';tag.textContent=name+' · '+(cap.status==='executable'?'已封装':'文档指引');tags.append(tag);
 }
 article.append(tags);
 const origin=document.createElement('p');origin.textContent=pack.origin;article.append(origin);
 const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='查看验证范围';pre.textContent=JSON.stringify(pack.verification,null,2);details.append(summary,pre);article.append(details);
 const actions=document.createElement('div');actions.className='form-actions';
 const a=document.createElement('a');a.className='button secondary';a.href=pack.download?.path||'packages/'+encodeURIComponent(pack.id)+'.zip';a.download=pack.id+'-'+pack.version+'.zip';a.textContent='下载 '+pack.version;actions.append(a);article.append(actions);container.append(article);
}
'''.replace('VERSION', json.dumps(version)).replace('DATA', json.dumps(items, ensure_ascii=False).replace('<','\\u003c'))


def catalog_files(root=ROOT):
    """Return a self-contained static site; never read applicant storage."""
    root = Path(root)
    version, items, archives = _catalog(root)
    return {'index.html': (root / 'web/catalog.html').read_bytes(),
            'styles.css': (root / 'web/styles.css').read_bytes(),
            'catalog.js': _javascript(version, items).encode('utf-8'),
            'index.json': _json({'schema_version': 1, 'packages': items}),
            'LICENSE.txt': (root / 'LICENSE').read_bytes(), '.nojekyll': b''} | archives


def build():
    version, items, _ = _catalog(ROOT)
    # The local preview uses its existing /packages/<id>.zip export route.
    local = [{k: value for k, value in item.items() if k != 'download'} for item in items]
    (ROOT / 'web' / 'catalog.js').write_text(_javascript(version, local), encoding='utf-8')
    return len(items)


def write_catalog(output):
    """Write only catalog assets to a fresh, dedicated output directory."""
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Static catalog output must be a new or empty directory.')
    files = catalog_files()
    output.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write the complete static catalog to a new or empty directory.')
    args = parser.parse_args(argv)
    if args.output is None:
        print(f'Public company packages: {build()}')
    else:
        try:
            count = write_catalog(args.output)
        except (OSError, ValueError) as error:
            parser.error(str(error))
        print(f'Static catalog: {count} public files -> {args.output}')


if __name__ == '__main__':
    main()
