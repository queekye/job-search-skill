"""Build only public, explicitly selected resources; never instantiate private App."""
from pathlib import Path
from types import SimpleNamespace
import argparse,hashlib,json,os,sys,tempfile,zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_catalog import build as build_catalog, catalog_files
from scripts.check_release import check_release
from service.packages import _bundled,_zip,_decode_json,validate_files
from scripts.validate_site_extension import MAX_BYTES as MAX_PLAN_BYTES, validate as validate_site_extension
from scripts.verify_release import MANIFEST as INSTALL_MANIFEST, verify as verify_snapshot

CORE_FILES=['.codex-plugin/plugin.json','.mcp.json','README.md','LICENSE','Start.cmd','Install.cmd',
            'scripts/run.py','scripts/start.ps1','scripts/install.ps1','scripts/verify_release.py',
            'scripts/browser-session.mjs','scripts/profile-fill.mjs','scripts/profile-upload.mjs',
            'references/company-pack.md','references/workflow.md','references/otp.md',
            'references/profile-fill-coverage.md','references/site-extension.md','references/migration.md',
            'references/sync.md','references/public-resources.md']
TREES={'service':{'.py'},'skills':{'.md','.yaml'},'schemas':{'.json'},'web':{'.html','.js','.css'}}

def public_paths(root=ROOT):
    paths=set(CORE_FILES)
    for directory,extensions in TREES.items():
        for base,dirs,files in os.walk(root/directory,followlinks=False):
            dirs[:]=[d for d in dirs if d not in {'__pycache__','node_modules','.git'}]
            for name in files:
                path=Path(base)/name
                if path.suffix in extensions:paths.add(path.relative_to(root).as_posix())
    for manifest in (root/'companies').glob('*/manifest.json'):
        files=_bundled(SimpleNamespace(root_dir=root),manifest.parent.name)
        paths.update('companies/'+manifest.parent.name+'/'+name for name in files)
    return sorted(paths)

def zip_bytes(files):
    import io
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(name,(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            archive.writestr(info,raw)
    return buffer.getvalue()

def installation_files(files, version):
    """A self-contained runtime and local marketplace; no consumer rebuild."""
    marketplace={'name':'job-search-local','interface':{'displayName':'Job Search Local'},
                 'plugins':[{'name':'job-search-skill','source':{'source':'local','path':'.'},
                             'policy':{'installation':'AVAILABLE','authentication':'ON_USE'},
                             'category':'Productivity'}]}
    runtime=dict(files)
    runtime['.agents/plugins/marketplace.json']=json.dumps(marketplace,indent=2).encode()
    manifest={'schema_version':1,'version':version,
              'files':[{'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
                       for name,raw in sorted(runtime.items())]}
    runtime[INSTALL_MANIFEST]=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    return runtime

def validate_extension_plans(files):
    """Check public plans in the exact release snapshot, without running them."""
    count = 0
    for name, raw in files.items():
        parts = name.split('/')
        if len(parts) < 4 or parts[0] != 'companies' or parts[2] != 'references' or not name.endswith('site-extension.json'):
            continue
        if len(raw) > MAX_PLAN_BYTES:
            raise ValueError('Public site-extension plan exceeds the size limit.')
        plan = _decode_json(raw)
        manifest = _decode_json(files['/'.join(parts[:2]) + '/manifest.json'])
        result = validate_site_extension(plan)
        if not result['ok'] or manifest['id'] != parts[1] or plan['company_pack']['id'] != manifest['id'] or plan['origin'] != manifest['origin'].rstrip('/'):
            raise ValueError('Invalid public site-extension plan or company binding.')
        count += 1
    return count

def build(forbidden_values=()):
    build_catalog()
    paths=public_paths()
    result=check_release(ROOT,paths,forbidden_values=forbidden_values)
    if not result['ok']:
        print(json.dumps(result,ensure_ascii=False,indent=2))
        raise SystemExit('Public release check failed; nothing packaged.')
    # Scan the exact immutable snapshot used to produce all archives.
    files={p:(ROOT/p).read_bytes() for p in paths}
    plan_count=validate_extension_plans(files)
    version=json.loads(files['.codex-plugin/plugin.json'])['version']
    runtime=installation_files(files,version)
    with tempfile.TemporaryDirectory(prefix='career-release-scan-') as staging:
        snapshot=Path(staging)
        for p,raw in runtime.items():
            target=snapshot/p;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        result=check_release(snapshot,sorted(runtime),forbidden_values=forbidden_values)
        if not result['ok']:
            print(json.dumps(result,ensure_ascii=False,indent=2))
            raise SystemExit('Immutable snapshot failed release check.')
        verify_snapshot(snapshot)
    out=ROOT/'dist'
    if out.is_symlink() or (out.exists() and getattr(out.lstat(),'st_file_attributes',0)&0x400):
        raise SystemExit('Release output must not be a link.')
    out.mkdir(exist_ok=True)
    def write(path,raw):
        target=out/path
        # Reject links in every existing output ancestor; never traverse external paths.
        for p in (target,*target.parents):
            if p==ROOT:break
            if p.is_symlink() or (p.exists() and getattr(p.lstat(),'st_file_attributes',0)&0x400):raise SystemExit('Linked release output rejected.')
        if out.resolve() not in target.resolve().parents:raise SystemExit('Release output escaped dist.')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(raw)
    artifacts={f'job-search-skill-{version}.zip':zip_bytes({'job-search-skill/'+p:v for p,v in runtime.items()})}
    catalog=catalog_files(ROOT)
    for manifest in sorted((ROOT/'companies').glob('*/manifest.json')):
        prefix='companies/'+manifest.parent.name+'/'
        pack={p[len(prefix):]:raw for p,raw in files.items() if p.startswith(prefix)}
        meta,_=validate_files(pack);raw=_zip(pack)
        artifacts[f"packages/{meta['id']}-{meta['version']}.zip"]=raw
        if catalog[f"packages/{meta['id']}-{meta['version']}.zip"] != raw:
            raise SystemExit('Static catalog differs from the selected immutable company snapshot.')
    for p,raw in catalog.items():write('catalog/'+p,raw)
    artifacts[f'company-catalog-{version}.zip']=zip_bytes(catalog)
    # Refuse a stale output tree before changing it. Extract/build a fresh snapshot
    # after a runtime layout change instead of silently carrying extra files.
    marketplace_root=out/'marketplace'
    for base,dirs,names in os.walk(marketplace_root,followlinks=False):
        for name in dirs+names:
            path=Path(base)/name
            if path.is_symlink() or getattr(path.lstat(),'st_file_attributes',0)&0x400:raise SystemExit('Linked marketplace content rejected.')
        for name in names:
            if (Path(base)/name).relative_to(marketplace_root).as_posix() not in runtime:
                raise SystemExit('Unexpected marketplace file; use a fresh dist/marketplace directory.')
    for p,raw in runtime.items():write('marketplace/'+p,raw)
    verify_snapshot(marketplace_root)
    for p,raw in artifacts.items():write(p,raw)
    manifest={'version':version,'files':[{'path':p,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)} for p,raw in runtime.items()],
      'artifacts':[{'path':p,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)} for p,raw in artifacts.items()],
      'scan':{'files_checked':result['files_checked'],'issues':0,'private_patterns_supplied':bool(forbidden_values)}}
    write('RELEASE-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2).encode())
    write('SHA256SUMS.txt',''.join(f"{a['sha256']}  {a['path']}\n" for a in manifest['artifacts']).encode())
    return {'version':version,'public_files':len(runtime),'site_extension_plans':plan_count,'archives':len(artifacts),'source_bytes':sum(map(len,runtime.values())),'output':str(out)}

def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--private-patterns-file',type=Path)
    args=parser.parse_args(argv);values=[]
    if args.private_patterns_file:
        path=args.private_patterns_file.resolve()
        if path==ROOT or ROOT in path.parents:raise SystemExit('Private scan patterns must stay outside source.')
        values=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(values,list) or not all(isinstance(v,str) and len(v)>1 for v in values):raise SystemExit('Use an external JSON array of nonempty patterns.')
    print(json.dumps(build(values),ensure_ascii=False))
if __name__=='__main__':main()
