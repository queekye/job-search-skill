import assert from 'node:assert/strict';
import test from 'node:test';
import {app} from './web_app_harness.mjs';

const application=(id='role-one',data={})=>({id,kind:'application',revision:9,scope:'company:synthetic',status:'observed',source:'synthetic_website',data:{company:'Synthetic Company',title:'Synthetic ML role',program:'Synthetic campus project',job_id:'00042',url:'https://example.test/role/00042',stage:'interview',raw_status:'Synthetic interview pending',checked_at:'2026-10-10T03:00:00Z',...data}});
const snapshot={job_description:'  Synthetic responsibilities\n<Synthetic requirements>  ',jd_url:'https://example.test/role/00042',jd_checked_at:'2026-10-16T06:00:00+08:00',jd_coverage:'complete',jd_identity_status:'exact',jd_last_attempt_at:'2026-10-16T07:00:00+08:00',jd_blocker:'Synthetic later read failed'};
const setField=(ui,key,value)=>{ui.el('editor-fields').querySelector(`[name=${key}]`).value=value;};
const save=ui=>ui.evaluate('saveEdit({target:document.querySelector("#edit-form"),preventDefault(){}})');
async function setup(records){const ui=await app({withAccounts:true});ui.stored.records={application:structuredClone(records)};ui.handlers['record.save']=args=>{const record=ui.stored.records.application.find(r=>r.id===args.id),saved={...record,...structuredClone(args),revision:record.revision+1};ui.stored.records.application=ui.stored.records.application.map(r=>r.id===saved.id?saved:r);return saved;};await ui.evaluate("navigate('applications')");return ui;}

test('application and recommendation JD views show readiness, source evidence and escaped original text without writes',async()=>{
 const injection='<img src=x onerror=alert(1)>',records=[application('missing'),application('ready',{...snapshot,job_description:injection}),application('partial',{...snapshot,jd_coverage:'partial'}),application('related',{...snapshot,stage:'candidate',jd_identity_status:'related',program:'Synthetic campus recruitment'})],ui=await setup(records);
 assert.match(ui.el('content').innerHTML,/JD 未保存|对应岗位 JD 已保存|JD 已保存，范围不完整/);
 const button=ui.document.querySelectorAll('[data-job-description]').find(b=>b.dataset.jobDescription==='ready');await button.click();const html=ui.el('detail-content').innerHTML;
 assert.match(html,/最后成功读取 JD|最近尝试读取 JD|Synthetic later read failed|00042/);assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);assert.doesNotMatch(html,/<img/);assert.match(html,/href="https:\/\/example.test\/role\/00042"/);
 await ui.evaluate("navigate('recommendations')");assert.match(ui.el('content').innerHTML,/相关岗位 JD 参考/);assert.equal(ui.calls.some(c=>c.operation==='record.save'),false);assert.deepEqual(ui.stored.records.application,records);
});

test('JD editing preserves original whitespace, unrelated application evidence and unknown fields with the record revision',async()=>{
 const original=application('role-one',{future_extension:{retain:false}}),ui=await setup([original]);await ui.document.querySelector('[data-job-description]').click();await ui.el('job-description-edit').click();
 assert.match(ui.el('editor-fields').innerHTML,/岗位 JD 与来源/);for(const [key,value]of Object.entries(snapshot))setField(ui,key,value);await save(ui);
 const saved=ui.calls.find(c=>c.operation==='record.save');assert.equal(saved.user_id,'synthetic-user-a');assert.equal(saved.args.id,original.id);assert.equal(saved.args.revision,9);assert.equal(saved.args.scope,original.scope);assert.equal(saved.args.status,original.status);
 for(const [key,value]of Object.entries(snapshot))assert.deepEqual(saved.args.data[key],value);
 for(const key of ['stage','raw_status','checked_at','future_extension'])assert.deepEqual(saved.args.data[key],original.data[key]);assert.equal(Object.hasOwn(saved.args.data,'last_attempt_at'),false);
});

test('unrelated edits keep absent JD fields absent and preserve explicit nulls in existing snapshots',async()=>{
 for(const extra of [{},{job_description:null,jd_url:null,jd_checked_at:null,jd_coverage:null,jd_identity_status:null,jd_last_attempt_at:null,jd_blocker:null}]){const original=application('role-one',extra),ui=await setup([original]);ui.evaluate("openEdit('application','role-one')");setField(ui,'notes','Only update a note');await save(ui);const saved=ui.calls.find(c=>c.operation==='record.save').args.data;for(const key of Object.keys(snapshot)){assert.equal(Object.hasOwn(saved,key),Object.hasOwn(extra,key));if(Object.hasOwn(extra,key))assert.equal(saved[key],null);}}
});

test('application JD editing after visiting profile still targets the application, and stale IDs cannot create a replacement',async()=>{
 const original=application('role-one',snapshot),ui=await setup([original]);ui.stored.records.basic=[{id:'synthetic-basic',kind:'basic',revision:1,status:'confirmed',data:{name:'Synthetic Applicant'}}];await ui.evaluate("navigate('profile')");await ui.evaluate("navigate('applications')");await ui.document.querySelector('[data-job-description]').click();await ui.el('job-description-edit').click();setField(ui,'jd_blocker','Updated read failure');await save(ui);
 const saved=ui.calls.find(c=>c.operation==='record.save');assert.equal(saved.args.id,'role-one');assert.equal(saved.args.revision,9);assert.equal(saved.args.data.job_description,snapshot.job_description);assert.equal(saved.args.data.jd_blocker,'Updated read failure');ui.el('editor').close();ui.evaluate("openEdit('application','unavailable')");assert.notEqual(ui.el('editor').open,true);assert.match(ui.el('notice').textContent,/记录已变化/);
});

test('saved JD status requires matching identity, source, time and coverage instead of treating any text as verified',async()=>{
 const ui=await app();for(const [patch,label]of [[{jd_identity_status:'unknown'},'来源待核对'],[{jd_url:'javascript:alert(1)'},'来源待核对'],[{jd_checked_at:'2026-10-16T06:00'},'来源待核对'],[{jd_coverage:null},'范围不完整'],[{jd_identity_status:'related'},'相关岗位 JD 参考']]){const record=application('role-one',{...snapshot,...patch});const html=ui.evaluate(`jobDescriptionHtml(${JSON.stringify(record)})`);assert.match(html,new RegExp(label));assert.doesNotMatch(html,/href="javascript:/);assert.doesNotMatch(html,/>对应岗位 JD 已保存</);}
});

test('a JD edit control retained from an old account cannot open an editor after account switching',async()=>{
 const ui=await setup([application('role-one',snapshot)]);await ui.document.querySelector('[data-job-description]').click();const oldEdit=ui.el('job-description-edit');await ui.evaluate("switchAccount('synthetic-user-b')");await oldEdit.click();assert.notEqual(ui.el('editor').open,true);assert.equal(ui.calls.some(c=>c.operation==='record.save'),false);assert.equal(ui.el('notice').hidden,true);
});
