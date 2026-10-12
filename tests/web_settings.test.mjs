import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../web/app.js', import.meta.url), 'utf8');
const decode = value => String(value).replace(/&quot;/g, '"').replace(/&#39;/g, "'")
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

// A small DOM/form boundary, not a replacement implementation of the UI logic.
// The unmodified app.js renders, binds its handlers and makes real API requests
// against a synthetic fetch stub. No credentials, browser or dependencies needed.
class Element {
  constructor(tag, attributes = {}, parent = null) {
    this.tagName = tag.toUpperCase();
    this.attributes = attributes;
    this.parentElement = parent;
    this.children = [];
    this.id = attributes.id || '';
    this.name = attributes.name || '';
    this.type = attributes.type || '';
    this.value = attributes.value || '';
    this.disabled = 'disabled' in attributes;
    this.required = 'required' in attributes;
    this.checked = 'checked' in attributes;
    this.hidden = 'hidden' in attributes;
    this.dataset = Object.fromEntries(Object.entries(attributes).filter(([k]) => k.startsWith('data-'))
      .map(([k, v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v]));
    this.classList = {toggle() {}};
    this.textContent = '';
    this.htmlWrites = 0;
    this._html = '';
  }
  get innerHTML() { return this._html; }
  set innerHTML(html) {
    this._html = html;
    this.htmlWrites++;
    this.children = [];
    const stack = [this];
    for (const match of html.matchAll(/<\/?([\w-]+)\b([^>]*)>/g)) {
      const [raw, tag, tail] = match;
      if (raw.startsWith('</')) {
        const index = stack.findLastIndex(el => el.tagName === tag.toUpperCase());
        if (index > 0) stack.length = index;
        continue;
      }
      const attrs = {};
      for (const [, key, quoted, single, bare] of tail.matchAll(/([\w-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s/>]+)))?/g)) {
        attrs[key] = decode(quoted ?? single ?? bare ?? '');
      }
      const parent = stack.at(-1), element = new Element(tag, attrs, parent);
      if (tag === 'textarea') {
        const end = html.indexOf('</textarea>', match.index + raw.length);
        if (end >= 0) element.value = decode(html.slice(match.index + raw.length, end));
      }
      parent.children.push(element);
      if (tag === 'option' && parent.tagName === 'SELECT' && (parent.children.length === 1 || 'selected' in attrs)) {
        parent.value = element.value;
      }
      if (!['input', 'br', 'hr', 'img', 'meta', 'link'].includes(tag)) stack.push(element);
    }
  }
  descendants() { return this.children.flatMap(child => [child, ...child.descendants()]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const nodes = this.descendants();
    if (selector.startsWith('#')) return nodes.filter(el => el.id === selector.slice(1));
    const attr = selector.match(/^\[([\w-]+)(?:=([^\]]+))?\]$/);
    if (attr) return nodes.filter(el => attr[1] in el.attributes && (attr[2] === undefined || el.attributes[attr[1]] === attr[2]));
    return nodes.filter(el => el.tagName.toLowerCase() === selector);
  }
  effectivelyDisabled() { return this.disabled || Boolean(this.parentElement?.effectivelyDisabled()); }
  reportValidity() {
    return this.descendants().every(el => !el.required || el.effectivelyDisabled() || Boolean(el.value));
  }
  showModal() { this.open = true; }
  close() { this.open = false; }
  reset() { for(const el of this.descendants()){if(['INPUT','TEXTAREA'].includes(el.tagName))el.value=el.attributes.value||'';} }
  focus() { this.focused = true; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  click() { if (!this.effectivelyDisabled()) return this.onclick?.({target: this}); }
}

class FormData {
  constructor(form) {
    this.values = new Map(form.descendants().filter(el => ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)
      && el.name && !el.effectivelyDisabled() && (el.type !== 'checkbox' || el.checked))
      .map(el => [el.name, el.type === 'checkbox' ? 'on' : el.value]));
  }
  get(name) { return this.values.get(name) ?? null; }
}

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
};
const tick = () => new Promise(resolve => setImmediate(resolve));
const manual = () => ({provider: 'manual', enabled: false, token_configured: false, url: null, connection_test: null});
const gotify = (url = 'http://127.0.0.1:8123') => ({provider: 'gotify', enabled: true, token_configured: true, url, connection_test: null});
const connectionResult = (status = 'passed') => ({provider: 'gotify', status,
  checked_at: '2026-09-16T06:00:00Z', code: status === 'passed' ? 'ok' : 'gotify_unauthorized',
  message: status === 'passed' ? '合成测试：连接成功且读取权限有效。' : '合成测试：客户端 Token 无读取权限。',
  live_login_verified: false,
});
const apiError = (code, message = code) => ({failure: {code, message}});
const localSync = () => ({configured:false,config:{url:'',username:'',workspace:'career',automatic:false,password_set:false},device_id:'synthetic-device-a',mode:'local',dirty:false,last_synced_at:null,last_error:null,remote_owner:null});
const writerSync = () => ({...localSync(),configured:true,config:{url:'https://storage.example.test/dav/',username:'synthetic-user',workspace:'career',automatic:true,password_set:true},mode:'writer',last_synced_at:'2026-10-10T01:00:00Z',remote_owner:'synthetic-device-a'});
const publicPack = () => ({id:'synthetic-company',name:'合成公司',version:'1.2.3',description:'合成公开方法',origin:'https://company.example.test',capabilities:{applications:{status:'documentation',detail:'合成指引'}},verification:{live_reverified:false},download:{path:'packages/synthetic-company-1.2.3.zip',sha256:'a'.repeat(64),bytes:1024}});
const publicDefaults = {registry_url:'https://job-search-company-resources.pages.dev/index.json',contribution_url:'https://github.com/queekye/job-search-skill/issues/new?template=company-resource.yml'};

test('public resource defaults appear in settings without reading the remote directory',async()=>{
 const ui=await app({withAccounts:true});await ui.render('settings');
 const form=ui.el('registry-form');
 assert.equal(form.querySelector('[name=registry_url]').value,publicDefaults.registry_url);
 assert.equal(form.querySelector('[name=contribution_url]').value,publicDefaults.contribution_url);
 assert.equal(ui.calls.some(c=>c.operation==='resource.refresh'),false);
 await ui.render('resources');
 assert.match(ui.el('content').innerHTML,/https:\/\/github\.com\/queekye\/job-search-skill\/issues\/new\?template=company-resource\.yml/);
 assert.equal(ui.el('registry-refresh').disabled,false);
 assert.equal(ui.calls.some(c=>c.operation==='resource.refresh'),false);
});

test('public resources never fetch automatically and remain available through offline import',async()=>{
 const ui=await app({withAccounts:true});await ui.render('resources');
 assert.equal(ui.el('registry-refresh').disabled,false);assert.equal(ui.calls.length,0);
 assert.equal(ui.el('package-file').attributes.accept,'.zip');assert.notEqual(ui.el('import-package'),null);
 ui.stored.settings.registry_url='https://static.example.test/index.json';await ui.evaluate('load()');
 assert.equal(ui.el('registry-refresh').disabled,false);assert.equal(ui.calls.some(c=>c.operation==='resource.refresh'),false);
});

test('public downloads pin displayed identity and digest and wait for user review',async()=>{
 const ui=await app({withAccounts:true});ui.stored.settings.registry_url='https://static.example.test/index.json';await ui.evaluate("navigate('resources')");
 ui.handlers['resource.refresh']=args=>({configured:true,url:args.url,items:[publicPack()]});
 ui.handlers['resource.download']=()=>({candidate_id:'b'.repeat(32),review_required:true,trust:'pending_user_review',review_files:[{path:'adapter.mjs',content:'export const SYNTHETIC=1;',truncated:false}]});
 ui.handlers['resource.approve']=()=>({installed:true});
 await ui.el('registry-refresh').click();assert.equal(ui.calls.find(c=>c.operation==='resource.refresh').user_id,'synthetic-user-a');
 await ui.document.querySelector('[data-registry-package]').click();
 assert.deepEqual(ui.calls.find(c=>c.operation==='resource.download').args,{id:'synthetic-company',version:'1.2.3',sha256:'a'.repeat(64),url:'https://static.example.test/index.json'});
 assert.match(ui.el('detail-content').innerHTML,/export const SYNTHETIC=1/);assert.equal(ui.calls.some(c=>c.operation==='resource.approve'),false);
 await ui.el('approve-package').click();assert.equal(ui.calls.filter(c=>c.operation==='resource.approve').length,1);
 assert.equal(ui.el('detail').open,false);
});

test('public metadata is escaped and a failed refresh retains the previous visible directory',async()=>{
 const ui=await app();ui.stored.settings.registry_url='https://static.example.test/index.json';await ui.evaluate("navigate('resources')");
 ui.handlers['resource.refresh']=args=>({configured:true,url:args.url,items:[{...publicPack(),name:'<script>SYNTHETIC</script>'}]});
 await ui.el('registry-refresh').click();assert.match(ui.el('content').innerHTML,/&lt;script&gt;SYNTHETIC&lt;\/script&gt;/);
 ui.handlers['resource.refresh']=()=>apiError('registry_unavailable','合成目录不可访问');await ui.el('registry-refresh').click();
 assert.equal(ui.el('registry-refresh').disabled,false);assert.match(ui.el('notice').textContent,/合成目录不可访问/);
 assert.notEqual(ui.document.querySelector('[data-registry-package]'),null);
});

test('a public download failure never opens an approval dialog or enables a package',async()=>{
 const ui=await app();ui.stored.settings.registry_url='https://static.example.test/index.json';await ui.evaluate("navigate('resources')");
 ui.handlers['resource.refresh']=args=>({configured:true,url:args.url,items:[publicPack()]});
 ui.handlers['resource.download']=()=>apiError('registry_version_changed','合成目录版本已变化');await ui.el('registry-refresh').click();
 await ui.document.querySelector('[data-registry-package]').click();
 assert.equal(ui.el('detail').open,undefined);assert.match(ui.el('notice').textContent,/合成目录版本已变化/);
 assert.equal(ui.calls.some(c=>c.operation==='resource.approve'),false);assert.equal(ui.document.querySelector('[data-registry-package]').disabled,false);
});

test('account switches discard pending public downloads and clear the old directory',async()=>{
 const ui=await app({withAccounts:true}),pending=deferred();ui.stored.settings.registry_url='https://static.example.test/index.json';await ui.evaluate("navigate('resources')");
 ui.handlers['resource.refresh']=args=>({configured:true,url:args.url,items:[publicPack()]});ui.handlers['resource.download']=()=>pending.promise;
 await ui.el('registry-refresh').click();const downloading=ui.document.querySelector('[data-registry-package]').click();await tick();
 await ui.evaluate("switchAccount('synthetic-user-b')");pending.resolve({candidate_id:'b'.repeat(32)});await downloading;
 assert.equal(ui.document.querySelector('[data-registry-package]'),null);assert.equal(ui.el('detail').open,undefined);
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.el('notice').hidden,true);
});

function fillSync(ui,{url='https://storage.example.test/dav/',username='synthetic-user',workspace='career',password='synthetic-password',automatic=true}={}){
 for(const [id,value] of Object.entries({url,username,workspace,password})){ui.el('sync-'+id).value=value;ui.el('sync-'+id).oninput();}
 ui.el('sync-automatic').checked=automatic;ui.el('sync-automatic').onchange();
}
async function submitSync(ui){const form=ui.el('sync-form');await form.onsubmit({target:form,preventDefault(){}});}

test('sync settings cover the shared dataset and expose no password',async()=>{
 const ui=await app({withAccounts:true});await ui.render('settings');
 assert.match(ui.el('content').innerHTML,/全部账号业务数据和原始附件/);
 assert.match(ui.el('content').innerHTML,/公司资源、待审包和版本锁不进入个人同步/);
 assert.match(ui.el('content').innerHTML,/从公共目录下载公司资源，或离线导入/);
 assert.match(ui.el('content').innerHTML,/未另行加密/);
 assert.equal(ui.el('sync-password').value,'');assert.equal(ui.el('sync-password').required,true);
 assert.equal(ui.el('sync-fields').disabled,false);assert.equal(ui.el('sync-now').disabled,true);
 assert.deepEqual(ui.syncCalls.map(c=>[c.operation,c.user_id]),[['sync.status','synthetic-user-a']]);
});

test('sync saves configuration then checks the remote and clears its password',async()=>{
 const ui=await app({withAccounts:true});await ui.render('settings');fillSync(ui);
 ui.handlers['sync.configure']=args=>{assert.equal(args.password,'synthetic-password');return {...writerSync(),mode:'uninitialized'};};
 ui.handlers['sync.run']=()=>({...writerSync(),changed:false});
 await submitSync(ui);
 assert.deepEqual(ui.syncCalls.map(c=>c.operation),['sync.status','sync.configure','sync.run']);
 assert.deepEqual(ui.syncCalls[1].args,{url:'https://storage.example.test/dav/',username:'synthetic-user',workspace:'career',password:'synthetic-password',automatic:true});
 assert.equal(ui.syncCalls[2].user_id,'synthetic-user-a');assert.equal(ui.el('sync-password').value,'');
 assert.match(ui.el('sync-status').textContent,/此电脑可写入/);assert.equal(ui.el('sync-handoff').disabled,false);
 assert.equal(ui.el('sync-takeover').disabled,true);assert.equal(ui.el('sync-fields').disabled,false);
});

test('saved sync credentials stay blank and unsaved destination changes prevent sync',async()=>{
 const ui=await app();ui.handlers['sync.status']=writerSync;await ui.render('settings');
 assert.equal(ui.el('sync-password').value,'');assert.equal(ui.el('sync-password').required,false);
 ui.el('sync-workspace').value='different';ui.el('sync-workspace').oninput();
 assert.equal(ui.el('sync-password').required,true);assert.equal(ui.el('sync-now').disabled,true);assert.equal(ui.el('sync-handoff').disabled,true);
 ui.el('sync-workspace').value='career';ui.el('sync-workspace').oninput();
 ui.el('sync-automatic').checked=false;ui.el('sync-automatic').onchange();assert.equal(ui.el('sync-now').disabled,true);
 ui.handlers['sync.configure']=args=>({...writerSync(),config:{...writerSync().config,automatic:args.automatic}});
 ui.handlers['sync.run']=()=>({...writerSync(),config:{...writerSync().config,automatic:false},changed:false});
 await submitSync(ui);assert.equal(ui.syncCalls.find(c=>c.operation==='sync.configure').args.password,'');
 assert.equal(ui.el('sync-now').disabled,false);
});

test('failed sync configuration retains entered values and allows correction',async()=>{
 const ui=await app();await ui.render('settings');fillSync(ui);
 ui.handlers['sync.configure']=()=>apiError('synthetic_failure','合成保存错误');
 await submitSync(ui);
 assert.equal(ui.el('sync-password').value,'synthetic-password');assert.equal(ui.el('sync-url').value,'https://storage.example.test/dav/');
 assert.equal(ui.el('sync-fields').disabled,false);assert.match(ui.el('sync-error').textContent,/配置未保存.*合成保存错误/);
 assert.equal(ui.syncCalls.some(c=>c.operation==='sync.run'),false);
});

test('saved configuration with a failed remote check clears credentials and explains the boundary',async()=>{
 const ui=await app();await ui.render('settings');fillSync(ui);
 ui.handlers['sync.configure']=()=>({...writerSync(),mode:'uninitialized'});
 ui.handlers['sync.run']=()=>apiError('sync_unavailable','合成服务不可访问');await submitSync(ui);
 assert.equal(ui.el('sync-password').value,'');assert.equal(ui.el('sync-fields').disabled,false);
 assert.match(ui.el('sync-error').textContent,/配置已保存，但同步未完成.*合成服务不可访问/);
 assert.match(ui.el('sync-status').textContent,/首次同步/);
});

test('handoff becomes read-only and takeover waits for explicit confirmation',async()=>{
 const ui=await app();ui.handlers['sync.status']=writerSync;await ui.render('settings');
 ui.handlers['sync.handoff']=()=>({...writerSync(),mode:'reader',remote_owner:null,changed:false});
 ui.handlers['sync.takeover']=()=>({...writerSync(),changed:false});
 await ui.el('sync-handoff').click();assert.match(ui.el('sync-status').textContent,/此电脑只读/);
 assert.equal(ui.el('sync-handoff').disabled,true);assert.equal(ui.el('sync-takeover').disabled,false);
 await ui.el('sync-takeover').click();assert.equal(ui.el('detail').open,true);
 assert.match(ui.el('detail-content').innerHTML,/先关闭另一台电脑/);assert.match(ui.el('detail-content').innerHTML,/不合并/);
 assert.equal(ui.syncCalls.some(c=>c.operation==='sync.takeover'),false);
 await ui.el('confirm-sync-takeover').click();assert.equal(ui.el('detail').open,false);
 assert.equal(ui.syncCalls.filter(c=>c.operation==='sync.takeover').length,1);assert.match(ui.el('sync-status').textContent,/此电脑可写入/);
});

test('sync status polling leaves edits and passwords untouched',async()=>{
 const ui=await app();ui.handlers['sync.status']=writerSync;await ui.render('settings');
 fillSync(ui,{url:'https://replacement.example.test/dav/',password:'unsaved-password'});
 ui.handlers['sync.status']=()=>({...writerSync(),mode:'reader',remote_owner:'synthetic-device-b'});
 await ui.timers.shift()();assert.equal(ui.el('sync-url').value,'https://replacement.example.test/dav/');
 assert.equal(ui.el('sync-password').value,'unsaved-password');assert.match(ui.el('sync-status').textContent,/synthetic-device-b/);
 assert.equal(ui.el('sync-now').disabled,true);
 await ui.render('applications');const before=ui.syncCalls.length;await ui.timers.shift()();assert.equal(ui.syncCalls.length,before);
});

test('disabling sync waits for confirmation and keeps the saved destination without exposing its password',async()=>{
 const ui=await app();ui.handlers['sync.status']=writerSync;await ui.render('settings');
 ui.handlers['sync.disable']=()=>({...writerSync(),configured:false,mode:'local',changed:true});
 ui.handlers['sync.status']=()=>({...writerSync(),configured:false,mode:'local'});
 await ui.el('sync-disable').click();assert.equal(ui.el('detail').open,true);
 assert.match(ui.el('detail-content').innerHTML,/保留本机资料.*后续修改不再上传/);assert.match(ui.el('detail-content').innerHTML,/交接.*冲突/);
 assert.equal(ui.syncCalls.some(c=>c.operation==='sync.disable'),false);
 await ui.el('confirm-sync-disable').click();assert.equal(ui.el('detail').open,false);
 assert.equal(ui.syncCalls.filter(c=>c.operation==='sync.disable').length,1);assert.equal(ui.el('sync-disable').disabled,true);
 assert.equal(ui.el('sync-url').value,writerSync().config.url);assert.equal(ui.el('sync-password').value,'');
 assert.equal(ui.el('sync-workspace').attributes.maxlength,'64');
});

test('account switches discard a pending sync response and never start its remote run',async()=>{
 const ui=await app({withAccounts:true}),saving=deferred();await ui.render('settings');fillSync(ui);
 ui.handlers['sync.configure']=()=>saving.promise;const pending=submitSync(ui);await tick();
 await ui.evaluate("switchAccount('synthetic-user-b')");saving.resolve(writerSync());await pending;
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.syncCalls.some(c=>c.operation==='sync.run'),false);
 assert.equal(ui.el('sync-password').value,'');assert.equal(ui.el('notice').hidden,true);
});

test('a pulled dataset reloads without the old account binding',async()=>{
 const ui=await app({withAccounts:true});ui.handlers['sync.status']=writerSync;await ui.render('settings');
 ui.handlers['sync.run']=()=>{ui.stored.user_id='synthetic-user-b';ui.stored.account={id:'synthetic-user-b',name:'合成账号乙'};return {...writerSync(),changed:true};};
 await ui.el('sync-now').click();
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');
 assert.equal(ui.calls.find(c=>c.operation==='state').user_id,undefined);
 assert.equal(ui.el('sync-password').value,'');assert.equal(ui.el('notice').hidden,true);
});

test('a successful sync followed by a failed page reload reports both outcomes',async()=>{
 const ui=await app({withAccounts:true});ui.handlers['sync.status']=writerSync;await ui.render('settings');
 ui.handlers['sync.run']=()=>({...writerSync(),changed:true});ui.handlers.state=()=>apiError('synthetic_read_error','合成读取错误');
 await ui.el('sync-now').click();
 assert.equal(ui.el('notice').hidden,false);assert.match(ui.el('notice').textContent,/同步已完成，但网页资料尚未刷新/);
});

test('whole-data migration help uses the current shared base for every account', async () => {
  const ui = await app({withAccounts:true});
  ui.stored.data_dir = "C:/合成资料/O'Brien/accounts/synthetic-user-a";
  ui.stored.database = {path:"C:/合成资料/O'Brien/career.db",base_data_dir:"C:/合成资料/O'Brien",root_dir:"C:/合成项目/O'Brien"};
  await ui.evaluate('load()');
  ui.render('settings');
  await ui.el('migration-help').click();
  const launch = () => JSON.parse(decode(ui.el('detail-content').innerHTML.match(/<pre>(.*?)<\/pre>/s)[1]));
  assert.deepEqual(launch(), ['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',"C:/合成项目/O'Brien/scripts/start.ps1",'backup','--data-dir',"C:/合成资料/O'Brien",'--output','<本人本地备份 ZIP 路径>']);
  assert.equal(ui.el('detail').open, true);
  assert.equal(ui.calls.filter(c=>!['state','account.list','otp.status'].includes(c.operation)).length, 0);

  ui.stored.database = {path:'D:/synthetic-restored/career.db',base_data_dir:'D:/synthetic-restored',root_dir:'D:/synthetic-new-project'};
  ui.stored.data_dir = 'D:/synthetic-restored/accounts/synthetic-user-b';
  await ui.evaluate("switchAccount('synthetic-user-b')");
  await ui.el('migration-help').click();
  assert.equal(launch()[5], 'D:/synthetic-new-project/scripts/start.ps1');
  assert.equal(launch()[8], 'D:/synthetic-restored');
  assert.ok(!JSON.stringify(launch()).includes('/accounts/'));
});

async function app({withAccounts=false}={}) {
  const document = new Element('document');
  document.innerHTML = '<main id="content"></main><h1 id="page-title"></h1><span id="save-state"></span>'
    + '<p id="notice" hidden></p><p id="toast" hidden></p><dialog id="editor"><h2 id="editor-title"></h2>'
    + '<form id="edit-form"><div id="editor-fields"></div><button type="submit"></button></form></dialog><button id="refresh"></button>'
    + '<dialog id="detail"><h2 id="detail-title"></h2><div id="detail-content"></div><div id="detail-actions"></div></dialog>'
    + '<dialog id="task-editor"><h2 id="task-editor-title"></h2><form id="task-form"><div id="task-editor-fields"></div><button type="submit"></button></form></dialog>'
    + (withAccounts?'<select id="account-select"></select><button id="account-new"></button><dialog id="account-editor"><form id="account-form"><input name="name"><button type="submit"></button></form></dialog>':'');
  const calls = [], syncCalls = [], timers = [], clipboard = [];
  const stored = {records: {}, documents: [], settings: {...publicDefaults, _revision: 4, schedule: {
    time: '10:00', timezone: 'Asia/Shanghai', allow_relogin: false, enabled: false, host_task_id: null,
  }}};
  const accountItems=[{id:'synthetic-user-a',name:'合成账号甲',created_at:'2026-10-09T01:00:00Z'},{id:'synthetic-user-b',name:'合成账号乙',created_at:'2026-10-09T02:00:00Z'}];
  if(withAccounts)Object.assign(stored,{account:accountItems[0],user_id:accountItems[0].id,database:{path:'C:/synthetic-local-data/career.db',base_data_dir:'C:/synthetic-local-data',root_dir:'C:/synthetic-project'}});
  const handlers = {
    state: () => structuredClone(stored),
    'otp.status': manual,
    'sync.status': localSync,
    'resource.list': () => ({items:[],registry_configured:false,remote_registry_supported:true}),
    'otp.test': () => connectionResult(),
    'settings.save': args => ({...stored.settings, ...args.settings, _revision: 5}),
    'account.list':()=>({items:structuredClone(accountItems),total:accountItems.length}),
    'account.select':args=>{stored.account=accountItems.find(a=>a.id===args.id);stored.user_id=args.id;return structuredClone(stored.account);},
  };
  const context = vm.createContext({
    document, FormData, URL, console, location: {hash: ''}, window: {addEventListener() {}},
    navigator: {clipboard: {writeText: async text => { clipboard.push(text); }}},
    setTimeout(callback, delay) {if(delay===5000)timers.push(callback);},
    fetch: async (path, options = {}) => {
      const url=new URL(path,'http://synthetic.local'),request=url.pathname==='/api/state'?{operation:'state',args:{},...(url.searchParams.has('user_id')?{user_id:url.searchParams.get('user_id')}:{})}:url.pathname==='/api/accounts'?{operation:'account.list',args:{}}:JSON.parse(options.body);
      // Keep existing per-account operation assertions independent of the
      // additional shared sync status read. Sync assertions inspect syncCalls.
      if(request.operation!=='sync.status')calls.push(request);
      if(request.operation.startsWith('sync.'))syncCalls.push(request);
      assert.ok(handlers[request.operation], `Unexpected API operation ${request.operation}`);
      const data = await handlers[request.operation](request.args);
      return data?.failure ? {ok: false, json: async () => ({ok: false, error: data.failure})}
        : {ok: true, json: async () => ({ok: true, data})};
    },
  });
  vm.runInContext(source, context, {filename: 'web/app.js'});
  await tick(); // Complete the actual startup load().
  calls.length = 0;
  const evaluate = code => vm.runInContext(code, context);
  return {
    document, calls, syncCalls, timers, handlers, clipboard, evaluate,stored,
    el: id => document.querySelector(`#${id}`),
    render: target => evaluate(`page=${JSON.stringify(target)}; render()`),
    submit: async () => {
      const form = document.querySelector('#otp-form');
      await form.onsubmit({target: form, preventDefault() {}});
    },
  };
}

function chooseGotify(ui, {url = 'http://127.0.0.1:8123', token = ''} = {}) {
  ui.el('otp-provider').value = 'gotify';
  ui.el('otp-url').value = url;
  ui.el('otp-token').value = token;
  ui.el('otp-provider').onchange();
}

test('first Gotify setup requires a client token before submitting, including loopback', async () => {
  const ui = await app();
  await ui.render('settings');
  chooseGotify(ui);
  assert.equal(ui.el('otp-url').required, true);
  assert.equal(ui.el('otp-token').required, true);
  assert.equal(ui.el('gotify-fields').hidden, false);
  await ui.submit();
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status']);
  assert.equal(ui.el('otp-provider').value, 'gotify');
});

test('saved Gotify token may be retained only for the same normalized service URL', async () => {
  const ui = await app();
  ui.handlers['otp.status'] = () => gotify();
  await ui.render('settings');
  ui.el('otp-url').value += '/';
  ui.el('otp-url').oninput();
  assert.equal(ui.el('otp-token').required, false);
  ui.handlers['otp.configure'] = () => ({});
  await ui.submit();
  assert.deepEqual(ui.calls.find(call => call.operation === 'otp.configure').args, {
    provider: 'gotify', enabled: true, url: 'http://127.0.0.1:8123/', token: '',
  });
  ui.el('otp-url').value = 'http://127.0.0.1:9000';
  ui.el('otp-url').oninput();
  assert.equal(ui.el('otp-token').required, true);
  const before = ui.calls.length;
  await ui.submit();
  assert.equal(ui.calls.length, before);
});

test('manual provider omits stale URL and token from configure request', async () => {
  const ui = await app();
  ui.handlers['otp.status'] = () => gotify();
  await ui.render('settings');
  ui.el('otp-url').value = 'invalid old URL';
  ui.el('otp-token').value = 'synthetic-old-token';
  ui.el('otp-provider').value = 'manual';
  ui.el('otp-provider').onchange();
  assert.equal(ui.el('otp-url').disabled, true);
  assert.equal(ui.el('otp-token').required, false);
  ui.handlers['otp.configure'] = () => ({});
  ui.handlers['otp.status'] = manual;
  await ui.submit();
  assert.deepEqual(ui.calls.find(call => call.operation === 'otp.configure').args, {provider: 'manual', enabled: false});
  assert.equal(ui.el('otp-provider').value, 'manual');
  assert.equal(ui.el('otp-token').value, '');
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'otp.configure', 'otp.status']);
  assert.equal(ui.el('otp-test').effectivelyDisabled(), true);
});

test('configure failure keeps the selected provider, URL and entered token available for correction', async () => {
  const ui = await app();
  await ui.render('settings');
  chooseGotify(ui, {token: 'synthetic-client-token'});
  ui.handlers['otp.configure'] = () => apiError('secret_storage_failed');
  const contentWrites = ui.el('content').htmlWrites;
  await ui.submit();
  assert.equal(ui.el('otp-provider').value, 'gotify');
  assert.equal(ui.el('otp-url').value, 'http://127.0.0.1:8123');
  assert.equal(ui.el('otp-token').value, 'synthetic-client-token');
  assert.equal(ui.el('otp-fields').disabled, false);
  assert.equal(ui.el('otp-error').hidden, false);
  assert.match(ui.el('otp-error').textContent, /未能加密/);
  assert.equal(ui.el('content').htmlWrites, contentWrites);
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'otp.configure']);
});

test('successful OTP save reads back configuration and clears token without resetting unsaved follow-up input', async () => {
  const ui = await app();
  await ui.render('settings');
  chooseGotify(ui, {token: 'synthetic-client-token'});
  const form = ui.el('schedule-form');
  form.querySelector('[name=time]').value = '17:35';
  form.querySelector('[name=timezone]').value = 'UTC';
  form.querySelector('[name=allow_relogin]').checked = true;
  ui.handlers['otp.configure'] = () => ({});
  ui.handlers['otp.status'] = () => gotify();
  await ui.submit();
  assert.equal(ui.el('schedule-form'), form);
  assert.equal(form.querySelector('[name=time]').value, '17:35');
  assert.equal(form.querySelector('[name=timezone]').value, 'UTC');
  assert.equal(form.querySelector('[name=allow_relogin]').checked, true);
  assert.equal(ui.el('otp-token').value, '');
  assert.equal(ui.el('otp-token').required, false);
  assert.equal(ui.el('otp-provider').value, 'gotify');
  assert.equal(ui.el('otp-test-status').dataset.result, 'passed');
  assert.match(ui.el('otp-test-status').textContent, /Token 可以读取消息/);
  assert.match(ui.el('otp-test-status').textContent, /手机短信转发和招聘网站取码尚未验证/);
  assert.equal(ui.evaluate('otpState.connection_test.live_login_verified'), false);
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'otp.configure', 'otp.status', 'otp.test']);
  assert.deepEqual(ui.calls.at(-1).args, {});
});

test('readback failure distinguishes successful save from unknown current state', async () => {
  const ui = await app();
  await ui.render('settings');
  chooseGotify(ui, {token: 'synthetic-client-token'});
  ui.handlers['otp.configure'] = () => ({});
  ui.handlers['otp.status'] = () => apiError('otp_storage_error');
  await ui.submit();
  assert.equal(ui.el('otp-token').value, '');
  assert.match(ui.el('otp-status').textContent, /保存请求已成功，但读取确认失败/);
  assert.equal(ui.el('otp-fields').disabled, true);
  assert.equal(ui.el('otp-error').hidden, false);
  assert.equal(ui.evaluate('otpState'), null);
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'otp.configure', 'otp.status']);
});

test('automatic connection test failure preserves saved Gotify configuration and keeps the token cleared', async () => {
  const ui = await app();
  await ui.render('settings');
  chooseGotify(ui, {token: 'synthetic-client-token'});
  ui.handlers['otp.configure'] = () => ({});
  ui.handlers['otp.status'] = () => gotify();
  ui.handlers['otp.test'] = () => connectionResult('failed');
  await ui.submit();
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'otp.configure', 'otp.status', 'otp.test']);
  assert.equal(ui.el('otp-provider').value, 'gotify');
  assert.equal(ui.el('otp-token').value, '');
  assert.equal(ui.el('otp-token').required, false);
  assert.equal(ui.el('otp-fields').disabled, false);
  assert.equal(ui.evaluate('otpState.token_configured'), true);
  assert.equal(ui.evaluate('otpState.connection_test.status'), 'failed');
  assert.match(ui.el('otp-status').textContent, /已保存/);
  assert.match(ui.el('otp-test-status').textContent, /客户端 Token 无读取权限/);
  assert.doesNotMatch(ui.el('otp-status').textContent, /读取确认失败/);
  assert.equal(ui.el('otp-test').effectivelyDisabled(), false);
});

test('connection-test API error after saving does not relabel the successful readback as failed or lose saved state', async () => {
  const ui = await app();
  await ui.render('settings');
  chooseGotify(ui, {token: 'synthetic-client-token'});
  ui.handlers['otp.configure'] = () => ({});
  ui.handlers['otp.status'] = () => gotify();
  ui.handlers['otp.test'] = () => apiError('test_unavailable', '合成测试：服务暂不可用');
  await ui.submit();
  assert.equal(ui.evaluate('otpState.token_configured'), true);
  assert.equal(ui.el('otp-token').value, '');
  assert.equal(ui.el('otp-fields').disabled, false);
  assert.match(ui.el('otp-status').textContent, /已保存/);
  assert.doesNotMatch(ui.el('otp-status').textContent, /读取确认失败/);
  assert.match(ui.el('otp-test-status').textContent, /服务暂不可用/);
});

test('test-only button calls the saved provider without reconfiguring or including secrets', async () => {
  const ui = await app();
  ui.handlers['otp.status'] = () => gotify();
  await ui.render('settings');
  assert.equal(ui.el('otp-test').effectivelyDisabled(), false);
  await ui.el('otp-test').click();
  assert.deepEqual(ui.calls, [{operation: 'otp.status', args: {},user_id:null}, {operation: 'otp.test', args: {},user_id:null}]);
  assert.equal(ui.el('otp-test-status').dataset.result, 'passed');
  assert.match(ui.el('otp-test-status').textContent, /Token 可以读取消息/);
  assert.equal(ui.el('otp-token').value, '');
});

test('unsaved provider, address or token changes disable the test button until restored or saved', async () => {
  const ui = await app();
  ui.handlers['otp.status'] = () => gotify();
  await ui.render('settings');
  const before = ui.calls.length;
  ui.el('otp-url').value = 'http://127.0.0.1:9000';
  ui.el('otp-url').oninput();
  assert.equal(ui.el('otp-test').effectivelyDisabled(), true);
  await ui.el('otp-test').click();
  assert.equal(ui.calls.length, before);
  ui.el('otp-url').value = 'http://127.0.0.1:8123/';
  ui.el('otp-url').oninput();
  assert.equal(ui.el('otp-test').effectivelyDisabled(), false);
  ui.el('otp-token').value = 'synthetic-unsaved-token';
  ui.el('otp-token').oninput();
  assert.equal(ui.el('otp-test').effectivelyDisabled(), true);
  await ui.el('otp-test').click();
  assert.equal(ui.calls.length, before);
  ui.el('otp-token').value = '';
  ui.el('otp-token').oninput();
  assert.equal(ui.el('otp-test').effectivelyDisabled(), false);
  ui.el('otp-provider').value = 'manual';
  ui.el('otp-provider').onchange();
  assert.equal(ui.el('otp-test').effectivelyDisabled(), true);
  await ui.el('otp-test').click();
  assert.equal(ui.calls.length, before);
});

test('pending connection test disables the same form and duplicate button clicks until completion', async () => {
  const ui = await app(), testing = deferred();
  ui.handlers['otp.status'] = () => gotify();
  ui.handlers['otp.test'] = () => testing.promise;
  await ui.render('settings');
  const first = ui.el('otp-test').click();
  assert.equal(ui.el('otp-fields').disabled, true);
  assert.equal(ui.el('otp-provider').effectivelyDisabled(), true);
  assert.equal(ui.el('otp-token').effectivelyDisabled(), true);
  assert.equal(ui.el('otp-form').querySelector('[type=submit]').effectivelyDisabled(), true);
  await ui.el('otp-test').click();
  assert.equal(ui.calls.filter(call => call.operation === 'otp.test').length, 1);
  testing.resolve(connectionResult());
  await first;
  assert.equal(ui.el('otp-fields').disabled, false);
  assert.equal(ui.el('otp-test').effectivelyDisabled(), false);
});

test('save keeps the form busy through readback and automatic testing and rejects overlapping saves or tests', async () => {
  const ui = await app(), saving = deferred(), testing = deferred();
  await ui.render('settings');
  chooseGotify(ui, {token: 'synthetic-client-token'});
  ui.handlers['otp.configure'] = () => saving.promise;
  ui.handlers['otp.status'] = () => gotify();
  ui.handlers['otp.test'] = () => testing.promise;
  const first = ui.submit();
  assert.equal(ui.el('otp-fields').disabled, true);
  saving.resolve({});
  await tick();
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'otp.configure', 'otp.status', 'otp.test']);
  assert.equal(ui.el('otp-fields').disabled, true);
  assert.equal(ui.el('otp-token').value, '');
  // Even direct repeated handler dispatch must not bypass the pending-operation guard.
  await ui.submit();
  await ui.el('otp-test').onclick();
  assert.equal(ui.calls.length, 4);
  testing.resolve(connectionResult());
  await first;
  assert.equal(ui.el('otp-fields').disabled, false);
  assert.equal(ui.el('otp-test').effectivelyDisabled(), false);
});

test('status readback displays the persisted redacted connection result without automatically retesting on render', async () => {
  const ui = await app();
  ui.handlers['otp.status'] = () => ({...gotify(), connection_test: connectionResult('failed')});
  await ui.render('settings');
  assert.match(ui.el('otp-test-status').textContent, /客户端 Token 无读取权限/);
  assert.equal(ui.el('otp-token').value, '');
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status']);
});

test('late connection result cannot overwrite the test state for a newly loaded configuration', async () => {
  const ui = await app(), testing = deferred();
  ui.handlers['otp.status'] = () => gotify();
  ui.handlers['otp.test'] = () => testing.promise;
  await ui.render('settings');
  const first = ui.el('otp-test').click();
  ui.handlers['otp.status'] = () => ({...gotify('http://127.0.0.1:9000'), connection_test: connectionResult()});
  await ui.render('settings');
  const newStatus = ui.el('otp-test-status').textContent;
  testing.resolve(connectionResult('failed'));
  await first;
  assert.equal(ui.el('otp-url').value, 'http://127.0.0.1:9000');
  assert.equal(ui.el('otp-test-status').textContent, newStatus);
  assert.equal(ui.evaluate('otpState.connection_test.status'), 'passed');
  assert.equal(ui.el('otp-fields').disabled, false);
});

test('late connection-test API failure after navigation does not show a stale error on another page', async () => {
  const ui = await app(), testing = deferred();
  ui.handlers['otp.status'] = () => gotify();
  ui.handlers['otp.test'] = () => testing.promise;
  await ui.render('settings');
  const first = ui.el('otp-test').click();
  await ui.render('applications');
  const newPage = ui.el('content').innerHTML;
  testing.resolve(apiError('test_unavailable', '合成测试：旧请求失败'));
  await first;
  assert.equal(ui.el('content').innerHTML, newPage);
  assert.equal(ui.el('otp-test-status'), null);
  assert.equal(ui.el('notice').hidden, true);
});

test('application filters group companies and keep contradictory raw status visible', async () => {
  const ui = await app();
  ui.evaluate(`state.records.application = [
    {id:'a1',kind:'application',data:{company:'甲公司',title:'视觉岗位',stage:'submitted',raw_status:'筛选中'}},
    {id:'a2',kind:'application',data:{company:'甲公司',title:'历史岗位',stage:'closed',raw_status:'已结束'}},
    {id:'b1',kind:'application',data:{company:'乙公司',title:'待核岗位',stage:'closed',raw_status:'新投递'}}
  ]`);
  await ui.render('applications');
  assert.match(ui.el('content').innerHTML, /显示 2 \/ 3 条岗位，2 家公司/);
  assert.equal(ui.document.querySelectorAll('[data-company-group]').length, 2);
  assert.doesNotMatch(ui.el('content').innerHTML, /历史岗位/);
  assert.match(ui.el('content').innerHTML, /待核岗位/);
  assert.match(ui.el('content').innerHTML, /状态待核对/);
  ui.el('app-progress').value = 'closed';
  ui.el('app-progress').onchange({target: ui.el('app-progress')});
  assert.match(ui.el('content').innerHTML, /历史岗位/);
  assert.doesNotMatch(ui.el('content').innerHTML, /待核岗位/);
  ui.el('app-progress').value = 'all';
  ui.el('app-progress').onchange({target: ui.el('app-progress')});
  ui.el('app-company').value = '甲公司';
  ui.el('app-company').onchange({target: ui.el('app-company')});
  assert.match(ui.el('content').innerHTML, /显示 2 \/ 3 条岗位，1 家公司/);
  assert.equal(ui.document.querySelectorAll('[data-company-group]').length, 1);
  ui.el('app-stage').value = 'closed';
  ui.el('app-stage').onchange({target: ui.el('app-stage')});
  assert.equal(ui.el('app-progress').value, 'closed');
  assert.match(ui.el('content').innerHTML, /历史岗位/);
  ui.el('app-progress').value = 'open';
  ui.el('app-progress').onchange({target: ui.el('app-progress')});
  assert.equal(ui.el('app-stage').value, '');
  assert.match(ui.el('content').innerHTML, /视觉岗位/);
});

test('source-changed test result reloads current configuration and discards stale readback after navigation', async () => {
  const changedResult = {...connectionResult('failed'), code: 'otp_source_changed', message: '合成测试：来源配置已经变化'};
  for (const scenario of ['new-gotify', 'new-manual', 'navigation']) {
    const ui = await app(), refreshed = deferred();
    ui.handlers['otp.status'] = () => ({...gotify(), connection_test: connectionResult('failed')});
    ui.handlers['otp.test'] = () => changedResult;
    await ui.render('settings');
    ui.handlers['otp.status'] = () => refreshed.promise;
    const first = ui.el('otp-test').click();
    await tick();
    assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'otp.test', 'otp.status']);
    assert.equal(ui.el('otp-fields').disabled, true);
    assert.notEqual(ui.evaluate('otpState.connection_test?.code'), 'otp_source_changed');
    const current = scenario === 'new-manual' ? manual()
      : {...gotify('http://127.0.0.1:9000'), connection_test: connectionResult()};
    if (scenario === 'navigation') {
      // A later settings page has its own configuration; the pending refresh
      // from the previous page must not replace it when that request finishes.
      ui.handlers['otp.status'] = () => current;
      await ui.render('settings');
      refreshed.resolve(manual());
    } else refreshed.resolve(current);
    await first;
    assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(otpState)')), current);
    assert.equal(ui.el('otp-provider').value, current.provider);
    assert.equal(ui.el('otp-url').value, current.url || '');
    assert.equal(ui.el('otp-test-status').dataset.result, current.connection_test?.status || 'untested');
    assert.equal(ui.el('otp-fields').disabled, false);
    if (scenario === 'navigation') {
      assert.equal(ui.el('otp-error').hidden, true);
      assert.equal(ui.el('notice').hidden, true);
    } else {
      assert.equal(ui.el('otp-error').hidden, false);
      assert.match(ui.el('otp-error').textContent, /变化|变更|改变|修改/);
    }
  }
});

test('late status response from an earlier settings render cannot overwrite a newer settings form', async () => {
  const ui = await app(), oldStatus = deferred();
  ui.handlers['otp.status'] = () => oldStatus.promise;
  const oldRender = ui.render('settings');
  ui.handlers['otp.status'] = manual;
  await ui.render('settings');
  chooseGotify(ui, {url: 'http://127.0.0.1:9999', token: 'synthetic-new-input'});
  oldStatus.resolve(gotify());
  await oldRender;
  assert.equal(ui.el('otp-url').value, 'http://127.0.0.1:9999');
  assert.equal(ui.el('otp-token').value, 'synthetic-new-input');
  assert.equal(ui.el('otp-token').required, true);
});

test('late status failure after navigating away does not touch the new page or display a stale error', async () => {
  const ui = await app(), oldStatus = deferred();
  ui.handlers['otp.status'] = () => oldStatus.promise;
  const oldRender = ui.render('settings');
  await ui.render('applications');
  const newPage = ui.el('content').innerHTML;
  oldStatus.resolve(apiError('otp_storage_error'));
  await oldRender;
  assert.equal(ui.el('content').innerHTML, newPage);
  assert.equal(ui.el('otp-status'), null);
  assert.equal(ui.el('notice').hidden, true);
});

test('late configure completion after navigation cannot clear a newly edited token or start stale readback', async () => {
  const ui = await app(), saving = deferred();
  await ui.render('settings');
  chooseGotify(ui, {token: 'synthetic-old-input'});
  ui.handlers['otp.configure'] = () => saving.promise;
  const submission = ui.submit();
  await ui.render('settings');
  chooseGotify(ui, {token: 'synthetic-new-input'});
  const callsBefore = ui.calls.length;
  saving.resolve({});
  await submission;
  assert.equal(ui.el('otp-token').value, 'synthetic-new-input');
  assert.equal(ui.el('otp-fields').disabled, false);
  assert.equal(ui.calls.length, callsBefore);
});

test('task instructions save current schedule first, then display and copy exactly the server prompt', async () => {
  const ui = await app(), saved = deferred();
  await ui.render('settings');
  const form = ui.el('schedule-form');
  form.querySelector('[name=time]').value = '16:42';
  form.querySelector('[name=timezone]').value = 'UTC';
  form.querySelector('[name=allow_relogin]').checked = true;
  ui.handlers['settings.save'] = () => saved.promise;
  const prompt = 'Synthetic server instructions <local-path> & explicit data binding';
  ui.handlers['schedule.prepare'] = () => ({prompt});
  const generating = ui.el('schedule-prompt').onclick();
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'settings.save']);
  assert.deepEqual(ui.calls[1].args, {settings: {schedule: {time: '16:42', timezone: 'UTC', allow_relogin: true}}, revision: 4});
  assert.equal(ui.el('schedule-prompt').disabled, true);
  saved.resolve({_revision: 5, schedule: {time: '16:42', timezone: 'UTC', allow_relogin: true}});
  await generating;
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'settings.save', 'schedule.prepare']);
  assert.match(ui.el('detail-content').innerHTML, /Synthetic server instructions &lt;local-path&gt; &amp; explicit data binding/);
  assert.doesNotMatch(ui.el('detail-content').innerHTML, /\$career\b/);
  assert.equal(ui.el('detail').open, true);
  assert.equal(ui.el('schedule-prompt').disabled, false);
  await ui.el('copy-prompt').onclick();
  assert.deepEqual(ui.clipboard, [prompt]);
  assert.equal(ui.evaluate('state.settings._revision'), 5);
});

test('schedule save conflict preserves current input and prevents generation from stale saved settings', async () => {
  const ui = await app();
  await ui.render('settings');
  ui.el('schedule-form').querySelector('[name=time]').value = '12:34';
  ui.handlers['settings.save'] = () => apiError('revision_conflict', 'Synthetic revision conflict');
  await ui.el('schedule-prompt').onclick();
  assert.deepEqual(ui.calls.map(call => call.operation), ['otp.status', 'settings.save']);
  assert.equal(ui.el('schedule-form').querySelector('[name=time]').value, '12:34');
  assert.equal(ui.el('notice').hidden, false);
  assert.equal(ui.el('notice').textContent, 'Synthetic revision conflict');
  assert.equal(ui.el('schedule-prompt').disabled, false);
  assert.equal(ui.el('detail').open, undefined);
});

const applicationFixture = (id, data = {}) => ({id, kind:'application', revision:3, scope:'global',
  status:'observed', source:'website', data:{company:'合成公司', title:'合成岗位', stage:'candidate', ...data}});
const setApplications = (ui, records) => ui.evaluate(`state.records.application=${JSON.stringify(records)}`);
const optionValues = element => element.children.map(option => option.value);
const change = (ui, id, value) => {const element=ui.el(id);element.value=value;return element.onchange({target:element});};
const syntheticState = records => ({records:{application:structuredClone(records)}, documents:[], settings:{}, counts:{application:records.length}});
const exclusionFixture = (id='synthetic-exclusion', data={}) => ({id,scope:'job',company:'合成公司',title:'合成岗位',job_id:'job-1',url:'https://example.test/job-1',reason:'合成删除理由',identity:'synthetic-identity',active:true,revision:2,created_at:'2026-10-10T01:00:00Z',updated_at:'2026-10-10T01:00:00Z',record_ids:['candidate'],...data});
const exclusionPage = (items, data={}) => ({items:structuredClone(items),total:items.length,limit:50,offset:0,next_offset:null,...data});
const submitExclusion = ui => {const form=ui.el('recommendation-exclusion-form');return form.onsubmit({target:form,preventDefault(){}});};
const visibleRecommendationGroups = ui => ui.document.querySelectorAll('[data-recommendation-company]').map(group=>({
  group:group.dataset.recommendationCompany,
  jobs:group.querySelectorAll('[data-prepare-recommendation]').map(button=>button.dataset.prepareRecommendation),
}));

test('recommendation companies use their best tier and jobs use tiers, independent of timestamps and input order', async () => {
  const ui=await app(),records=[
    applicationFixture('a-c',{company:'Alpha',program:'校招',job_id:'30',fit_tier:'C'}),
    applicationFixture('b-b',{company:'Beta',program:'校招',job_id:'20',fit_tier:'B'}),
    applicationFixture('a-a',{company:'Alpha',program:'校招',job_id:'10',fit_tier:'A',recommendation_scope:'仅视觉问答方向 <限定>'}),
    applicationFixture('a-u',{company:'Alpha',program:'校招',job_id:'01',fit_tier:null}),
    applicationFixture('a-b',{company:'Alpha',program:'校招',job_id:'20',fit_tier:'B'}),
  ];
  const before=structuredClone(records);
  setApplications(ui,records);await ui.render('recommendations');
  const expected=[{group:'pending|Alpha',jobs:['a-a','a-b','a-c','a-u']},{group:'pending|Beta',jobs:['b-b']}];
  assert.deepEqual(visibleRecommendationGroups(ui),expected);
  assert.match(ui.el('content').innerHTML,/最佳岗位A档（限定范围）/);
  assert.match(ui.el('content').innerHTML,/仅视觉问答方向 &lt;限定&gt;/);
  assert.match(ui.el('content').innerHTML,/同梯队并列/);
  assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),before);
  setApplications(ui,records.toReversed().map((r,index)=>({...r,updated_at:`2030-01-0${index+1}`,data:{...r.data,notes:'旧记载第1名A档，不用它推断梯队'}})));
  await ui.render('recommendations');
  assert.deepEqual(visibleRecommendationGroups(ui),expected);
  assert.equal(ui.calls.length,0);
});

test('same-tier jobs are parallel with stable identities; adding one does not modify other records', async () => {
  const ui=await app(),records=[
    applicationFixture('two',{company:'Alpha',program:'校招',job_id:'02',fit_tier:'B'}),
    applicationFixture('one',{company:'Alpha',program:'校招',job_id:'01',fit_tier:'B'}),
  ];
  setApplications(ui,records);await ui.render('recommendations');
  assert.deepEqual(visibleRecommendationGroups(ui),[{group:'pending|Alpha',jobs:['one','two']}]);
  const added=applicationFixture('middle',{company:'Alpha',program:'校招',job_id:'015',fit_tier:'B'});
  setApplications(ui,[...records,added]);await ui.render('recommendations');
  assert.deepEqual(visibleRecommendationGroups(ui),[{group:'pending|Alpha',jobs:['one','middle','two']}]);
  assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application.slice(0,2))')),records);
  assert.doesNotMatch(ui.el('content').innerHTML,/第\d+名|rank_global|rank_within_company/);
  assert.equal(ui.calls.length,0);
});

test('qualification partitions come before tiers and keep repeated company groups independently collapsible', async () => {
  const ui=await app();
  setApplications(ui,[
    applicationFixture('pending',{company:'Alpha',program:'校招',fit_tier:'A',recommendation_bucket:'pending'}),
    applicationFixture('excluded',{company:'Alpha',program:'校招',fit_tier:'A',recommendation_bucket:'excluded'}),
    applicationFixture('recommended',{company:'Beta',program:'校招',fit_tier:'B',recommendation_bucket:'recommended'}),
    applicationFixture('exploratory',{company:'Alpha',program:'校招',fit_tier:'C',recommendation_bucket:'exploratory'}),
  ]);await ui.render('recommendations');
  assert.deepEqual(visibleRecommendationGroups(ui),[
    {group:'recommended|Beta',jobs:['recommended']},{group:'exploratory|Alpha',jobs:['exploratory']},
    {group:'pending|Alpha',jobs:['pending']},{group:'excluded|Alpha',jobs:['excluded']},
  ]);
  assert.match(ui.el('content').innerHTML,/显示 4 \/ 4 条正式校招，2 家公司/);
  const group=ui.document.querySelectorAll('[data-recommendation-company]')[1];
  group.open=false;group.ontoggle();await ui.render('recommendations');
  const collapsed=ui.document.querySelectorAll('[data-recommendation-company]').filter(el=>!('open' in el.attributes));
  assert.deepEqual(collapsed.map(el=>el.dataset.recommendationCompany),['exploratory|Alpha']);
});

test('company best tier uses only jobs visible after type, city and search filters', async () => {
  const ui=await app();
  setApplications(ui,[
    applicationFixture('hidden-intern',{company:'Alpha',title:'隐藏A实习',program:'实习',city:'武汉',fit_tier:'A'}),
    applicationFixture('hidden-city',{company:'Alpha',title:'异城A',program:'校招',city:'北京',fit_tier:'A'}),
    applicationFixture('alpha',{company:'Alpha',title:'共同研究C',program:'校招',city:'武汉',fit_tier:'C'}),
    applicationFixture('beta',{company:'Beta',title:'共同研究B',program:'校招',city:'武汉',fit_tier:'B'}),
  ]);await ui.render('recommendations');
  assert.equal(visibleRecommendationGroups(ui)[0].group,'pending|Alpha');
  change(ui,'recommend-city','武汉');
  assert.deepEqual(visibleRecommendationGroups(ui),[{group:'pending|Beta',jobs:['beta']},{group:'pending|Alpha',jobs:['alpha']}]);
  change(ui,'recommend-city','');
  const input=ui.el('recommend-query');input.value='共同研究';input.oninput({target:input});
  assert.deepEqual(visibleRecommendationGroups(ui),[{group:'pending|Beta',jobs:['beta']},{group:'pending|Alpha',jobs:['alpha']}]);
});

test('legacy recommendation notes and null metadata never fabricate a tier or eligibility', async () => {
  const ui=await app(),records=[
    applicationFixture('legacy',{company:'Alpha',program:'校招',notes:'A档，待核队列第1'}),
    applicationFixture('null',{company:'Beta',program:'校招',fit_tier:null,recommendation_bucket:null,recommendation_scope:null}),
  ];setApplications(ui,records);await ui.render('recommendations');
  assert.deepEqual(visibleRecommendationGroups(ui),[{group:'pending|Alpha',jobs:['legacy']},{group:'pending|Beta',jobs:['null']}]);
  assert.equal((ui.el('content').innerHTML.match(/最佳岗位未分档/g)||[]).length,2);
  assert.doesNotMatch(ui.el('content').innerHTML,/data-recommendation-bucket="recommended"|A · 直接匹配/);
  assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),records);
});

test('recommendations have a registered navigation entry and do not trigger any record mutation on entry', async () => {
  const index = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
  assert.match(index, /<button data-page="recommendations">推荐岗位<\/button>/);
  const ui=await app();
  await ui.evaluate("navigate('recommendations')");
  assert.equal(ui.el('page-title').textContent,'推荐岗位');
  assert.equal(ui.el('recommend-type').value,'campus');
  assert.deepEqual(ui.calls.map(call=>call.operation),['state']);
});

test('all application views, company choices and stage filters exclude every unsubmitted record', async () => {
  const ui=await app();
  setApplications(ui,[
    applicationFixture('candidate',{company:'仅候选公司',title:'候选隔离岗位',program:'校园招聘'}),
    applicationFixture('mixed',{company:'合成公司',title:'同公司候选隔离岗位'}),
    applicationFixture('preparing',{title:'实际准备岗位',stage:'preparing'}),
    applicationFixture('profile-saved',{company:'仅候选公司',title:'仅保存简历岗位',stage:'profile_saved'}),
    applicationFixture('unknown-stage',{company:'仅候选公司',title:'投递证据未知岗位',stage:'unknown'}),
    applicationFixture('submitted',{title:'实际投递岗位',stage:'submitted',raw_status:'筛选中'}),
    applicationFixture('closed',{title:'实际结束岗位',stage:'closed',raw_status:'已结束'})
  ]);
  for(const [scope,shown] of [['open',1],['all',2],['closed',1]]){
    ui.evaluate(`progressFilter=${JSON.stringify(scope)};stageFilter=''`);
    await ui.render('applications');
    assert.match(ui.el('content').innerHTML,new RegExp(`显示 ${shown} / 2 条岗位`));
    assert.doesNotMatch(ui.el('content').innerHTML,/候选隔离岗位|仅候选公司|实际准备岗位|仅保存简历岗位|投递证据未知岗位/);
    for(const stage of ['candidate','preparing','profile_saved','unknown'])assert.ok(!optionValues(ui.el('app-stage')).includes(stage));
    assert.ok(!optionValues(ui.el('app-progress')).includes('preparing'));
    assert.ok(!optionValues(ui.el('app-company')).includes('仅候选公司'));
  }
  for(const stage of ['preparing','submitted','closed','unknown','candidate']){
    ui.evaluate(`progressFilter='all';stageFilter=${JSON.stringify(stage)}`);
    await ui.render('applications');
    assert.doesNotMatch(ui.el('content').innerHTML,/候选隔离岗位|仅候选公司/);
  }
});

test('overview application count and recent applications exclude candidates while formal recommendations are separate', async () => {
  const ui=await app();
  const campus=applicationFixture('campus',{title:'未选校招候选',program:'2027校园招聘'});
  campus.status='unconfirmed';
  setApplications(ui,[campus,applicationFixture('intern',{title:'未选实习候选',program:'实习生招聘'}),
    applicationFixture('applied',{title:'已选申请岗位',stage:'submitted'})]);
  await ui.render('overview');
  assert.match(ui.el('content').innerHTML,/投递记录<\/div><div class="number">1<\/div>/);
  assert.match(ui.el('content').innerHTML,/正式校招推荐<\/div><div class="number">1<\/div>/);
  assert.match(ui.el('content').innerHTML,/已选申请岗位/);
  assert.doesNotMatch(ui.el('content').innerHTML,/未选校招候选|未选实习候选|条资料待确认/);
  setApplications(ui,[campus]);
  await ui.render('overview');
  assert.match(ui.el('content').innerHTML,/投递记录<\/div><div class="number">0<\/div>/);
  assert.match(ui.el('content').innerHTML,/还没有申请记录/);
});

test('recommendations default to explicit campus jobs, preserve full escaped notes and separate internship, experienced and unknown types', async () => {
  const ui=await app(),notes='匹配理由：合成多模态研究。\n资格限制：毕业时间待核对。<script>not-code</script>';
  const records=[
    applicationFixture('campus',{title:'正式校招算法岗位',program:'Plan A 2027校招',notes,checked_at:'2026-10-09T01:00:00Z',coverage:'partial',evidence_url:'https://example.test/job'}),
    applicationFixture('campus2',{title:'正式校招研究岗位',program:'2027校园招聘'}),
    applicationFixture('intern',{title:'仅实习岗位',program:'校园招聘（实习）'}),
    applicationFixture('intern2',{title:'Research Intern',program:null}),
    applicationFixture('unknown',{title:'博士大模型岗位',program:'TGT',notes:'官网类型尚未确认，不能当作校招'}),
    applicationFixture('experienced',{title:'社会招聘岗位',program:'正式社会招聘'}),
    applicationFixture('selected',{title:'已进入准备岗位',stage:'preparing',program:'校园招聘'})
  ];
  setApplications(ui,records);await ui.render('recommendations');
  assert.equal(ui.el('recommend-type').value,'campus');
  assert.match(ui.el('content').innerHTML,/显示 3 \/ 3 条正式校招，1 家公司。共保留 7 条候选记录/);
  assert.match(ui.el('content').innerHTML,/正式校招算法岗位|正式校招研究岗位/);
  assert.doesNotMatch(ui.el('content').innerHTML,/仅实习岗位|Research Intern|博士大模型岗位|社会招聘岗位/);
  assert.match(ui.el('content').innerHTML,/已进入准备岗位/);
  assert.ok(ui.el('content').innerHTML.includes(notes.replace('<script>','&lt;script&gt;').replace('</script>','&lt;/script&gt;')));
  assert.match(ui.el('content').innerHTML,/岗位信息最后核验：[\s\S]*部分页面/);
  assert.equal(ui.document.querySelectorAll('[data-recommendation-company]').length,1);
  assert.equal(ui.document.querySelectorAll('[data-prepare-recommendation]').length,3);
  change(ui,'recommend-type','internship');
  assert.match(ui.el('content').innerHTML,/仅实习岗位|Research Intern/);
  assert.doesNotMatch(ui.el('content').innerHTML,/正式校招算法岗位|博士大模型岗位/);
  change(ui,'recommend-type','unknown');
  assert.match(ui.el('content').innerHTML,/博士大模型岗位/);
  assert.doesNotMatch(ui.el('content').innerHTML,/仅实习岗位|正式校招算法岗位/);
  change(ui,'recommend-type','experienced');
  assert.match(ui.el('content').innerHTML,/社会招聘岗位/);
  assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),records);
  assert.equal(ui.calls.length,0);
});

test('recommendation company, split multi-city and notes search filters compose without enabling internships', async () => {
  const ui=await app();
  setApplications(ui,[
    applicationFixture('a',{company:'甲合成',title:'合成武汉研究岗',city:'武汉/北京、合肥',program:'校园招聘',notes:'需要确认毕业月份'}),
    applicationFixture('b',{company:'乙合成',title:'合成武汉工程岗',city:'深圳，武汉；上海 南京',program:'校招'}),
    applicationFixture('c',{company:'甲合成',title:'合成未知城市岗',city:'未知/等9城',program:'校招'}),
    applicationFixture('d',{company:'甲合成',title:'隐藏的武汉实习',city:'武汉',program:'实习'})
  ]);await ui.render('recommendations');
  const cities=optionValues(ui.el('recommend-city'));
  for(const city of ['武汉','北京','合肥','深圳','上海','南京','未知','等9城'])assert.ok(cities.includes(city));
  assert.ok(!cities.includes('武汉/北京、合肥'));
  change(ui,'recommend-city','武汉');
  assert.match(ui.el('content').innerHTML,/显示 2 \/ 3 条正式校招，2 家公司/);
  assert.doesNotMatch(ui.el('content').innerHTML,/合成未知城市岗|隐藏的武汉实习/);
  change(ui,'recommend-company','甲合成');
  assert.match(ui.el('content').innerHTML,/显示 1 \/ 3 条正式校招，1 家公司/);
  const input=ui.el('recommend-query');input.value='毕业月份';input.selectionStart=4;input.oninput({target:input});
  assert.match(ui.el('content').innerHTML,/合成武汉研究岗/);
  assert.doesNotMatch(ui.el('content').innerHTML,/合成武汉工程岗|隐藏的武汉实习/);
  assert.equal(ui.el('recommend-query').selectionStart,4);
  change(ui,'recommend-type','internship');
  assert.equal(ui.el('recommend-company').value,'');
  assert.equal(ui.el('recommend-city').value,'');
});

test('new recommendation and actual-application editors default to candidate and user-reported without preparation stages', async () => {
  const ui=await app();
  for(const [target,expected] of [['recommendations','candidate'],['applications','user_reported']]){
    await ui.render(target);
    const button=ui.document.querySelectorAll('[data-edit]').find(item=>item.dataset.edit==='application'&&!item.dataset.id);
    button.click();
    assert.equal(ui.el('editor-fields').querySelector('[name=stage]').value,expected);
    for(const oldStage of ['preparing','profile_saved','unknown'])assert.ok(!optionValues(ui.el('editor-fields').querySelector('[name=stage]')).includes(oldStage));
    assert.equal(ui.el('editor').open,true);
  }
});

test('legacy preparation and unknown stages stay recommended without using login, titles or check times as submission evidence',async()=>{
 const ui=await app(),stages=['candidate','preparing','profile_saved','unknown',null,undefined],records=stages.map((stage,i)=>{
  const record=applicationFixture(`legacy-${i}`,{title:`未投岗位 ${i}`,program:'校招',stage,raw_status:'登录成功，简历已保存',checked_at:'2026-10-12T01:00:00Z',recommendation_bucket:'recommended',fit_tier:'A'});
  if(stage===undefined)delete record.data.stage;return record;
 });
 records[3].data.title='标题写着已投却没有投递证据';records[3].data.application_id='draft-form-id';
 setApplications(ui,records);await ui.render('recommendations');assert.equal(ui.document.querySelectorAll('[data-prepare-recommendation]').length,6);
 assert.equal(ui.document.querySelectorAll('[data-recommendation-bucket=pending]')[0].querySelectorAll('[data-prepare-recommendation]').length,3);
 await ui.render('applications');assert.equal(ui.document.querySelectorAll('[data-edit]').filter(b=>b.dataset.id).length,0);assert.match(ui.el('content').innerHTML,/显示 0 \/ 0 条岗位/);
 await ui.render('overview');assert.match(ui.el('content').innerHTML,/投递记录<\/div><div class="number">0<\/div>/);assert.match(ui.el('content').innerHTML,/正式校招推荐<\/div><div class="number">6<\/div>/);
 assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),records);assert.equal(ui.calls.length,0);
});

test('editing old preparation or saved-resume recommendations stores candidate and preserves material references and evidence',async()=>{
 for(const stage of ['preparing','profile_saved']){
  const ui=await app(),legacy={...applicationFixture('legacy',{stage,company:'合成公司',program:'校招',raw_status:'本人未投，门户简历已保存',checked_at:'2026-10-12T01:00:00Z',notes:'材料已整理',resume_material:{document_id:'synthetic-local-document',projects:2,papers:3},legacy_unknown:{keep:true}}),scope:'company:synthetic',source:'official_observation',status:'observed'};let server=[legacy];
  ui.handlers.state=()=>syntheticState(server);ui.handlers['record.save']=args=>{server=[{...legacy,data:args.data,revision:4}];return server[0];};setApplications(ui,server);await ui.render('recommendations');
  ui.evaluate("openEdit('application','legacy')");assert.equal(ui.el('editor-fields').querySelector('[name=stage]').value,'candidate');
  ui.el('editor-fields').querySelector('[name=notes]').value='补充准备说明';await ui.evaluate('saveEdit({target:document.querySelector("#edit-form"),preventDefault(){}})');
  const save=ui.calls.find(c=>c.operation==='record.save');assert.equal(save.args.revision,3);assert.equal(save.args.scope,legacy.scope);assert.equal(save.args.status,legacy.status);assert.equal(server[0].data.stage,'candidate');
  for(const key of ['resume_material','legacy_unknown','raw_status','checked_at'])assert.deepEqual(server[0].data[key],legacy.data[key]);
  assert.equal(ui.evaluate('applicationRecords().length'),0);assert.equal(ui.document.querySelectorAll('[data-prepare-recommendation]').length,1);
 }
});

test('editing a candidate keeps omitted metadata absent and preserves existing tiers and explicit nulls', async () => {
  const ui=await app(),legacy=applicationFixture('edit-legacy',{program:'校招',notes:'原备注'});
  let server=[legacy];
  ui.handlers.state=()=>syntheticState(server);
  ui.handlers['record.save']=args=>{server=[{...server[0],data:args.data,revision:server[0].revision+1}];return server[0]};
  setApplications(ui,server);await ui.render('recommendations');
  ui.evaluate("openEdit('application','edit-legacy')");
  ui.el('editor-fields').querySelector('[name=notes]').value='仅改备注';
  await ui.evaluate('saveEdit({target:document.querySelector("#edit-form"),preventDefault(){}})');
  for(const key of ['recommendation_bucket','fit_tier','recommendation_scope'])assert.ok(!Object.hasOwn(server[0].data,key));
  assert.equal(server[0].data.notes,'仅改备注');
  server=[{...server[0],data:{...server[0].data,recommendation_bucket:'pending',fit_tier:'B',recommendation_scope:null}}];
  setApplications(ui,server);await ui.render('recommendations');
  assert.match(ui.el('content').innerHTML,/评估范围待确认|最佳岗位B档（范围待确认）/);
  ui.evaluate("openEdit('application','edit-legacy')");
  ui.el('editor-fields').querySelector('[name=notes]').value='再改备注';
  await ui.evaluate('saveEdit({target:document.querySelector("#edit-form"),preventDefault(){}})');
  assert.equal(server[0].data.recommendation_bucket,'pending');
  assert.equal(server[0].data.fit_tier,'B');
  assert.equal(server[0].data.recommendation_scope,null);
});

test('preparing recommendation materials keeps candidate, preserves facts and opens editing without implying an application', async () => {
  const ui=await app(),record=applicationFixture('select',{company:'合成选择公司',title:'合成选定岗位',program:'校园招聘',
    raw_status:'官网原文未改',checked_at:'2026-10-09T01:00:00Z',last_attempt_at:null,coverage:'partial',
    url:'https://example.test/position',job_id:'00042',application_id:null,decision:'原有本人决定',notes:'资格限制全文',
    recommendation_bucket:'pending',fit_tier:'B',recommendation_scope:'仅视觉研究方向',legacy_unknown:{retained:true}});
  let server=[record];
  ui.handlers.state=()=>syntheticState(server);
  ui.handlers['record.save']=args=>{server=[{...record,data:args.data,revision:4}];return server[0];};
  setApplications(ui,server);await ui.render('recommendations');
  assert.equal(ui.calls.length,0);
  await ui.document.querySelector('[data-prepare-recommendation]').click();
  const save=ui.calls.find(call=>call.operation==='record.save');
  assert.deepEqual(save.args,{kind:'application',id:record.id,revision:3,data:{...record.data,stage:'candidate',decision:'原有本人决定\n已选择整理该岗位的申请资料，等待本人审阅后决定投递。'},scope:'global',status:'observed',source:'user_edit'});
  assert.deepEqual(ui.calls.map(call=>call.operation),['record.save','state']);
  assert.equal(ui.evaluate('page'),'recommendations');
  assert.match(ui.el('content').innerHTML,/合成选定岗位/);
  assert.equal(ui.el('editor').open,true);assert.equal(ui.el('editor-fields').querySelector('[name=stage]').value,'candidate');
  assert.equal(ui.evaluate('applicationRecords().length'),0);
  await ui.render('recommendations');
  assert.equal(ui.document.querySelectorAll('[data-prepare-recommendation]').length,1);
  assert.equal(server[0].data.application_id,null);
  assert.equal(server[0].data.raw_status,record.data.raw_status);
});

test('preparation keeps same-company recommendations until actual submission clears them without manual deletion',async()=>{
 const ui=await app(),selected=applicationFixture('selected',{company:'合成准备公司',title:'选定岗位',program:'校招'}),sibling=applicationFixture('sibling',{company:'合成准备公司',title:'同公司其他岗位',program:'校招'}),other=applicationFixture('other',{company:'合成其他公司',title:'其他公司推荐',program:'校招'}),ended=applicationFixture('ended',{company:'合成准备公司',stage:'closed',raw_status:'历史结束原文'});
 let server=[selected,sibling,other,ended];setApplications(ui,server);await ui.render('recommendations');
 ui.handlers['record.save']=args=>{const saved={...selected,data:args.data,revision:server[0].revision+1};server=args.data.stage==='user_reported'?[saved,other,ended]:[saved,sibling,other,ended];return saved;};
 ui.handlers.state=()=>({...syntheticState(server),recommendation_activity:{items:server[0].data.stage==='user_reported'?[{company:'合成准备公司',company_key:'合成准备公司',record_ids:['selected'],stages:['user_reported']}]:[],total:server[0].data.stage==='user_reported'?1:0,limit:200,offset:0,next_offset:null,suppressed_candidates:0,suppressed_recommendations:0}});
 ui.handlers['recommendation.exclusions']=()=>exclusionPage([]);
 await ui.document.querySelector('[data-prepare-recommendation=selected]').click();
 assert.equal(ui.evaluate('page'),'recommendations');assert.match(ui.el('content').innerHTML,/选定岗位|同公司其他岗位/);
 assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),server);assert.equal(server.find(r=>r.id==='ended').data.raw_status,'历史结束原文');
 assert.equal(ui.document.querySelectorAll('[data-prepare-recommendation]').length,3);
 ui.el('editor-fields').querySelector('[name=stage]').value='user_reported';
 await ui.evaluate('saveEdit({target:document.querySelector("#edit-form"),preventDefault(){}})');
 await ui.render('recommendations');assert.deepEqual(visibleRecommendationGroups(ui),[{group:'pending|合成其他公司',jobs:['other']}]);
 assert.match(ui.el('content').innerHTML,/实际投递后，该公司未投岗位会自动移出推荐；已结束历史继续保留/);
 await ui.el('recommendation-exclusions').click();assert.match(ui.el('detail-content').innerHTML,/暂无删除记录/);
 assert.match(ui.el('detail-content').innerHTML,/这里只记录你手动删除的推荐/);assert.equal(ui.calls.some(c=>c.operation==='recommendation.exclude'),false);
 await ui.render('applications');assert.match(ui.el('content').innerHTML,/选定岗位/);assert.doesNotMatch(ui.el('content').innerHTML,/同公司其他岗位|其他公司推荐/);
});

test('read-only suppressed candidate counts use visible pagination and preserve the active application table',async()=>{
 const ui=await app(),active=applicationFixture('active',{company:'合成在投公司',stage:'submitted',application_id:'observed-application',raw_status:'官网处理中'}),first=applicationFixture('first',{company:'合成新公司',program:'校招'}),second=applicationFixture('second',{company:'合成新公司',program:'校招'}),visible=[active,first,second];
 ui.handlers.state=()=>({...syntheticState(visible),counts:{application:6},records:{application:[active,first]},recommendation_activity:{items:[{company:'合成在投公司',company_key:'合成在投公司',record_ids:['active'],stages:['submitted']}],total:1,limit:200,offset:0,next_offset:null,suppressed_candidates:1,suppressed_recommendations:3}});
 ui.handlers['record.list']=args=>({items:visible.slice(args.offset,args.offset+args.limit),total:visible.length});
 await ui.evaluate('load()');assert.deepEqual(ui.calls.find(c=>c.operation==='record.list').args,{kind:'application',limit:200,offset:2});
 assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),visible);assert.equal(ui.evaluate('applicationLoadInfo.complete'),true);assert.equal(ui.evaluate('applicationLoadInfo.expected'),3);
 await ui.render('recommendations');assert.equal(ui.document.querySelectorAll('[data-prepare-recommendation]').length,2);assert.doesNotMatch(ui.el('content').innerHTML,/记录未全部读取|合成在投公司/);
 await ui.render('applications');assert.match(ui.el('content').innerHTML,/合成在投公司|官网处理中/);assert.doesNotMatch(ui.el('content').innerHTML,/记录未全部读取/);
 assert.equal(ui.calls.some(c=>c.operation.startsWith('recommendation.')),false);
});

test('recommendation deletion includes legacy unsubmitted preparation, stays explicit and escapes the selected job',async()=>{
 const ui=await app(),candidate=applicationFixture('candidate',{company:'<合成公司>',title:'<script>合成标题</script>',program:'校招'}),prepared=applicationFixture('prepared',{stage:'preparing',program:'校招'}),submitted=applicationFixture('submitted',{stage:'submitted',program:'校招'});
 setApplications(ui,[candidate,prepared,submitted]);await ui.render('applications');
 assert.equal(ui.document.querySelectorAll('[data-exclude-recommendation]').length,0);
 await ui.render('recommendations');assert.equal(ui.document.querySelectorAll('[data-exclude-recommendation]').length,2);
 await ui.document.querySelector('[data-exclude-recommendation=candidate]').click();
 assert.equal(ui.el('detail').open,true);assert.equal(ui.el('recommendation-exclusion-form').querySelector('[name=scope]').value,'job');
 assert.match(ui.el('detail-content').innerHTML,/&lt;合成公司&gt;/);assert.match(ui.el('detail-content').innerHTML,/&lt;script&gt;合成标题&lt;\/script&gt;/);
 assert.match(ui.el('detail-content').innerHTML,/已投递记录继续保留/);assert.equal(ui.calls.length,0);
});

test('company exclusion removes all unsubmitted recommendations while preserving actual application facts',async()=>{
 const ui=await app({withAccounts:true}),candidate=applicationFixture('candidate',{company:'合成甲',program:'校招',raw_status:'官网原文',notes:'资格全文'}),other=applicationFixture('other',{company:'合成甲',program:'校招'}),prepared=applicationFixture('prepared',{company:'合成甲',stage:'preparing'}),submitted=applicationFixture('submitted',{company:'合成甲',stage:'submitted',application_id:'verified-1'});
 let server=[candidate,other,prepared,submitted];ui.handlers.state=()=>({...syntheticState(server),user_id:'synthetic-user-a'});
 ui.handlers['recommendation.exclude']=args=>{server=[submitted];return {exclusion:exclusionFixture('rule',{scope:args.scope,reason:args.reason,record_ids:['candidate','other','prepared']}),removed:[{...candidate,archived:true,revision:4},{...other,archived:true,revision:4},{...prepared,archived:true,revision:4}],already_excluded:false};};
 setApplications(ui,server);await ui.render('recommendations');await ui.document.querySelector('[data-exclude-recommendation]').click();
 const form=ui.el('recommendation-exclusion-form');form.querySelector('[name=scope]').value='company';form.querySelector('[name=reason]').value='  以多模态为主，暂不考虑此公司  ';
 await submitExclusion(ui);
 const request=ui.calls.find(c=>c.operation==='recommendation.exclude');assert.deepEqual(request,{operation:'recommendation.exclude',user_id:'synthetic-user-a',args:{id:'candidate',revision:3,scope:'company',reason:'以多模态为主，暂不考虑此公司'}});
 assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),[submitted]);assert.equal(ui.el('detail').open,false);
 assert.equal(ui.document.querySelectorAll('[data-exclude-recommendation]').length,0);assert.match(ui.el('toast').textContent,/公司排除规则/);
 assert.equal(ui.calls.some(c=>c.operation==='record.archive'||c.operation==='record.save'),false);
});

test('failed or duplicate deletion preserves the candidate, selected scope and reason for correction',async()=>{
 const ui=await app(),pending=deferred(),candidate=applicationFixture('candidate',{program:'校招'});setApplications(ui,[candidate]);await ui.render('recommendations');
 ui.handlers['recommendation.exclude']=()=>pending.promise;await ui.document.querySelector('[data-exclude-recommendation]').click();
 const form=ui.el('recommendation-exclusion-form'),button=form.querySelector('[type=submit]');form.querySelector('[name=scope]').value='company';form.querySelector('[name=reason]').value='保留我的原因 <限定>';
 const first=submitExclusion(ui);await submitExclusion(ui);assert.equal(button.disabled,true);assert.equal(ui.calls.filter(c=>c.operation==='recommendation.exclude').length,1);
 pending.resolve(apiError('revision_conflict','合成版本冲突，请刷新'));await first;
 assert.equal(button.disabled,false);assert.equal(ui.el('detail').open,true);assert.equal(form.querySelector('[name=scope]').value,'company');assert.equal(form.querySelector('[name=reason]').value,'保留我的原因 <限定>');
 assert.equal(ui.el('recommendation-exclusion-error').textContent,'合成版本冲突，请刷新');assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),[candidate]);
});

test('a successful exclusion survives failed refresh without describing its persisted rule as a failed deletion',async()=>{
 const ui=await app(),candidate=applicationFixture('candidate',{program:'校招'}),prepared=applicationFixture('prepared',{stage:'preparing'});setApplications(ui,[candidate,prepared]);await ui.render('recommendations');
 ui.handlers['recommendation.exclude']=()=>({exclusion:exclusionFixture(),removed:[{...candidate,archived:true,revision:4}]});ui.handlers.state=()=>apiError('unavailable','合成读取失败');
 await ui.document.querySelector('[data-exclude-recommendation]').click();await submitExclusion(ui);
 assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),[prepared]);assert.equal(ui.document.querySelectorAll('[data-exclude-recommendation]').length,0);
 change(ui,'recommend-type','unknown');assert.equal(ui.document.querySelectorAll('[data-exclude-recommendation]').length,1);
 assert.match(ui.el('notice').textContent,/推荐已删除，排除规则已保存；列表刷新失败/);assert.equal(ui.el('detail').open,false);
});

test('an invalid exclusion response never hides an actual submitted record',async()=>{
 const ui=await app(),candidate=applicationFixture('candidate',{program:'校招'}),prepared=applicationFixture('prepared',{stage:'submitted'});setApplications(ui,[candidate,prepared]);await ui.render('recommendations');
 ui.handlers['recommendation.exclude']=()=>({exclusion:exclusionFixture(),removed:[{...prepared,archived:true,revision:4}]});
 await ui.document.querySelector('[data-exclude-recommendation]').click();await submitExclusion(ui);
 assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),[candidate,prepared]);assert.match(ui.el('recommendation-exclusion-error').textContent,/返回结果无法确认/);
 assert.equal(ui.calls.filter(c=>c.operation==='state').length,0);
});

test('account switches discard late exclusion success and close its editing form',async()=>{
 const ui=await app({withAccounts:true}),pending=deferred(),candidate=applicationFixture('candidate',{program:'校招'});setApplications(ui,[candidate]);await ui.render('recommendations');
 ui.handlers['recommendation.exclude']=()=>pending.promise;await ui.document.querySelector('[data-exclude-recommendation]').click();const saving=submitExclusion(ui);await tick();
 await ui.evaluate("switchAccount('synthetic-user-b')");pending.resolve({exclusion:exclusionFixture(),removed:[{...candidate,archived:true,revision:4}]});await saving;
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.el('detail').open,false);assert.equal(ui.el('notice').hidden,true);assert.equal(ui.el('toast').textContent,'已切换本地账号');
 assert.equal(ui.calls.filter(c=>c.operation==='recommendation.exclude').length,1);assert.equal(ui.calls.find(c=>c.operation==='recommendation.exclude').user_id,'synthetic-user-a');
});

test('exclusion history is paged, escaped and read-only until a restore is explicitly chosen',async()=>{
 const ui=await app({withAccounts:true}),active=exclusionFixture('active',{scope:'company',company:'<公司>',reason:'<script>合成理由</script>\n第二行'}),inactive=exclusionFixture('inactive',{active:false});await ui.render('recommendations');
 ui.handlers['recommendation.exclusions']=args=>args.offset===0?exclusionPage([active,inactive],{total:51,next_offset:50}):exclusionPage([inactive],{total:51,offset:50});
 await ui.el('recommendation-exclusions').click();assert.deepEqual(ui.calls[0],{operation:'recommendation.exclusions',args:{include_inactive:true,limit:50,offset:0},user_id:'synthetic-user-a'});
 assert.match(ui.el('detail-content').innerHTML,/&lt;公司&gt; · 整家公司/);assert.match(ui.el('detail-content').innerHTML,/&lt;script&gt;合成理由&lt;\/script&gt;\n第二行/);
 assert.equal(ui.document.querySelectorAll('[data-restore-recommendation]').length,1);assert.equal(ui.calls.some(c=>c.operation==='recommendation.restore'),false);
 await ui.el('recommendation-exclusions-next').click();assert.equal(ui.calls.at(-1).args.offset,50);assert.equal(ui.el('recommendation-exclusions-next').disabled,true);
 await ui.el('recommendation-exclusions-prev').click();assert.equal(ui.calls.at(-1).args.offset,0);
});

test('restoring a rule uses its optimistic revision and preserves another exclusion and selected applications',async()=>{
 const ui=await app({withAccounts:true}),rule=exclusionFixture('rule',{record_ids:['candidate','still-excluded']}),restored=applicationFixture('candidate',{program:'校招',notes:'资格原文',raw_status:'未申请',archived:false}),prepared=applicationFixture('prepared',{stage:'preparing'});let active=true,server=[prepared];
 setApplications(ui,server);await ui.render('recommendations');ui.handlers.state=()=>({...syntheticState(server),user_id:'synthetic-user-a'});
 ui.handlers['recommendation.exclusions']=()=>exclusionPage([{...rule,active}]);ui.handlers['recommendation.restore']=()=>{active=false;server=[prepared,restored];return {exclusion:{...rule,active:false,revision:3},restored:[{...restored,archived:false}],remaining_exclusions:['another-rule']};};
 await ui.el('recommendation-exclusions').click();await ui.document.querySelector('[data-restore-recommendation]').click();
 assert.deepEqual(ui.calls.find(c=>c.operation==='recommendation.restore'),{operation:'recommendation.restore',args:{id:'rule',revision:2},user_id:'synthetic-user-a'});
 assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),[prepared,restored]);assert.equal(ui.document.querySelectorAll('[data-restore-recommendation]').length,0);
 assert.equal(ui.document.querySelector('[data-prepare-recommendation]').disabled,false);assert.match(ui.el('toast').textContent,/已恢复 1 条候选。其他记录仍受排除规则、公司已有申请或后续修改影响/);
});

test('a persisted restore closes stale management controls and reports a subsequent exclusion-list refresh failure',async()=>{
 for(const stateRefreshFails of [false,true]){
  const ui=await app(),rule=exclusionFixture(),restored={...applicationFixture('candidate',{program:'校招',raw_status:'未申请',notes:'资格全文'}),archived:false};let listReads=0,restoredOnServer=false;
  await ui.render('recommendations');
  ui.handlers['recommendation.exclusions']=()=>++listReads===1?exclusionPage([rule]):apiError('unavailable','合成管理记录读取失败');
  ui.handlers['recommendation.restore']=()=>{restoredOnServer=true;return {exclusion:{...rule,active:false,revision:3},restored:[restored],remaining_exclusions:[]};};
  ui.handlers.state=()=>stateRefreshFails?apiError('unavailable','合成推荐列表读取失败'):syntheticState([restored]);
  await ui.el('recommendation-exclusions').click();await ui.document.querySelector('[data-restore-recommendation]').click();
  assert.equal(restoredOnServer,true);assert.equal(ui.calls.filter(c=>c.operation==='recommendation.restore').length,1);assert.equal(listReads,2);
  assert.equal(ui.el('detail').open,false);assert.equal(ui.document.querySelector('[data-prepare-recommendation]').disabled,false);
  assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),[restored]);
  assert.match(ui.el('notice').textContent,/排除已撤销，已恢复 1 条候选；管理记录刷新失败/);
  assert.equal(ui.el('notice').textContent.includes('推荐列表也未刷新'),stateRefreshFails);
 }
});

test('duplicate or failed restore keeps the exclusion and its reason without fabricating candidates',async()=>{
 const ui=await app(),pending=deferred(),rule=exclusionFixture();await ui.render('recommendations');ui.handlers['recommendation.exclusions']=()=>exclusionPage([rule]);ui.handlers['recommendation.restore']=()=>pending.promise;
 await ui.el('recommendation-exclusions').click();const button=ui.document.querySelector('[data-restore-recommendation]'),first=button.onclick();await button.onclick();
 assert.equal(button.disabled,true);assert.equal(ui.calls.filter(c=>c.operation==='recommendation.restore').length,1);pending.resolve(apiError('revision_conflict','合成排除已变化'));await first;
 assert.equal(button.disabled,false);assert.match(ui.el('detail-content').innerHTML,/合成删除理由/);assert.equal(ui.el('recommendation-exclusion-error').textContent,'合成排除已变化');assert.equal(ui.evaluate("rows('application').length"),0);
});

test('a late exclusion list cannot open an old account dialog after account switching',async()=>{
 const ui=await app({withAccounts:true}),pending=deferred();await ui.render('recommendations');ui.handlers['recommendation.exclusions']=()=>pending.promise;
 const reading=ui.el('recommendation-exclusions').click();await tick();await ui.evaluate("switchAccount('synthetic-user-b')");pending.resolve(exclusionPage([exclusionFixture()]));await reading;
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.el('detail').open,undefined);assert.equal(ui.el('notice').hidden,true);
});

test('a late restore result cannot add an old account candidate or overwrite the new account notice',async()=>{
 const ui=await app({withAccounts:true}),pending=deferred(),rule=exclusionFixture(),restored={...applicationFixture('candidate',{program:'校招'}),archived:false};await ui.render('recommendations');ui.handlers['recommendation.exclusions']=()=>exclusionPage([rule]);ui.handlers['recommendation.restore']=()=>pending.promise;
 await ui.el('recommendation-exclusions').click();const restoring=ui.document.querySelector('[data-restore-recommendation]').click();await tick();await ui.evaluate("switchAccount('synthetic-user-b')");pending.resolve({exclusion:{...rule,active:false},restored:[restored],remaining_exclusions:[]});await restoring;
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.evaluate("rows('application').length"),0);assert.equal(ui.el('detail').open,false);assert.equal(ui.el('notice').hidden,true);
});

test('a failed or duplicate in-flight selection preserves the candidate and never fabricates an application', async () => {
  const ui=await app(),saving=deferred(),record=applicationFixture('conflict',{title:'合成冲突岗位',program:'校招',notes:'保留说明',raw_status:'未申请'});
  setApplications(ui,[record]);ui.handlers['record.save']=()=>saving.promise;
  await ui.render('recommendations');
  const button=ui.document.querySelector('[data-prepare-recommendation]'),first=button.onclick();
  await button.onclick();
  assert.equal(button.disabled,true);
  assert.equal(ui.calls.filter(call=>call.operation==='record.save').length,1);
  saving.resolve(apiError('revision_conflict','合成版本冲突，请刷新'));
  await first;
  assert.equal(button.disabled,false);
  assert.equal(ui.evaluate('page'),'recommendations');
  assert.deepEqual(JSON.parse(ui.evaluate('JSON.stringify(state.records.application)')),[record]);
  assert.match(ui.el('content').innerHTML,/合成冲突岗位/);
  assert.equal(ui.el('notice').textContent,'合成版本冲突，请刷新');
  assert.equal(ui.el('notice').hidden,false);
  assert.deepEqual(ui.calls.map(call=>call.operation),['record.save']);
});

test('application pagination recovers older applications behind more than 200 recent candidates', async () => {
  const ui=await app(),records=Array.from({length:400},(_,index)=>applicationFixture(`candidate-${index}`,{program:'校园招聘'}));
  records.push(applicationFixture('older',{title:'被候选挤到后面的投递',stage:'submitted'}),applicationFixture('oldest',{title:'更早的准备记录',stage:'preparing',program:'校招'}));
  ui.handlers.state=()=>({...syntheticState(records),records:{application:structuredClone(records.slice(0,200))}});
  ui.handlers['record.list']=args=>({items:structuredClone(records.slice(args.offset,args.offset+args.limit)),total:records.length});
  await ui.evaluate("page='applications';load()");
  assert.deepEqual(ui.calls.filter(call=>call.operation==='record.list').map(call=>call.args),[
    {kind:'application',limit:200,offset:200},{kind:'application',limit:200,offset:400}]);
  assert.match(ui.el('content').innerHTML,/显示 1 \/ 1 条岗位/);
  assert.match(ui.el('content').innerHTML,/被候选挤到后面的投递/);assert.doesNotMatch(ui.el('content').innerHTML,/更早的准备记录/);
  await ui.render('recommendations');assert.match(ui.el('content').innerHTML,/更早的准备记录/);
  assert.doesNotMatch(ui.el('content').innerHTML,/未全部读取/);
  assert.equal(ui.evaluate('state.records.application.length'),402);
  assert.equal(ui.evaluate('applicationLoadInfo.complete'),true);
});

test('failed, changed or duplicate pagination exposes incomplete coverage instead of claiming a complete application total', async () => {
  for(const mode of ['failed','changed','duplicate']){
    const ui=await app(),records=Array.from({length:200},(_,index)=>applicationFixture(`synthetic-${index}`,{program:'校招'}));
    ui.handlers.state=()=>({...syntheticState(records),counts:{application:201}});
    ui.handlers['record.list']=()=>mode==='failed'?apiError('unavailable','合成读取失败')
      :{items:[records[0]],total:mode==='changed'?202:201};
    await ui.evaluate("page='applications';load()");
    assert.equal(ui.evaluate('applicationLoadInfo.complete'),false);
    for(const target of ['applications','overview','recommendations']){
      await ui.render(target);
      assert.match(ui.el('content').innerHTML,/投递与推荐记录未全部读取：当前 200 \/ 201 条/);
      assert.match(ui.el('content').innerHTML,/以下数量仅代表已读取记录/);
    }
  }
});

test('a saved choice survives failed list refresh and shows an explicit saved-but-unrefreshed result', async () => {
  const ui=await app(),record=applicationFixture('saved',{title:'合成保存后刷新失败岗位',program:'校招',raw_status:'官网原文'});
  setApplications(ui,[record]);await ui.render('recommendations');
  ui.handlers['record.save']=args=>({...record,data:args.data,revision:4});
  ui.handlers.state=()=>apiError('unavailable','合成列表刷新失败');
  await ui.document.querySelector('[data-prepare-recommendation]').click();
  assert.equal(ui.evaluate('page'),'recommendations');
  assert.equal(ui.evaluate('state.records.application[0].data.stage'),'candidate');
  assert.equal(ui.document.querySelectorAll('[data-prepare-recommendation]').length,1);
  assert.match(ui.el('content').innerHTML,/合成保存后刷新失败岗位/);
  assert.equal(ui.el('notice').hidden,false);
  assert.equal(ui.el('notice').textContent,'资料准备意图已保存，岗位仍在推荐中；列表刷新失败，请刷新核对。');
  assert.equal(ui.el('editor').open,true);
  assert.deepEqual(ui.calls.map(call=>call.operation),['record.save','state']);
});

test('an older pending candidate page cannot overwrite a newer preparation intent that remains recommended', async () => {
  const ui=await app(),oldPage=deferred(),records=Array.from({length:201},(_,index)=>applicationFixture(`race-${index}`,{program:'校招'}));
  setApplications(ui,records.slice(0,200));await ui.render('recommendations');
  ui.handlers.state=()=>({...syntheticState(records),records:{application:structuredClone(records.slice(0,200))}});
  let pageCalls=0;
  ui.handlers['record.list']=()=>++pageCalls===1?oldPage.promise:{items:structuredClone(records.slice(200)),total:201};
  const oldLoading=ui.evaluate('load()');await tick();
  assert.equal(pageCalls,1);
  ui.handlers['record.save']=args=>{records[0]={...records[0],data:args.data,revision:4};return structuredClone(records[0]);};
  await ui.document.querySelector('[data-prepare-recommendation]').click();
  assert.equal(ui.evaluate('state.records.application[0].data.stage'),'candidate');
  assert.match(ui.el('content').innerHTML,/显示 201 \/ 201 条正式校招/);
  assert.match(ui.evaluate('state.records.application[0].data.decision'),/已选择整理该岗位的申请资料/);
  oldPage.resolve({items:[applicationFixture('race-200',{program:'校招'})],total:201});
  assert.equal(await oldLoading,false);
  assert.equal(ui.evaluate('state.records.application[0].data.stage'),'candidate');
  assert.equal(ui.evaluate('applicationLoadInfo.complete'),true);
  assert.match(ui.el('content').innerHTML,/显示 201 \/ 201 条正式校招/);
  assert.equal(ui.el('notice').hidden,true);
});

test('preferences save to the captured database account and campus remains the default scope',async()=>{
 const ui=await app({withAccounts:true});
 ui.stored.settings.preferences={cities:['旧城市'],recruitment_types:['campus'],legacy_rule:{retain:true}};
 ui.handlers['settings.save']=args=>{ui.stored.settings={...ui.stored.settings,...args.settings,preferences:{...ui.stored.settings.preferences,...args.settings.preferences},_revision:5};return structuredClone(ui.stored.settings);};
 await ui.evaluate("page='settings';load()");const form=ui.el('preferences-form');
 form.querySelector('[name=cities]').value='武汉\n北京，武汉';form.querySelector('[name=roles]').value='研究员, 算法工程師';
 form.querySelector('[name=industries]').value='人工智能';form.querySelector('[name=excluded_companies]').value='合成公司';form.querySelector('[name=notes]').value='最新偏好';
 await form.onsubmit({target:form,preventDefault(){}});
 const save=ui.calls.find(c=>c.operation==='settings.save');assert.equal(save.user_id,'synthetic-user-a');assert.equal(save.args.revision,4);
 assert.deepEqual(save.args.settings.preferences,{cities:['武汉','北京'],roles:['研究员','算法工程師'],industries:['人工智能'],excluded_companies:['合成公司'],recruitment_types:['campus'],notes:'最新偏好'});
 assert.deepEqual(ui.stored.settings.preferences.legacy_rule,{retain:true});
 assert.equal(ui.el('preferences-form').querySelector('[name=notes]').value,'最新偏好');
 const reread=ui.calls.find(c=>c.operation==='state');assert.equal(reread.user_id,'synthetic-user-a');
 const next=ui.el('preferences-form');next.querySelector('[name=include_internships]').checked=true;
 await next.onsubmit({target:next,preventDefault(){}});
 assert.deepEqual(ui.calls.filter(c=>c.operation==='settings.save').at(-1).args.settings.preferences.recruitment_types,['campus','internship']);
});

test('editing social-only preferences preserves the selected scope and does not add campus or internships',async()=>{
 const ui=await app({withAccounts:true});
 ui.stored.settings.preferences={recruitment_types:['social'],notes:'原备注'};
 ui.handlers['settings.save']=args=>{ui.stored.settings={...ui.stored.settings,...args.settings,_revision:5};return structuredClone(ui.stored.settings);};
 await ui.evaluate("page='settings';load()");const form=ui.el('preferences-form');
 assert.equal(form.querySelector('[name=include_social]').checked,true);
 assert.equal(form.querySelector('[name=include_campus]').checked,false);
 assert.equal(form.querySelector('[name=include_campus]').disabled,false);
 assert.equal(form.querySelector('[name=include_internships]').checked,false);
 form.querySelector('[name=notes]').value='更新备注';
 await form.onsubmit({target:form,preventDefault(){}});
 assert.deepEqual(ui.calls.find(c=>c.operation==='settings.save').args.settings.preferences.recruitment_types,['social']);
 assert.deepEqual(ui.stored.settings.preferences.recruitment_types,['social']);
});

test('recruitment types can be explicitly changed and empty selection never overwrites saved preferences',async()=>{
 const ui=await app({withAccounts:true});
 ui.stored.settings.preferences={recruitment_types:['campus'],notes:'保留备注'};
 ui.handlers['settings.save']=args=>{ui.stored.settings={...ui.stored.settings,...args.settings,_revision:5};return structuredClone(ui.stored.settings);};
 await ui.evaluate("page='settings';load()");const form=ui.el('preferences-form');
 form.querySelector('[name=include_campus]').checked=false;
 await form.onsubmit({target:form,preventDefault(){}});
 assert.equal(ui.calls.filter(c=>c.operation==='settings.save').length,0);
 assert.deepEqual(ui.stored.settings.preferences.recruitment_types,['campus']);
 assert.equal(ui.el('notice').hidden,false);
 form.querySelector('[name=include_social]').checked=true;
 form.querySelector('[name=include_internships]').checked=true;
 await form.onsubmit({target:form,preventDefault(){}});
 assert.deepEqual(ui.stored.settings.preferences.recruitment_types,['internship','social']);
 const refreshed=ui.el('preferences-form');
 assert.equal(refreshed.querySelector('[name=include_social]').checked,true);
 assert.equal(refreshed.querySelector('[name=include_internships]').checked,true);
 assert.equal(refreshed.querySelector('[name=include_campus]').checked,false);
});

test('switching accounts clears editors, filters, and OTP state and discards a late result',async()=>{
 const ui=await app({withAccounts:true}),testing=deferred();ui.handlers['otp.status']=()=>ui.stored.user_id==='synthetic-user-a'?gotify():manual();ui.handlers['otp.test']=()=>testing.promise;
 await ui.render('settings');const first=ui.el('otp-test').click();
 ui.evaluate("query='旧查询';profileTab='family';taskOffset=50;openEdit('basic','')");assert.equal(ui.el('editor').open,true);
 const selector=ui.el('account-select');selector.value='synthetic-user-b';await selector.onchange({target:selector});
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.evaluate('query'),'');assert.equal(ui.evaluate('profileTab'),'basic');assert.equal(ui.evaluate('taskOffset'),0);assert.equal(ui.evaluate('editing'),null);assert.equal(ui.el('editor').open,false);
 assert.equal(ui.el('otp-provider').value,'manual');const writes=ui.el('content').htmlWrites;
 testing.resolve(connectionResult());await first;
 assert.equal(ui.el('otp-provider').value,'manual');assert.equal(ui.el('content').htmlWrites,writes);assert.equal(ui.evaluate('otpState.connection_test'),null);assert.equal(ui.el('notice').hidden,true);
 assert.equal(ui.calls.find(c=>c.operation==='otp.test').user_id,'synthetic-user-a');assert.equal(ui.calls.find(c=>c.operation==='account.select').user_id,'synthetic-user-a');
 assert.equal(ui.calls.filter(c=>c.operation==='otp.status').at(-1).user_id,'synthetic-user-b');
});

test('a profile file read begun in an old account never imports into the new account',async()=>{
 const ui=await app({withAccounts:true}),reading=deferred();await ui.render('profile');
 const input=ui.el('profile-file');input.files=[{text:()=>reading.promise}];const importing=input.onchange({target:input});
 await ui.evaluate("switchAccount('synthetic-user-b')");reading.resolve('{"records":[]}');await importing;
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.calls.filter(c=>c.operation==='profile.import').length,0);assert.equal(ui.el('notice').hidden,true);
});

test('late file and network failures from an old account do not report errors in the new account',async()=>{
 const ui=await app({withAccounts:true}),reading=deferred(),request=deferred();await ui.render('profile');
 const input=ui.el('profile-file');input.files=[{text:()=>reading.promise}];const importing=input.onchange({target:input});ui.handlers['profile.export']=()=>request.promise;const exporting=ui.evaluate("api('profile.export')");
 await ui.evaluate("switchAccount('synthetic-user-b')");reading.reject(Error('合成旧文件读取失败'));request.reject(Error('合成旧网络失败'));await importing;await assert.rejects(exporting,{code:'stale_account'});
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.el('notice').hidden,true);
});

test('refresh adopts an account changed by another tab once and closes stale editing',async()=>{
 const ui=await app({withAccounts:true});ui.evaluate("openEdit('basic','')");let reads=0;
 ui.handlers.state=()=>++reads===1?apiError('account_changed','账号已在另一页切换'):{...structuredClone(ui.stored),user_id:'synthetic-user-b',account:{id:'synthetic-user-b',name:'合成账号乙'}};
 await ui.el('refresh').click();assert.equal(reads,2);assert.equal(ui.calls.filter(c=>c.operation==='state')[0].user_id,'synthetic-user-a');assert.equal(ui.calls.filter(c=>c.operation==='state')[1].user_id,undefined);
 assert.equal(ui.evaluate('state.user_id'),'synthetic-user-b');assert.equal(ui.el('editor').open,false);assert.equal(ui.el('account-select').value,'synthetic-user-b');
});

test('task editor writes revision and account, and execution text references fresh context in the bound database',async()=>{
 const ui=await app({withAccounts:true}),task={id:'synthetic-task',user_id:'synthetic-user-a',title:'合成任务',kind:'search',instructions:'查找岗位',data:{existing:true},status:'pending',revision:7};
 ui.handlers['task.list']=()=>({items:[task],total:1,next_offset:null});ui.handlers['task.get']=()=>structuredClone(task);ui.handlers['task.save']=args=>Object.assign(task,args,{revision:8});ui.handlers['task.context']=()=>({user_id:'synthetic-user-a',settings:{preferences:{notes:'最新偏好'}},task:structuredClone(task),profile:{items:[],total:0,next_offset:null},applications:{items:[],total:0,next_offset:null}});
 await ui.evaluate("navigate('tasks')");assert.match(ui.el('content').innerHTML,/合成任务/);await ui.document.querySelector('[data-task-edit]').click();
 const form=ui.el('task-form');ui.el('task-editor-fields').querySelector('[name=instructions]').value='执行时读取最新偏好';await form.onsubmit({target:form,preventDefault(){}});
 const save=ui.calls.find(c=>c.operation==='task.save');assert.equal(save.user_id,'synthetic-user-a');assert.equal(save.args.id,task.id);assert.equal(save.args.revision,7);assert.equal(save.args.instructions,'执行时读取最新偏好');assert.deepEqual(save.args.data,{existing:true});
 await ui.document.querySelector('[data-task-prompt]').click();const text=ui.el('detail-content').innerHTML;assert.match(text,/C:\/synthetic-local-data\/career.db/);assert.match(text,/&quot;--data-dir&quot;,&quot;C:\/synthetic-local-data&quot;/);assert.match(text,/&quot;operation&quot;:&quot;task.context&quot;/);assert.match(text,/&quot;args&quot;:\{&quot;id&quot;:&quot;synthetic-task&quot;\}/);assert.doesNotMatch(text,/action=&quot;context&quot;|旧城市/);
 await ui.el('copy-task-prompt').click();const call=ui.clipboard.at(-1).match(/career_tasks：(\{[^\n]+?\})，/);assert.ok(call);assert.deepEqual(JSON.parse(call[1]),{operation:'task.context',args:{id:'synthetic-task'},user_id:'synthetic-user-a'});
 ui.el('detail').close();await ui.document.querySelector('[data-task-context]').click();assert.equal(ui.calls.find(c=>c.operation==='task.context').user_id,'synthetic-user-a');assert.match(ui.el('detail-content').innerHTML,/最新偏好/);
});

test('task launch instructions keep Chinese and apostrophe paths as separate JSON arguments',async()=>{
 const ui=await app({withAccounts:true});ui.stored.database={path:"C:/合成资料/O'Brien/career.db",root_dir:"C:/合成项目/O'Brien"};await ui.evaluate('load()');
 const prompt=ui.evaluate("taskExecutionPrompt({id:'synthetic-task'},accountContext())"),line=prompt.split('\n').find(l=>l.startsWith('["powershell"'));
 assert.ok(line);assert.deepEqual(JSON.parse(line),['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',"C:/合成项目/O'Brien/scripts/start.ps1",'mcp','--data-dir',"C:/合成资料/O'Brien",'--user-id','synthetic-user-a']);
 assert.doesNotMatch(prompt,/--data-dir C:\/合成项目/);
});

test('task pagination reads database pages and task filters start from the first page',async()=>{
 const ui=await app({withAccounts:true});ui.handlers['task.list']=args=>({items:Array.from({length:args.offset?5:50},(_,i)=>({id:`synthetic-${args.offset+i}`,title:`任务 ${args.offset+i}`,kind:'custom',status:'pending',instructions:''})),total:55,next_offset:args.offset?null:50});
 await ui.evaluate("navigate('tasks')");await ui.el('task-next').click();const list=ui.calls.filter(c=>c.operation==='task.list');assert.equal(list[1].args.offset,50);assert.equal(list[1].user_id,'synthetic-user-a');assert.match(ui.el('content').innerHTML,/51–55 \/ 55/);
 const status=ui.el('task-status');status.value='running';await status.onchange({target:status});assert.deepEqual(ui.calls.filter(c=>c.operation==='task.list').at(-1).args,{limit:50,offset:0,status:'running'});
});
