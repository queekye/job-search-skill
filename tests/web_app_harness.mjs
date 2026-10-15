import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../web/app.js', import.meta.url), 'utf8');
const profileCatalogFixture = JSON.parse(await readFile(new URL('../schemas/profile-fields.json', import.meta.url), 'utf8'));
const intentionCatalogFixture = JSON.parse(await readFile(new URL('../schemas/intention-fields.json', import.meta.url), 'utf8'));

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

async function app({withAccounts=false}={}) {
  const document = new Element('document');
  document.innerHTML = '<main id="content"></main><h1 id="page-title"></h1><span id="save-state"></span>'
    + '<p id="notice" hidden></p><p id="toast" hidden></p><dialog id="editor"><h2 id="editor-title"></h2>'
    + '<form id="edit-form"><div id="editor-fields"></div><button type="submit"></button></form></dialog><button id="refresh"></button>'
    + '<dialog id="detail"><h2 id="detail-title"></h2><div id="detail-content"></div><div id="detail-actions"></div></dialog>'
    + '<dialog id="task-editor"><h2 id="task-editor-title"></h2><form id="task-form"><div id="task-editor-fields"></div><button type="submit"></button></form></dialog>'
    + (withAccounts?'<select id="account-select"></select><button id="account-new"></button><dialog id="account-editor"><form id="account-form"><input name="name"><button type="submit"></button></form></dialog>':'');
  const calls = [], syncCalls = [], timers = [], clipboard = [];
  const stored = {records: {}, relations: [], documents: [], settings: {...publicDefaults, _revision: 4, schedule: {
    time: '10:00', timezone: 'Asia/Shanghai', allow_relogin: false, enabled: false, host_task_id: null,
  }}};
  const accountItems=[{id:'synthetic-user-a',name:'合成账号甲',created_at:'2026-10-09T01:00:00Z'},{id:'synthetic-user-b',name:'合成账号乙',created_at:'2026-10-09T02:00:00Z'}];
  if(withAccounts)Object.assign(stored,{account:accountItems[0],user_id:accountItems[0].id,database:{path:'C:/synthetic-local-data/career.db',base_data_dir:'C:/synthetic-local-data',root_dir:'C:/synthetic-project'}});
  const handlers = {
    state: () => structuredClone(stored),
    'profile.schema': () => structuredClone(profileCatalogFixture),
    'intention.schema': () => structuredClone(intentionCatalogFixture),
    'intention.get': () => ({data:structuredClone(stored.settings.preferences||{}),revision:stored.settings._revision,legacy_count:0}),
    'intention.patch': args => handlers['settings.save']({settings:{preferences:args.changes},revision:args.revision}),
    'resume.list': () => ({items:[],total:0,next_offset:null}),
    'answer.list': () => ({items:[],total:0,next_offset:null}),
    'decision.list': () => ({items:[],total:0,next_offset:null}),
    'profile.get': args => {const all=Object.values(stored.records).flat().filter(r=>!['application','resume_variant','company_answer','company_decision'].includes(r.kind));const offset=args.offset||0,limit=args.limit||20,items=all.slice(offset,offset+limit);return {items,total:all.length,offset,limit,next_offset:offset+items.length<all.length?offset+items.length:null};},
    'profile.relation.list': args => {const all=stored.relations.filter(r=>!args.record_id||[r.from_id,r.to_id].includes(args.record_id)),offset=args.offset||0,limit=args.limit||20,items=all.slice(offset,offset+limit);return {items,total:all.length,offset,limit,next_offset:offset+items.length<all.length?offset+items.length:null};},
    'otp.status': manual,
    'sync.status': localSync,
    'resource.list': () => ({items:[],registry_configured:false,remote_registry_supported:true}),
    'otp.test': () => connectionResult(),
    'settings.save': args => ({...stored.settings, ...args.settings, _revision: 5}),
    'account.list':()=>({items:structuredClone(accountItems),total:accountItems.length}),
    'account.select':args=>{stored.account=accountItems.find(a=>a.id===args.id);stored.user_id=args.id;return structuredClone(stored.account);},
  };
  const context = vm.createContext({
    document, FormData, URL, Blob, structuredClone, console, location: {hash: ''}, window: {addEventListener() {}},
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
  evaluate('profileCatalog='+JSON.stringify(profileCatalogFixture));
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


export {decode,app,deferred,tick,manual,gotify,connectionResult,apiError,localSync,writerSync,publicPack,publicDefaults};
