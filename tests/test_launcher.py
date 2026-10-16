"""The real launcher must speak MCP and respect an isolated data directory."""
from pathlib import Path
import json,os,subprocess,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class LauncherTests(unittest.TestCase):
    def test_stdio_entrypoint_without_http_and_with_explicit_data_dir(self):
        with tempfile.TemporaryDirectory(prefix='career-launch-') as folder:
            data=Path(folder)/'private'
            request={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','clientInfo':{'name':'synthetic-test','version':'1'}}}
            result=subprocess.run([sys.executable,str(ROOT/'scripts/run.py'),'mcp','--data-dir',str(data)],input=json.dumps(request)+'\n',capture_output=True,text=True,encoding='utf-8',timeout=15,cwd=folder)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('serverInfo',json.loads(result.stdout)['result'])
            self.assertTrue((data/'career.db').is_file())
    @unittest.skipUnless(os.name=='nt','Windows plugin launcher')
    def test_shipped_powershell_launcher(self):
        with tempfile.TemporaryDirectory(prefix='career-ps-launch-') as folder:
            config=json.loads((ROOT/'.mcp.json').read_text())['mcpServers']['career-local']
            request={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','clientInfo':{'name':'synthetic-test','version':'1'}}}
            result=subprocess.run([config['command'],*config['args']],cwd=ROOT,env=dict(os.environ,JOB_SEARCH_DATA_DIR=folder,JOB_SEARCH_PYTHON=sys.executable),input=json.dumps(request)+'\n',capture_output=True,text=True,encoding='utf-8',timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('serverInfo',json.loads(result.stdout)['result'])

    @unittest.skipUnless(os.name=='nt','Windows default bundled interpreter')
    def test_powershell_launcher_finds_bundled_python_without_override(self):
        bundled = Path(os.environ.get('USERPROFILE', str(Path.home()))) / '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
        if not bundled.is_file():
            self.skipTest('Host has no Codex bundled Python at the supported path')
        with tempfile.TemporaryDirectory(prefix='career-default-launch-') as folder:
            config = json.loads((ROOT/'.mcp.json').read_text())['mcpServers']['career-local']
            env = dict(os.environ, JOB_SEARCH_DATA_DIR=folder)
            env.pop('JOB_SEARCH_PYTHON', None)
            request = {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','clientInfo':{'name':'synthetic-default','version':'1'}}}
            result = subprocess.run([config['command'], *config['args']], cwd=ROOT, env=env,
                                    input=json.dumps(request)+'\n', capture_output=True,
                                    text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('serverInfo', json.loads(result.stdout)['result'])

    @unittest.skipUnless(os.name=='nt','Windows runtime snapshot launcher')
    def test_runtime_snapshot_launches_mcp_without_developer_scripts(self):
        from scripts.build_release import installation_files, public_paths
        from scripts.verify_release import verify
        files = {name: (ROOT / name).read_bytes() for name in public_paths()}
        version = json.loads(files['.codex-plugin/plugin.json'])['version']
        runtime = installation_files(files, version)
        with tempfile.TemporaryDirectory(prefix='career-runtime-launch-') as folder:
            root = Path(folder) / 'runtime with spaces'
            for name, raw in runtime.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            self.assertFalse((root/'scripts/build_release.py').exists())
            config = json.loads((root/'.mcp.json').read_text())['mcpServers']['career-local']
            requests = [{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','clientInfo':{'name':'synthetic-runtime','version':'1'}}},
                        {'jsonrpc':'2.0','id':2,'method':'tools/list'}]
            result = subprocess.run([config['command'], *config['args']], cwd=root,
                                    env=dict(os.environ, JOB_SEARCH_DATA_DIR=str(Path(folder)/'private'), JOB_SEARCH_PYTHON=sys.executable),
                                    input='\n'.join(json.dumps(request) for request in requests)+'\n', capture_output=True,
                                    text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            responses = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(responses[1]['result']['tools']), 9)
            self.assertTrue(verify(root)['ok'])

    @unittest.skipUnless(os.name=='nt','Windows runtime installer')
    def test_runtime_installer_uses_snapshot_and_stops_before_mutation_if_tampered(self):
        from scripts.build_release import installation_files, public_paths
        files = {name: (ROOT / name).read_bytes() for name in public_paths()}
        runtime = installation_files(files, json.loads(files['.codex-plugin/plugin.json'])['version'])
        with tempfile.TemporaryDirectory(prefix='career-fake-cli-') as folder:
            folder = Path(folder)
            root = folder / 'runtime with spaces'
            for name, raw in runtime.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            # This synthetic CLI only records arguments; it never installs a plugin.
            command_log = folder / 'commands.txt'
            (folder/'codex.cmd').write_text('@echo off\necho %*>>"%CAREER_TEST_COMMANDS%"\nif "%3"=="list" echo {"marketplaces":[]}\nexit /b 0\n', encoding='ascii')
            env = dict(os.environ, PATH=str(folder)+os.pathsep+os.environ.get('PATH',''),
                       JOB_SEARCH_PYTHON=sys.executable, CAREER_TEST_COMMANDS=str(command_log))
            command = ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(root/'scripts/install.ps1')]
            result = subprocess.run(command, cwd=folder, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = command_log.read_text().splitlines()
            self.assertEqual(len(calls), 5)
            self.assertTrue(calls[-1].startswith('plugin add job-search-skill@job-search-local'))
            self.assertFalse((root/'scripts/build_release.py').exists())
            (root/'web/index.html').write_bytes(b'tampered')
            command_log.unlink()
            result = subprocess.run(command, cwd=folder, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(command_log.read_text().splitlines()), 2)

    @unittest.skipUnless(os.name=='nt','Windows marketplace update')
    def test_installer_updates_only_its_marketplace_and_rolls_back_failures(self):
        from scripts.build_release import installation_files, public_paths
        files = {name: (ROOT / name).read_bytes() for name in public_paths()}
        runtime = installation_files(files, json.loads(files['.codex-plugin/plugin.json'])['version'])
        fake_cli = '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]; path=Path(os.environ['CAREER_TEST_STATE'])
with open(os.environ['CAREER_TEST_COMMANDS'],'a') as log: log.write(json.dumps(args)+'\\n')
state=json.loads(path.read_text())
if '--help' in args: sys.exit(0)
if args[:3]==['plugin','marketplace','list']:
 print(json.dumps({'marketplaces':state['marketplaces']})); sys.exit(0)
if args[:3]==['plugin','marketplace','remove']:
 state['marketplaces']=[item for item in state['marketplaces'] if item['name']!=args[3]]
elif args[:3]==['plugin','marketplace','add']:
 if os.environ['CAREER_TEST_FAILURE']=='add' and args[3]==os.environ['CAREER_TEST_NEW_ROOT']: sys.exit(1)
 state['marketplaces'].append({'name':'job-search-local','root':args[3]})
elif args[:2]==['plugin','add']:
 current=next(item['root'] for item in state['marketplaces'] if item['name']=='job-search-local')
 if os.environ['CAREER_TEST_FAILURE']=='plugin' and current==os.environ['CAREER_TEST_NEW_ROOT']: sys.exit(1)
 state['installed_root']=current
else: sys.exit(2)
path.write_text(json.dumps(state))
'''
        for scenario in ('success','add','plugin','unrelated','missing'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(prefix='career-upgrade-') as folder:
                folder=Path(folder).resolve(); old=folder/'previous runtime'; new=folder/'new runtime'
                for root in (old,new):
                    if root==old and scenario=='missing': continue
                    for name,raw in runtime.items():
                        path=root/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(raw)
                if scenario=='unrelated':
                    marketplace=old/'.agents/plugins/marketplace.json'
                    data=json.loads(marketplace.read_text()); data['plugins'][0]['name']='other-plugin'
                    marketplace.write_text(json.dumps(data))
                state_file=folder/'state.json'; command_log=folder/'commands.jsonl'; driver=folder/'fake_cli.py'
                initial={'marketplaces':[{'name':'unrelated-market','root':'unchanged'},
                                         {'name':'job-search-local','root':str(old)}], 'installed_root':str(old)}
                state_file.write_text(json.dumps(initial)); driver.write_text(fake_cli)
                (folder/'codex.cmd').write_text('@echo off\n"%CAREER_TEST_PYTHON%" "%CAREER_TEST_CLI%" %*\nexit /b %errorlevel%\n',encoding='ascii')
                env=dict(os.environ,PATH=str(folder)+os.pathsep+os.environ.get('PATH',''),
                         JOB_SEARCH_PYTHON=sys.executable,CAREER_TEST_PYTHON=sys.executable,
                         CAREER_TEST_CLI=str(driver),CAREER_TEST_STATE=str(state_file),
                         CAREER_TEST_COMMANDS=str(command_log),CAREER_TEST_NEW_ROOT=str(new),CAREER_TEST_FAILURE=scenario)
                result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(new/'scripts/install.ps1')],
                                      cwd=folder,env=env,capture_output=True,text=True,encoding='utf-8',timeout=25)
                state=json.loads(state_file.read_text()); calls=[json.loads(line) for line in command_log.read_text().splitlines()]
                self.assertIn(initial['marketplaces'][0],state['marketplaces'])
                if scenario=='success':
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(state['installed_root'],str(new))
                    self.assertEqual(next(item['root'] for item in state['marketplaces'] if item['name']=='job-search-local'),str(new))
                else:
                    self.assertNotEqual(result.returncode,0)
                    self.assertEqual(state,initial)
                    if scenario in ('unrelated','missing'):
                        self.assertFalse(any(call[:3]==['plugin','marketplace','remove'] for call in calls))
                    else:
                        self.assertIn('Restored the previous project marketplace and plugin.',result.stdout)
                    if scenario=='missing':
                        self.assertIn('its mapping was kept',result.stderr)
if __name__=='__main__':unittest.main()
