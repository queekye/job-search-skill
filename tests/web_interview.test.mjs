import assert from 'node:assert/strict';
import test from 'node:test';
import {app,deferred,tick,apiError} from './web_app_harness.mjs';

const fact=(id,category='research',revision=3)=>({id,kind:'experience',revision,status:'confirmed',source:'synthetic_fact',scope:'global',data:{title:`Synthetic ${id}`,category}});
const application=(id,stage='interview')=>({id,kind:'application',revision:4,status:'observed',source:'synthetic_website',data:{company:'Synthetic Company',title:`Synthetic ${id}`,stage}});
const data=(extra={})=>({workflow:'interview_preparation',schema_version:1,application_id:'applied-one',scheduled_at:'2026-10-16T06:00:00Z',round:null,selected_record_ids:['research-one'],source_versions:{'research-one':2,'applied-one':4},plan:[],project_cards:[],practice:[],summary:'Synthetic summary',...extra});
const task=(extra={})=>({id:'interview-one',kind:'custom',title:'Synthetic interview',instructions:'Synthetic preparation',revision:7,status:'pending',data:data(),...extra});
const value=(ui,key,next)=>{ui.el('task-form').querySelector(`[name=${key}]`).value=next;};
const submit=ui=>{const form=ui.el('task-form');return form.onsubmit({target:form,preventDefault(){}});};
async function setup(original=task()){
 const ui=await app({withAccounts:true});ui.stored.records={experience:[fact('research-one'),fact('project-one','project'),fact('skill-one','skill')],application:[application('applied-one'),application('candidate-one','candidate')]};
 let current=original;
 ui.handlers['task.list']=()=>({items:current?[structuredClone(current)]:[],total:current?1:0,next_offset:null});
 ui.handlers['task.get']=()=>structuredClone(current);
 ui.handlers['task.context']=()=>({task:structuredClone(current),profile:{items:structuredClone(ui.stored.records.experience),total:3,next_offset:null},applications:{items:structuredClone(ui.stored.records.application),total:2,next_offset:null}});
 ui.handlers['task.save']=args=>{current={...current,...structuredClone(args),id:current?.id||'new-task',revision:(current?.revision||0)+1};return structuredClone(current);};
 await ui.evaluate("navigate('tasks')");return ui;
}

test('interview template selects only actual applications and project/research facts, saves one shared custom task without asserting submission',async()=>{
 const ui=await setup(null);await ui.el('task-interview-new').click();const html=ui.el('task-editor-fields').innerHTML;
 assert.match(html,/Synthetic applied-one|Synthetic Company/);assert.doesNotMatch(html,/candidate-one|skill-one/);
 value(ui,'title','Synthetic planned interview');value(ui,'application_id','applied-one');value(ui,'scheduled_at','2026-10-16T14:30:00+08:00');value(ui,'round','First interview');
 ui.document.querySelectorAll('[data-interview-record]').find(el=>el.dataset.interviewRecord==='project-one').checked=true;await submit(ui);
 const saved=ui.calls.find(c=>c.operation==='task.save');assert.equal(saved.user_id,'synthetic-user-a');assert.equal(saved.args.kind,'custom');assert.equal(saved.args.data.workflow,'interview_preparation');assert.equal(saved.args.data.schema_version,1);assert.equal(saved.args.data.application_id,'applied-one');assert.deepEqual(saved.args.data.selected_record_ids,['project-one']);assert.deepEqual(saved.args.data.source_versions,{'project-one':3,'applied-one':4});assert.equal(saved.args.data.scheduled_at,'2026-10-16T14:30:00+08:00');assert.equal(ui.calls.some(c=>c.operation==='record.save'),false);
});

test('unassociated interview preparation preserves unknown fields, result payloads, missing references and original revision pins',async()=>{
 const original=task({data:data({application_id:'missing-application',selected_record_ids:['research-one','missing-source'],future_extension:{unknown:false},practice:[{question:'Synthetic question',future_score:0}]})}),ui=await setup(original);
 await ui.document.querySelector('[data-task-edit]').click();assert.match(ui.el('task-editor-fields').innerHTML,/原申请不可用|原资料不可用/);value(ui,'interview_summary','Edited summary');await submit(ui);
 const saved=ui.calls.find(c=>c.operation==='task.save');assert.equal(saved.args.revision,7);assert.equal(saved.args.data.application_id,'missing-application');assert.deepEqual(saved.args.data.selected_record_ids,['research-one','missing-source']);assert.deepEqual(saved.args.data.source_versions,{'research-one':2,'applied-one':4});assert.deepEqual(saved.args.data.future_extension,{unknown:false});assert.deepEqual(saved.args.data.practice,[{question:'Synthetic question',future_score:0}]);assert.equal(saved.args.data.summary,'Edited summary');
 const blank=await setup(null);await blank.el('task-interview-new').click();value(blank,'title','Interview without recorded application');await submit(blank);const payload=blank.calls.find(c=>c.operation==='task.save').args.data;assert.equal(payload.application_id,null);assert.equal(payload.scheduled_at,null);assert.deepEqual(payload.selected_record_ids,[]);assert.equal(blank.calls.some(c=>c.operation==='record.save'),false);
});

test('time input rejects ambiguous local or invalid calendar dates and displays UTC input in Asia/Shanghai',async()=>{
 const ui=await setup();await ui.document.querySelector('[data-task-edit]').click();for(const stamp of ['2026-10-16T14:00','2026-02-30T14:00:00+08:00','2026-10-16T24:00:00+08:00']){value(ui,'scheduled_at',stamp);await submit(ui);assert.match(ui.el('notice').textContent,/ISO 时间/);}assert.equal(ui.calls.some(c=>c.operation==='task.save'),false);
 assert.match(ui.evaluate("interviewTime('2026-10-16T06:00:00Z')"),/2026.*10.*16.*14:00.*Asia\/Shanghai/);assert.match(ui.evaluate("interviewTime('2026-10-16T14:00')"),/时间需核对/);
});

test('readable interview details expose changed/missing sources, escaped content, real practice statuses and tolerate unknown structures',async()=>{
 const injection='<img src=x onerror=alert(1)>',original=task({data:data({selected_record_ids:['research-one','gone'],plan:[{topic:injection,priority:'high',status:'running'},null],project_cards:[{record_id:'research-one',narrative:injection,contribution:'Synthetic contribution',evidence:['Synthetic experiment'],limitations:'Synthetic limits',questions:[injection]},null],practice:[{question:injection,answer_summary:'Synthetic answer',feedback:'Synthetic feedback',misconceptions:[injection],status:'needs_review'},{question:'Read explanation only',status:'unassessed'},{question:'Unverified pass marker',status:'passed'},{question:'Retested answer',status:'passed',retested_at:'2026-10-15T06:00:00Z'},null]})}),ui=await setup(original);
 await ui.document.querySelector('[data-task-detail]').click();const html=ui.el('detail-content').innerHTML;
 assert.match(html,/原资料已变更，讲述内容需核对/);assert.match(html,/原记录不可用/);assert.match(html,/需要复习/);assert.match(html,/尚未评估/);assert.match(html,/通过标记（未记录复测时间）/);assert.match(html,/复测通过/);assert.match(html,/讲解过不代表已经掌握/);assert.match(html,/&lt;img src=x onerror=alert\(1\)&gt;/);assert.doesNotMatch(html,/<img|<script/);assert.equal(ui.calls.some(c=>c.operation==='task.save'),false);
});

test('task save conflicts preserve the editable draft and suppress overlapping submissions',async()=>{
 const ui=await setup(),pending=deferred();ui.handlers['task.save']=()=>pending.promise;await ui.document.querySelector('[data-task-edit]').click();value(ui,'interview_summary','Keep this reviewed draft');const saving=submit(ui);await tick();await submit(ui);assert.equal(ui.calls.filter(c=>c.operation==='task.save').length,1);pending.resolve(apiError('revision_conflict','Synthetic task changed'));await saving;assert.equal(ui.el('task-editor').open,true);assert.equal(ui.el('task-form').querySelector('[name=interview_summary]').value,'Keep this reviewed draft');assert.match(ui.el('notice').textContent,/Synthetic task changed/);
});

test('late interview reads and failed writes do not expose or close dialogs after switching accounts',async()=>{
 const ui=await setup(),pending=deferred();ui.handlers['task.context']=()=>pending.promise;const opening=ui.document.querySelector('[data-task-edit]').click();await tick();await ui.evaluate("switchAccount('synthetic-user-b')");pending.resolve({task:task({title:'Old private task'}),profile:{items:[fact('Old private source')],next_offset:null},applications:{items:[],next_offset:null}});await opening;assert.notEqual(ui.el('task-editor').open,true);assert.doesNotMatch(ui.el('task-editor-fields').innerHTML,/Old private/);assert.equal(ui.el('notice').hidden,true);
});

test('capability catalog is readable, escapes nested metadata, handles unavailable service and makes no changes',async()=>{
 const ui=await setup(),injection='<script>synthetic()</script>';ui.handlers['capability.list']=()=>({items:[{id:'interview_preparation',name:'Interview preparation',description:injection,status:'workflow',entry:{skill:'career-interview',tool:'career_tasks',operation:'task.context'},requires:['Current shared context'],limitations:[injection],evidence:{scope:'source_contract',installed_plugin:'not_asserted'}}]});await ui.el('capability-catalog').click();const html=ui.el('detail-content').innerHTML;assert.match(html,/Interview preparation/);assert.match(html,/流程指引/);assert.match(html,/task.context/);assert.match(html,/not_asserted/);assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);ui.handlers['capability.list']=()=>apiError('unknown_operation','Synthetic old service');await ui.el('capability-catalog').click();assert.match(ui.el('detail-title').textContent,/暂不可用/);assert.match(ui.el('detail-content').innerHTML,/Synthetic old service/);assert.equal(ui.calls.some(c=>c.operation==='task.save'),false);
});

test('interview source selectors finish both paginated groups and reject changing snapshots before editing',async()=>{
 const ui=await setup(),original=task(),profiles=[fact('research-one'),fact('project-two','project')],applications=[application('applied-one'),application('applied-two')];
 ui.handlers['task.context']=args=>({task:structuredClone(original),profile:{items:[structuredClone(profiles[args.profile_offset||0])],total:2,next_offset:args.profile_offset?null:1},applications:{items:[structuredClone(applications[args.application_offset||0])],total:2,next_offset:args.application_offset?null:1}});
 await ui.document.querySelector('[data-task-edit]').click();const html=ui.el('task-editor-fields').innerHTML;assert.match(html,/project-two/);assert.match(html,/applied-two/);assert.equal(ui.calls.filter(c=>c.operation==='task.context').length,3);ui.el('task-editor').close();
 ui.handlers['task.context']=args=>({task:{...original,revision:args.profile_offset?8:7},profile:{items:[profiles[0]],total:2,next_offset:args.profile_offset?null:1},applications:{items:[],total:0,next_offset:null}});await ui.document.querySelector('[data-task-edit]').click();assert.equal(ui.el('task-editor').open,false);assert.match(ui.el('notice').textContent,/分页读取时发生变化/);assert.equal(ui.calls.some(c=>c.operation==='task.save'),false);
});

test('editing existing unpinned references cannot silently endorse current revisions or old result claims',async()=>{
 const original=task({data:data({source_versions:{},project_cards:[{record_id:'research-one',narrative:'Old narrative',evidence:'Old unreviewed metric'}]})}),ui=await setup(original);ui.stored.records.experience[0].revision=9;await ui.document.querySelector('[data-task-edit]').click();value(ui,'title','Only rename the interview');await submit(ui);const saved=ui.calls.find(c=>c.operation==='task.save');assert.deepEqual(saved.args.data.source_versions,{});assert.deepEqual(saved.args.data.project_cards,original.data.project_cards);await ui.document.querySelector('[data-task-detail]').click();assert.match(ui.el('detail-content').innerHTML,/未记录引用版本，讲述内容需核对/);
 await ui.document.querySelector('[data-task-edit]').click();ui.document.querySelectorAll('[data-interview-record]').find(el=>el.dataset.interviewRecord==='project-one').checked=true;await submit(ui);const next=ui.calls.filter(c=>c.operation==='task.save').at(-1);assert.deepEqual(next.args.data.source_versions,{'project-one':3});assert.equal(Object.hasOwn(next.args.data.source_versions,'applied-one'),false);assert.equal(Object.hasOwn(next.args.data.source_versions,'research-one'),false);
});

test('unknown interview versions and malformed top-level fields use generic editing and preserve the exact JSON payload',async()=>{
 for(const changed of [{schema_version:2,selected_record_ids:{future:['research-one']},source_versions:[{id:'research-one',revision:2}],summary:{future:'Keep structured summary'}},{selected_record_ids:{future:['research-one']}},{source_versions:[]},{summary:{content:'Unknown future summary'}},{plan:null}]){const original=task({data:data(changed)}),ui=await setup(original);await ui.document.querySelector('[data-task-edit]').click();assert.match(ui.el('task-editor-fields').innerHTML,/暂不支持专用表单/);assert.equal(ui.el('task-form').querySelector('[name=interview_summary]'),null);value(ui,'title','Rename without conversion');await submit(ui);assert.deepEqual(ui.calls.find(c=>c.operation==='task.save').args.data,original.data);await ui.document.querySelector('[data-task-detail]').click();assert.match(ui.el('detail-content').innerHTML,/数据版本或结构需核对/);}
});

test('readable plan translates stable priority values without changing stored task data',async()=>{
 const original=task({data:data({plan:[{topic:'Synthetic high',priority:'high',status:'pending'},{topic:'Synthetic medium',priority:'medium',status:'in_progress'},{topic:'Synthetic low',priority:'low',status:'completed'}]})}),ui=await setup(original);await ui.document.querySelector('[data-task-detail]').click();const html=ui.el('detail-content').innerHTML;assert.match(html,/优先级 高/);assert.match(html,/优先级 中/);assert.match(html,/优先级 低/);assert.doesNotMatch(html,/优先级 high|优先级 medium|优先级 low/);assert.equal(ui.calls.some(c=>c.operation==='task.save'),false);
});

test('advanced JSON upgrades during editing retain their new structure instead of being converted by the v1 form',async()=>{
 const ui=await setup();await ui.document.querySelector('[data-task-edit]').click();const advanced=data({schema_version:2,summary:{future:'Structured content'},selected_record_ids:{future:['research-one']},source_versions:[{id:'research-one',revision:2}],practice:[{question:'Synthetic future question',status:'future_pending'}]});value(ui,'data',JSON.stringify(advanced));await submit(ui);assert.deepEqual(ui.calls.find(c=>c.operation==='task.save').args.data,advanced);await ui.document.querySelector('[data-task-detail]').click();assert.match(ui.el('detail-content').innerHTML,/状态需核对：future_pending/);
});

test('interview details use explicitly resolved sources and saved JD even when general context pages omit them',async()=>{
 const original=task(),ui=await setup(original),linked=application('applied-one');linked.data={...linked.data,url:'https://example.test/linked-role',job_description:'Synthetic saved duties\n<Synthetic saved requirements>',jd_url:'https://example.test/linked-role',jd_checked_at:'2026-10-16T01:00:00Z',jd_coverage:'complete',jd_identity_status:'exact'};
 ui.handlers['task.context']=()=>({task:structuredClone(original),profile:{items:[],total:0,next_offset:null},applications:{items:[],total:0,next_offset:null},interview_context:{application:structuredClone(linked),selected_records:[fact('research-one')],issues:[{code:'source_changed',record_id:'research-one'}]}});
 await ui.document.querySelector('[data-task-detail]').click();const html=ui.el('detail-content').innerHTML;
 assert.match(html,/对应岗位 JD 已保存|Synthetic saved duties/);assert.match(html,/&lt;Synthetic saved requirements&gt;/);assert.match(html,/原资料已变更，讲述内容需核对/);assert.doesNotMatch(html,/原记录不可用|<Synthetic saved requirements>/);assert.equal(ui.calls.some(c=>c.operation==='task.save'||c.operation==='record.save'),false);
});

test('interview JD absence and related-role references stay visible without implying a complete target JD',async()=>{
 const ui=await setup();await ui.document.querySelector('[data-task-detail]').click();assert.match(ui.el('detail-content').innerHTML,/JD 未保存|打开已存岗位网页|尚未保存 JD/);
 Object.assign(ui.stored.records.application[0].data,{job_description:'Synthetic related-role description',jd_url:'https://example.test/other-program',jd_checked_at:'2026-10-16T01:00:00Z',jd_coverage:'complete',jd_identity_status:'related'});await ui.document.querySelector('[data-task-detail]').click();const html=ui.el('detail-content').innerHTML;assert.match(html,/相关岗位 JD 参考|不能作为原岗位要求|Synthetic related-role description/);assert.doesNotMatch(html,/>对应岗位 JD 已保存</);assert.equal(ui.calls.some(c=>c.operation==='task.save'||c.operation==='record.save'),false);
});
