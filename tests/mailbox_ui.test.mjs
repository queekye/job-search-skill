import assert from 'node:assert/strict';
import test from 'node:test';
import {app,deferred,tick,apiError} from './web_app_harness.mjs';

// Fixed-provider QQ tests use synthetic local parts; no real mailbox is configured.
const qqAddress = local => [local,'qq.com'].join('@');
const mailbox = (extra={}) => ({configured:true,email:qqAddress('synthetic'),enabled:true,automatic:false,interval_minutes:15,lookback_days:30,authorization_code_set:true,last_sync_at:null,last_result:null,connection_test:null,...extra});
const customMailbox = (extra={}) => mailbox({provider:'custom',email:'synthetic@example.test',host:'imap.example.test',port:993,username:'synthetic-user',credential_set:true,authorization_code_set:false,...extra});
const notice = (extra={}) => ({id:'synthetic-notice',revision:3,title:'招聘通知',status:'unconfirmed',notice_type:'interview',subject:'合成公司面试邀请',sender:'Recruiting <synthetic@example.test>',received_at:'2026-10-16T02:00:00+08:00',observed_at:'2026-10-16T03:00:00+08:00',summary:'现场编程并提问理论知识',raw_text:'面试时间：10月16日下午3点\n现场编程；梳理项目难点。',time_candidates:['10月16日下午3点'],scheduled_at:null,application_id:null,match_status:'unmatched',requires_review:true,source:'mail',...extra});
async function setup(){
 const ui=await app({withAccounts:true});let status=mailbox();let notices=[notice()];
 ui.handlers['mail.status']=()=>structuredClone(status);
 ui.handlers['mail.list']=args=>({items:structuredClone(notices.slice(args.offset,args.offset+args.limit)),total:notices.length,next_offset:args.offset+args.limit<notices.length?args.offset+args.limit:null});
 return Object.assign(ui,{setStatus:value=>{status=value;},setNotices:value=>{notices=value;}});
}
async function submit(ui,id){const form=ui.el(id);await form.onsubmit({target:form,preventDefault(){}});}

test('settings reads redacted QQ configuration without testing or importing mail',async()=>{
 const ui=await setup();await ui.render('settings');
 assert.equal(ui.el('mail-fields').disabled,false);assert.equal(ui.el('mail-email').value,qqAddress('synthetic'));
 assert.equal(ui.el('mail-provider').value,'qq');assert.equal(ui.el('mail-custom-fields').hidden,true);assert.equal(ui.el('mail-host').disabled,true);
 assert.equal(ui.el('mail-code').type,'password');assert.equal(ui.el('mail-code').value,'');assert.equal(ui.el('mail-code').required,false);
 assert.equal(ui.el('mail-test').disabled,false);assert.equal(ui.el('mail-sync').disabled,false);
 assert.deepEqual(ui.mailCalls.map(call=>call.operation),['mail.status']);
 assert.match(ui.el('mail-test-status').textContent,/尚未测试/);assert.match(ui.el('mail-sync-status').textContent,/尚未同步/);
 const help=ui.document.querySelectorAll('a').find(element=>element.attributes.href==='https://help.mail.qq.com/detail/106/985');
 assert.equal(help.attributes.target,'_blank');assert.match(help.attributes.rel,/noopener/);
 assert.match(ui.el('content').innerHTML,/本地服务持续运行/);
});

test('QQ authorization code is sent only during save, cleared, and never echoed or copied into settings',async()=>{
 const ui=await setup();await ui.render('settings');let saved;
 ui.handlers['mail.configure']=args=>{saved=structuredClone(args);ui.setStatus(mailbox({automatic:true,interval_minutes:20}));return mailbox();};
 ui.el('mail-code').value='SYNTHETICCODEAAA';ui.el('mail-automatic').checked=true;ui.el('mail-interval').value='20';
 await submit(ui,'mail-form');
 assert.deepEqual(saved,{provider:'qq',email:qqAddress('synthetic'),enabled:true,automatic:true,interval_minutes:20,lookback_days:30,password:'SYNTHETICCODEAAA'});
 assert.equal(ui.el('mail-code').value,'');assert.doesNotMatch(ui.el('content').innerHTML,/SYNTHETICCODEAAA/);assert.doesNotMatch(JSON.stringify(ui.stored),/SYNTHETICCODEAAA/);
 assert.equal(ui.mailCalls.find(call=>call.operation==='mail.configure').user_id,'synthetic-user-a');
 assert.deepEqual(ui.mailCalls.map(call=>call.operation),['mail.status','mail.configure','mail.status']);
 await submit(ui,'mail-form');assert.equal('password' in ui.mailCalls.filter(call=>call.operation==='mail.configure').at(-1).args,false);assert.equal('authorization_code' in saved,false);
 assert.match(ui.el('mail-status').textContent,/每 20 分钟/);
});

test('changed mailbox and unsaved options must be saved before connection test or sync',async()=>{
 const ui=await setup();await ui.render('settings');ui.el('mail-email').value=qqAddress('another');ui.el('mail-email').oninput();
 assert.equal(ui.el('mail-code').required,true);assert.equal(ui.el('mail-test').disabled,true);assert.equal(ui.el('mail-sync').disabled,true);
 await ui.el('mail-test').click();await ui.el('mail-sync').click();assert.equal(ui.mailCalls.length,1);
 ui.el('mail-email').value=qqAddress('synthetic');ui.el('mail-email').oninput();ui.el('mail-enabled').checked=false;ui.el('mail-enabled').onchange();
 assert.equal(ui.el('mail-automatic').checked,false);assert.equal(ui.el('mail-automatic').disabled,true);assert.equal(ui.el('mail-sync').disabled,true);
});

test('custom IMAP reads connection fields and credential_set without exposing or requiring a legacy QQ authorization code',async()=>{
 const ui=await setup();ui.setStatus(customMailbox());await ui.render('settings');
 assert.match(ui.el('content').innerHTML,/邮箱通知自动同步/);assert.equal(ui.el('mail-provider').value,'custom');assert.equal(ui.el('mail-custom-fields').hidden,false);assert.equal(ui.el('mail-qq-help').hidden,true);assert.equal(ui.el('mail-custom-help').hidden,false);
 assert.equal(ui.el('mail-host').value,'imap.example.test');assert.equal(ui.el('mail-port').value,'993');assert.equal(ui.el('mail-port').attributes.readonly,'');assert.equal(ui.el('mail-username').value,'synthetic-user');assert.equal(ui.el('mail-code').value,'');assert.equal(ui.el('mail-code').required,false);
 assert.equal(ui.el('mail-test').disabled,false);assert.equal(ui.mailCalls.some(call=>call.operation==='mail.configure'),false);
});

test('custom IMAP saves an arbitrary application password without trimming, echoing, or sending the legacy secret field',async()=>{
 const ui=await setup();ui.setStatus(customMailbox());await ui.render('settings');const password='  Synthetic app password!<&>  ';let saved;
 ui.handlers['mail.configure']=args=>{saved=structuredClone(args);return customMailbox();};ui.el('mail-code').value=password;await submit(ui,'mail-form');
 assert.deepEqual(saved,{provider:'custom',email:'synthetic@example.test',host:'imap.example.test',port:993,username:'synthetic-user',enabled:true,automatic:false,interval_minutes:15,lookback_days:30,password});
 assert.equal('authorization_code' in saved,false);assert.equal(ui.el('mail-code').value,'');assert.doesNotMatch(ui.el('content').innerHTML,/Synthetic app password/);assert.doesNotMatch(JSON.stringify(ui.stored),/Synthetic app password/);
 await submit(ui,'mail-form');assert.equal('password' in ui.mailCalls.filter(call=>call.operation==='mail.configure').at(-1).args,false);
});

test('every IMAP connection identity change requires a new credential and discards an entered secret',async()=>{
 const changes=[['mail-host','new-imap.example.test'],['mail-port','1993'],['mail-username','another-user'],['mail-email','another@example.test'],['mail-provider','qq']];
 for(const [id,value] of changes){
  const ui=await setup();ui.setStatus(customMailbox());await ui.render('settings');const field=ui.el(id),old=field.value;ui.el('mail-code').value='SYNTHETIC_OLD_ENDPOINT_PASSWORD';field.value=value;if(id==='mail-provider')field.onchange();else field.oninput();
  assert.equal(ui.el('mail-code').value,'');assert.equal(ui.el('mail-code').required,true);assert.equal(ui.el('mail-test').disabled,true);assert.equal(ui.el('mail-sync').disabled,true);
  field.value=old;if(id==='mail-provider')field.onchange();else field.oninput();assert.equal(ui.el('mail-code').required,false);assert.equal(ui.el('mail-test').disabled,false);
 }
});

test('custom username may deliberately default to the email address while changing a saved login needs a new credential',async()=>{
 const ui=await setup();ui.setStatus(customMailbox({username:'synthetic@example.test'}));await ui.render('settings');ui.el('mail-username').value='';ui.el('mail-username').oninput();assert.equal(ui.el('mail-code').required,false);
 ui.el('mail-username').value='different-login';ui.el('mail-username').oninput();assert.equal(ui.el('mail-code').required,true);
});

test('late custom configuration results cannot overwrite a switched account endpoint or its entered credential',async()=>{
 const ui=await setup(),pending=deferred();ui.setStatus(customMailbox());await ui.render('settings');ui.handlers['mail.configure']=()=>pending.promise;ui.el('mail-code').value='OLD_ACCOUNT_APP_PASSWORD';const saving=submit(ui,'mail-form');await tick();
 ui.setStatus(customMailbox({email:'new-account@example.test',host:'new-imap.example.test',username:'new-login'}));await ui.evaluate("switchAccount('synthetic-user-b')");ui.el('mail-code').value='NEW_ACCOUNT_APP_PASSWORD';pending.resolve(customMailbox());await saving;
 assert.equal(ui.el('mail-host').value,'new-imap.example.test');assert.equal(ui.el('mail-username').value,'new-login');assert.equal(ui.el('mail-code').value,'NEW_ACCOUNT_APP_PASSWORD');assert.equal(ui.el('notice').hidden,true);
});

test('connection test is distinct from syncing and does not claim imported messages',async()=>{
 const ui=await setup();await ui.render('settings');ui.handlers['mail.test']=()=>mailbox({connection_test:{status:'passed',message:'连接成功',readonly:true}});
 await ui.el('mail-test').click();assert.match(ui.el('mail-test-status').textContent,/通过/);assert.match(ui.el('mail-test-status').textContent,/尚未验证/);assert.match(ui.el('mail-sync-status').textContent,/尚未同步/);
 assert.equal(ui.mailCalls.some(call=>call.operation==='mail.sync'),false);
 ui.handlers['mail.test']=()=>mailbox({connection_test:{status:'failed',message:'授权失败'}});await ui.el('mail-test').click();
 assert.match(ui.el('mail-test-status').textContent,/授权失败/);assert.equal(ui.el('mail-fields').disabled,false);
});

test('a failed configuration read keeps the form disabled and offers a retry',async()=>{
 const ui=await setup();ui.handlers['mail.status']=()=>apiError('mail_config_invalid','合成配置暂不可读');await ui.render('settings');
 assert.equal(ui.el('mail-fields').disabled,true);assert.equal(ui.el('mail-retry').hidden,false);assert.match(ui.el('mail-error').textContent,/暂不可读/);
 ui.handlers['mail.status']=()=>mailbox();await ui.el('mail-retry').click();assert.equal(ui.el('mail-fields').disabled,false);assert.equal(ui.el('mail-error').hidden,true);assert.equal(ui.el('mail-retry').hidden,true);
});

test('failed save clears authorization input and distinguishes a saved configuration with failed readback',async()=>{
 const ui=await setup();await ui.render('settings');ui.el('mail-code').value='SYNTHETIC_SECRET';ui.handlers['mail.configure']=()=>apiError('mail_config_invalid','合成保存失败');await submit(ui,'mail-form');
 assert.equal(ui.el('mail-code').value,'');assert.match(ui.el('mail-error').textContent,/配置未保存/);
 ui.handlers['mail.configure']=()=>mailbox();ui.handlers['mail.status']=()=>apiError('mail_config_invalid','合成读取失败');await submit(ui,'mail-form');
 assert.match(ui.el('mail-error').textContent,/配置已保存，但读取确认失败/);assert.equal(ui.el('mail-fields').disabled,true);assert.equal(ui.el('mail-retry').hidden,false);
});

test('mail notification content and time fragments are escaped and recommendations are excluded from link choices',async()=>{
 const ui=await setup();ui.setNotices([notice({subject:'<script>subject</script>',sender:'<img src=x onerror=bad()>',summary:'<a href="javascript:bad()">summary</a>',raw_text:'<script>body</script>\nhttps://untrusted.example.test',time_candidates:['<svg onload=bad()>']})]);
 ui.stored.records.application=[{id:'actual',kind:'application',data:{company:'合成公司',title:'算法工程师',stage:'user_reported'},revision:2},{id:'closed',kind:'application',data:{company:'历史已投公司',title:'算法工程师',stage:'closed'},revision:1},{id:'recommendation',kind:'application',data:{company:'未选择公司',title:'推荐岗位',stage:'candidate'},revision:1}];
 await ui.evaluate("navigate('mailbox')");const html=ui.el('content').innerHTML;assert.match(html,/&lt;script&gt;subject/);assert.match(html,/&lt;img/);assert.match(html,/&lt;svg/);assert.doesNotMatch(html,/<script>|<img|<svg/);
 assert.match(html,/时间与要求待复核/);assert.match(html,/尚未关联申请/);await ui.document.querySelector('[data-mail-detail]').click();
 assert.match(ui.el('detail-content').innerHTML,/&lt;script&gt;body/);assert.equal(ui.el('detail-content').querySelectorAll('a').length,0);
 assert.equal(ui.el('mail-application').attributes['aria-label'],'关联已有申请');assert.equal(ui.el('mail-application').querySelectorAll('option').length,3);assert.match(ui.el('detail-content').innerHTML,/历史已投公司/);assert.doesNotMatch(ui.el('detail-content').innerHTML,/未选择公司/);assert.equal(ui.mailCalls.some(call=>call.operation==='mail.link'),false);
});

test('explicit association uses notice revision and selected actual application without changing its website status',async()=>{
 const ui=await setup();ui.stored.records.application=[{id:'actual',kind:'application',data:{company:'合成公司',title:'算法工程师',stage:'submitted',raw_status:'原始官网状态'},revision:2}];
 ui.handlers['mail.link']=args=>notice({revision:4,application_id:args.application_id,match_status:'manual'});await ui.evaluate("navigate('mailbox')");await ui.document.querySelector('[data-mail-detail]').click();ui.el('mail-application').value='actual';await submit(ui,'mail-link-form');
 assert.deepEqual(ui.mailCalls.find(call=>call.operation==='mail.link'),{operation:'mail.link',args:{id:'synthetic-notice',revision:3,application_id:'actual'},user_id:'synthetic-user-a'});
 assert.equal(ui.el('detail').open,false);assert.match(ui.el('mail-notices').innerHTML,/关联申请：合成公司/);assert.equal(ui.stored.records.application[0].data.raw_status,'原始官网状态');
 assert.equal(ui.calls.some(call=>call.operation==='record.save'||call.operation==='application.jd.save'),false);
});

test('an existing association missing from the current application snapshot remains explicit',async()=>{
 const ui=await setup();ui.setNotices([notice({application_id:'missing-application',match_status:'manual'})]);await ui.evaluate("navigate('mailbox')");
 assert.match(ui.el('content').innerHTML,/关联申请暂未读到/);assert.doesNotMatch(ui.el('content').innerHTML,/尚未关联申请/);await ui.document.querySelector('[data-mail-detail]').click();assert.match(ui.el('detail-content').innerHTML,/已存关联申请当前未读到/);
 assert.equal(ui.mailCalls.some(call=>call.operation==='mail.link'),false);
});

test('paginated notifications use server offsets and do not import messages just by opening the page',async()=>{
 const ui=await setup();ui.setNotices(Array.from({length:51},(_,index)=>notice({id:'notice-'+index,subject:'合成通知 '+index})));await ui.evaluate("navigate('mailbox')");
 assert.equal(ui.el('mail-prev').disabled,true);assert.equal(ui.el('mail-next').disabled,false);await ui.el('mail-next').click();
 assert.deepEqual(ui.mailCalls.filter(call=>call.operation==='mail.list').map(call=>call.args),[{limit:50,offset:0},{limit:50,offset:50}]);assert.match(ui.el('content').innerHTML,/51–51 \/ 51/);assert.equal(ui.el('mail-next').disabled,true);
 await ui.el('mail-prev').click();assert.equal(ui.mailCalls.filter(call=>call.operation==='mail.list').at(-1).args.offset,0);assert.equal(ui.mailCalls.some(call=>call.operation==='mail.sync'),false);
});

test('sync displays counts and supports continuing a bounded batch with more messages',async()=>{
 const ui=await setup();await ui.evaluate("navigate('mailbox')");let count=0;ui.handlers['mail.sync']=()=>{count++;const result={status:'passed',imported:count,duplicates:2,pending_review:count,has_more:count===1};const status=mailbox({last_result:result,last_sync_at:'2026-10-16T05:00:00Z'});ui.setStatus(status);return {...status,...result};};
 await ui.el('mail-page-sync').click();assert.match(ui.el('content').innerHTML,/新增 1 条，重复 2 条/);assert.equal(ui.el('mail-page-sync').textContent,'继续同步通知');
 await ui.el('mail-page-sync').click();assert.equal(ui.el('mail-page-sync').textContent,'立即同步通知');assert.equal(ui.mailCalls.filter(call=>call.operation==='mail.sync').length,2);
 ui.handlers['mail.sync']=()=>apiError('mail_unreachable','合成邮箱不可达');await ui.el('mail-page-sync').click();assert.match(ui.el('mail-sync-status').textContent,/同步失败/);assert.equal(ui.el('mail-page-sync').disabled,false);assert.equal(ui.el('mail-notices').querySelectorAll('article').length,1);
});

test('late settings reads, saves and tests are discarded after switching accounts',async()=>{
 for(const operation of ['mail.status','mail.configure','mail.test']){
  const ui=await setup(),pending=deferred();if(operation!=='mail.status')await ui.render('settings');ui.handlers[operation]=()=>pending.promise;
  let running;if(operation==='mail.status')running=ui.render('settings');else if(operation==='mail.configure'){ui.el('mail-code').value='OLD_ACCOUNT_SECRET';running=submit(ui,'mail-form');}else running=ui.el('mail-test').click();
  await tick();ui.handlers[operation]=()=>mailbox({email:qqAddress('new-account')});ui.handlers['mail.status']=()=>mailbox({email:qqAddress('new-account')});await ui.evaluate("switchAccount('synthetic-user-b')");pending.resolve(mailbox({email:qqAddress('old-account')}));await running;
  assert.equal(ui.el('mail-email').value,qqAddress('new-account'));assert.doesNotMatch(ui.el('content').innerHTML,/old-account|OLD_ACCOUNT_SECRET/);assert.equal(ui.el('notice').hidden,true);
 }
});

test('a completed import with failed list refresh keeps the saved outcome distinct from the refresh failure',async()=>{
 const ui=await setup();await ui.evaluate("navigate('mailbox')");
 ui.handlers['mail.sync']=()=>{const status=mailbox({last_result:{status:'passed',imported:2,duplicates:0,pending_review:2}});ui.setStatus(status);return status;};ui.handlers['mail.list']=()=>apiError('synthetic_read_failed','合成列表不可读');
 await ui.el('mail-page-sync').click();assert.match(ui.el('notice').textContent,/已同步，但列表刷新失败/);assert.match(ui.el('mail-page-status').textContent,/已保存/);assert.match(ui.el('mail-sync-status').textContent,/新增 2 条/);assert.equal(ui.el('mail-page-sync').disabled,false);
});

test('an in-flight configuration save prevents duplicate saves, tests and syncs',async()=>{
 const ui=await setup(),pending=deferred();await ui.render('settings');ui.handlers['mail.configure']=()=>pending.promise;
 const saving=submit(ui,'mail-form');await tick();await submit(ui,'mail-form');await ui.el('mail-test').click();await ui.el('mail-sync').click();
 assert.equal(ui.mailCalls.filter(call=>call.operation==='mail.configure').length,1);assert.equal(ui.el('mail-fields').disabled,true);assert.equal(ui.mailCalls.some(call=>['mail.test','mail.sync'].includes(call.operation)),false);
 pending.resolve(mailbox());await saving;assert.equal(ui.el('mail-fields').disabled,false);
});

test('late notification loads, syncs and links cannot display or associate data in the new account',async()=>{
 for(const operation of ['mail.list','mail.sync','mail.link']){
  const ui=await setup(),pending=deferred();ui.stored.records.application=[{id:'actual',kind:'application',data:{company:'合成公司',title:'算法工程师',stage:'user_reported'},revision:1}];if(operation!=='mail.list')await ui.evaluate("navigate('mailbox')");ui.handlers[operation]=()=>pending.promise;
  let running;if(operation==='mail.list')running=ui.evaluate("navigate('mailbox')");else if(operation==='mail.sync')running=ui.el('mail-page-sync').click();else{await ui.document.querySelector('[data-mail-detail]').click();ui.el('mail-application').value='actual';running=submit(ui,'mail-link-form');}
  await tick();ui.handlers['mail.list']=()=>({items:[],total:0,next_offset:null});await ui.evaluate("switchAccount('synthetic-user-b')");pending.resolve(operation==='mail.list'?{items:[notice({subject:'Old account private notice'})],total:1,next_offset:null}:operation==='mail.link'?notice({application_id:'actual'}):mailbox({last_result:{imported:99}}));await running;
  assert.doesNotMatch(ui.el('content').innerHTML,/Old account private notice/);assert.equal(ui.el('detail').open||false,false);assert.equal(ui.el('notice').hidden,true);assert.equal(ui.evaluate('mailNotices.length'),0);
 }
});

test('late link completion cannot close a newer selected notification',async()=>{
 const ui=await setup(),pending=deferred();ui.setNotices([notice(),notice({id:'second-notice',subject:'Second notice'})]);ui.stored.records.application=[{id:'actual',kind:'application',data:{company:'合成公司',title:'算法工程师',stage:'interview'},revision:1}];ui.handlers['mail.link']=()=>pending.promise;
 await ui.evaluate("navigate('mailbox')");ui.evaluate("showMailNotice('synthetic-notice')");ui.el('mail-application').value='actual';const linking=submit(ui,'mail-link-form');await tick();ui.evaluate("showMailNotice('second-notice')");const html=ui.el('detail-content').innerHTML;
 pending.resolve(notice({revision:4,application_id:'actual'}));await linking;assert.equal(ui.el('detail').open,true);assert.equal(ui.el('detail-content').innerHTML,html);
});
