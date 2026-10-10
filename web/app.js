'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const titles={overview:'概览',profile:'个人资料',recommendations:'推荐岗位',applications:'投递情况',documents:'简历材料',tasks:'Agent 任务',resources:'公司资源',settings:'设置与跟进'};
const labels={basic:'基础信息',education:'教育经历',experience:'项目与经历',family:'家庭简况',preference:'求职偏好',application:'申请记录'};
const stageNames={candidate:'候选岗位',preparing:'准备中',profile_saved:'简历已保存',user_reported:'本人报告已投',submitted:'已核验投递',assessment:'测评',interview:'面试',offer:'录用',closed:'已结束',unknown:'待核验'};
const statusNames={confirmed:'已确认',unconfirmed:'待确认',observed:'官网观察',unknown:'未知'};
const fields={
 basic:[['name','姓名'],['email','邮箱','email'],['phone','电话'],['location','所在地'],['summary','个人简介','textarea']],
 education:[['school','学校'],['degree','学历'],['major','专业'],['department','院系'],['start_date','开始年月'],['end_date','结束／预计毕业年月'],['description','补充说明','textarea']],
 experience:[['title','经历名称'],['category','类别','select',{project:'项目',work:'工作',research:'研究',publication:'论文',skill:'技能',award:'获奖'}],['organization','单位／团队'],['role','本人角色'],['start_date','开始年月'],['end_date','结束年月'],['description','内容与个人贡献','textarea']],
 family:[['relation','亲属关系'],['name','姓名（按需填写）'],['organization','任职单位'],['employment_status','任职状态','select',{unknown:'不了解',current:'在职',former:'曾任职',none:'无任职'}],['department','部门'],['role','职务'],['confirmed_at','确认日期'],['notes','补充说明','textarea']],
 preference:[['category','偏好类别','select',{roles:'岗位方向',cities:'城市',industries:'行业',excluded:'排除条件',other:'其他'}],['value','偏好内容','textarea'],['notes','补充说明','textarea']],
 application:[['company','公司'],['title','岗位名称'],['url','官网链接','url'],['city','城市'],['program','招聘项目'],['job_id','岗位编号'],['application_id','申请编号'],['stage','当前程度','select',stageNames],['raw_status','官网原始状态'],['checked_at','最后成功核验时间'],['last_attempt_at','最近尝试核验时间'],['coverage','核验范围','select',{unknown:'尚未核验',partial:'部分页面',complete:'完整范围'}],['evidence_url','核验依据链接','url'],['decision','本人决定（保留原话）'],['next_action','下一步安排','textarea'],['blocker','当前阻点','textarea'],['notes','补充备注','textarea']]
};
let state={records:{},documents:[],settings:{}},page='overview',profileTab='basic',resources=[],editing=null,query='',stageFilter='',progressFilter='open',companyFilter='',collapsedCompanies=new Set(),recommendQuery='',recommendType='campus',recommendCompany='',recommendCity='',collapsedRecommendationCompanies=new Set(),preparingRecommendations=new Set(),applicationLoadInfo={complete:true,expected:0},loadVersion=0,otpState=null,renderVersion=0,otpBusy=false,accounts=[],accountEpoch=0,accountSwitching=false,taskRecords=[],taskTotal=0,taskOffset=0,taskStatus='',taskEditing=null;
let syncState=null,syncBusy=false,publicRegistry=null,registryBusy=false;
function accountContext(){return {userId:state.user_id||state.account?.id||null,epoch:accountEpoch};}
function ensureAccount(context){if(context.epoch!==accountEpoch){const error=Error('账号已切换，旧账号的操作结果已丢弃。');error.code='stale_account';throw error;}}
function staleAccount(error){return error.code==='stale_account';}
async function api(operation,args={},context=accountContext()){
 ensureAccount(context);
 if(accountSwitching&&!['account.select','account.create'].includes(operation)){const error=Error('正在切换账号，请稍后操作。');error.code='stale_account';throw error;}
 const isState=operation==='state',url=isState?'/api/state'+(context.userId?'?user_id='+encodeURIComponent(context.userId):''):'/api/action';
 const opts=isState?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({operation,args,user_id:context.userId})};
 let res,obj;try{res=await fetch(url,opts);try{obj=await res.json()}catch{throw Error('本地服务返回异常，请确认服务正在运行。')}}catch(error){ensureAccount(context);throw error;}
 ensureAccount(context);
 if(!res.ok||!obj.ok){const error=Error(obj.error?.message||'操作未完成');error.code=obj.error?.code;throw error;}return obj.data;
}
function notify(message,error=false){if(error){$('#notice').textContent=message;$('#notice').hidden=false}else{$('#notice').hidden=true;$('#toast').textContent=message;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,3500)}}
async function load(recoverAccount=true){
 if(accountSwitching)return false;
 const version=++loadVersion,context=accountContext();
 try{
  const nextState=await api('state',{},context),nextContext={...context,userId:nextState.user_id||nextState.account?.id||context.userId};
  if(version!==loadVersion)return false;
  const initial=nextState.records?.application||[],expected=nextState.counts?.application;
  const nextCoverage={complete:true,expected:Number.isInteger(expected)?expected:initial.length};
  if(Number.isInteger(expected)&&expected>initial.length){
   const collected=[...initial],ids=new Set(initial.map(r=>r.id));
   try{
    for(let offset=initial.length;offset<expected;){
     const result=await api('record.list',{kind:'application',limit:200,offset},nextContext);
     if(version!==loadVersion)return false;
     if(result.total!==expected||!Array.isArray(result.items)||!result.items.length||new Set(result.items.map(r=>r.id)).size!==result.items.length||result.items.some(r=>r.kind!=='application'||ids.has(r.id)))throw Error('记录在读取时发生变化');
     for(const record of result.items){ids.add(record.id);collected.push(record)}
     offset+=result.items.length;
    }
    if(collected.length!==expected)throw Error('记录数量不一致');
   }catch(error){if(staleAccount(error))throw error;nextCoverage.complete=false;}
   nextState.records.application=collected;
  }
  let nextResources=resources;
  if(page==='resources')nextResources=(await api('resource.list',{},nextContext)).items||[];
  let nextTasks={items:taskRecords,total:taskTotal};
  if(page==='tasks')nextTasks=await api('task.list',{limit:50,offset:taskOffset,status:taskStatus||undefined},nextContext);
  if($('#account-select')){
   const result=await fetch('/api/accounts');const payload=await result.json();ensureAccount(context);
   if(!result.ok||!payload.ok)throw Error(payload.error?.message||'读取本地账号失败');accounts=payload.data.items||[];
  }
  if(version!==loadVersion)return false;
  state=nextState;applicationLoadInfo=nextCoverage;resources=nextResources;taskRecords=nextTasks.items||[];taskTotal=nextTasks.total||0;renderAccounts();
  $('#save-state').textContent='已读取 '+new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
  await render();return version===loadVersion;
 }catch(e){
  if(version===loadVersion&&recoverAccount&&['account_changed','account_context_required'].includes(e.code)){
   ++accountEpoch;clearAccountView();renderAccounts();const loaded=await load(false);if(loaded)notify('其他页面已切换账号，已读取当前账号的数据库资料。');return loaded;
  }
  if(version===loadVersion&&!staleAccount(e))notify(e.message,true);return false;
 }
}
function renderAccounts(){const select=$('#account-select');if(!select)return;const id=accountContext().userId;select.innerHTML=accounts.map(a=>`<option value="${esc(a.id)}" ${a.id===id?'selected':''}>${esc(a.name)}</option>`).join('');select.disabled=accountSwitching||!accounts.length;$('#account-new').disabled=accountSwitching||!id;select.title=state.account?`本地账号 ID：${state.account.id}`:'本地用户资料分区';}
function clearAccountView(){
 ++loadVersion;++renderVersion;state={records:{},documents:[],settings:{}};resources=[];editing=null;taskEditing=null;otpState=null;otpBusy=false;publicRegistry=null;registryBusy=false;
 profileTab='basic';query='';stageFilter='';progressFilter='open';companyFilter='';collapsedCompanies.clear();recommendQuery='';recommendType='campus';recommendCompany='';recommendCity='';collapsedRecommendationCompanies.clear();preparingRecommendations.clear();taskRecords=[];taskTotal=0;taskOffset=0;taskStatus='';applicationLoadInfo={complete:true,expected:0};
 $$('dialog').forEach(dialog=>{if(dialog.open)dialog.close();});$('#notice').hidden=true;$('#toast').hidden=true;$('#content').innerHTML=empty('正在切换账号','正在读取所选账号的数据库资料…');$('#save-state').textContent='';
}
async function switchAccount(id,fromCreate=false){
 if(accountSwitching)return false;const source=accountContext();if(id===source.userId&&!fromCreate)return true;
 accountSwitching=true;++accountEpoch;const context={...source,epoch:accountEpoch};clearAccountView();renderAccounts();
 try{await api('account.select',{id},context);accountSwitching=false;const loaded=await load();if(loaded)notify('已切换本地账号');return loaded;}
 catch(error){accountSwitching=false;renderAccounts();notify(error.message,true);await load();return false;}
}
async function createAccount(event){
 event.preventDefault();const context=accountContext(),button=event.target.querySelector('[type=submit]'),name=String(new FormData(event.target).get('name')||'').trim();if(!name)return;
 button.disabled=true;try{const account=await api('account.create',{name},context);ensureAccount(context);event.target.reset();$('#account-editor').close();await switchAccount(account.id||account.account?.id,true);}catch(error){if(!staleAccount(error))notify(error.message,true);}finally{button.disabled=false;}
}
function rows(kind){return state.records?.[kind]||[]}
function titleOf(r){const d=r.data||{};return d.name||d.title||d.school||d.relation||d.value||labels[r.kind]||'资料'}
function badge(text,type=''){return `<span class="badge ${type}">${esc(text)}</span>`}
function when(v){if(!v)return '尚未核验';const d=new Date(v);return Number.isNaN(d.getTime())?esc(v):d.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}
function empty(title,text,button=''){return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(text)}</p>${button}</div>`}
function editButton(kind,id='',text='编辑'){return `<button class="button secondary small" data-edit="${esc(kind)}" data-id="${esc(id)}">${text}</button>`}
function safeUrl(v){try{const u=new URL(v);return ['http:','https:'].includes(u.protocol)?u.href:null}catch{return null}}
function link(v,label='查看官网'){const u=safeUrl(v);return u?`<a href="${esc(u)}" target="_blank" rel="noreferrer noopener">${esc(label)}</a>`:''}
async function render(){
 const version=++renderVersion;
 document.title=`${titles[page]} · Career`;$('#page-title').textContent=titles[page];
 $$('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===page));
 const body=$('#content');
 if(page==='overview')body.innerHTML=overview();
 if(page==='profile')body.innerHTML=profile();
 if(page==='recommendations')body.innerHTML=recommendations();
 if(page==='applications')body.innerHTML=applications();
 if(page==='documents')body.innerHTML=documents();
 if(page==='tasks')body.innerHTML=tasks();
 if(page==='resources')body.innerHTML=resourcePage();
 if(page==='settings'){otpState=null;otpBusy=false;syncState=null;syncBusy=false;body.innerHTML=settings();}
 bind();
 if(page==='settings'){
  await Promise.all([
   (async()=>{try{const status=await api('otp.status');if(version===renderVersion)applyOtpStatus(status);}catch(error){if(version===renderVersion)$('#otp-status').textContent='读取验证码配置失败，请刷新后重试：'+otpError(error);}})(),
   (async()=>{try{const status=await api('sync.status');if(version===renderVersion)applySyncStatus(status,true);}catch(error){if(version===renderVersion)$('#sync-status').textContent='读取同步配置失败：'+error.message;}})()
  ]);
  pollSyncStatus(version);
 }
}
function applicationRecords(){return rows('application').filter(r=>r.data?.stage!=='candidate')}
function recommendationRecords(){return rows('application').filter(r=>r.data?.stage==='candidate')}
function applicationCoverageNotice(){return applicationLoadInfo.complete?'':`<div class="record-load-warning" role="alert">投递与推荐记录未全部读取：当前 ${rows('application').length} / ${applicationLoadInfo.expected} 条。以下数量仅代表已读取记录，请刷新后再核对；未读到的岗位不代表不存在。</div>`}
function recruitmentType(data){
 const text=[data.title,data.program].filter(Boolean).join(' ');
 if(/实习|\bintern(?:ship)?s?\b/i.test(text))return 'internship';
 if(/校招|校园招聘|应届|秋招|春招|\bcampus\b|\bgraduate recruitment\b|\bgraduate program\b/i.test(text))return 'campus';
 if(/社招|社会招聘|\bexperienced hire\b/i.test(text))return 'experienced';
 return 'unknown';
}
const recruitmentTypeNames={campus:'正式校招',internship:'历史实习候选',experienced:'社会招聘',unknown:'招聘类型待确认'};
const recommendationBuckets={recommended:'可推荐',exploratory:'探索项',pending:'待核验',excluded:'不推荐'};
const recommendationTiers={A:'A · 直接匹配',B:'B · 迁移匹配',C:'C · 探索匹配'};
function recommendationBucket(data){return Object.hasOwn(recommendationBuckets,data.recommendation_bucket)?data.recommendation_bucket:'pending'}
function recommendationTier(data){return Object.hasOwn(recommendationTiers,data.fit_tier)?data.fit_tier:null}
function compareText(a,b){return String(a??'').localeCompare(String(b??''),'zh-CN')}
function compareRecommendationTier(a,b){return ['A','B','C',null].indexOf(recommendationTier(a.data))-['A','B','C',null].indexOf(recommendationTier(b.data))}
function recommendationIdentity(record){const d=record.data;return [d.program,d.job_id||safeUrl(d.url)||d.title,record.id]}
function compareRecommendations(a,b){
 const tier=compareRecommendationTier(a,b);if(tier)return tier;
 const left=recommendationIdentity(a),right=recommendationIdentity(b);
 for(let i=0;i<left.length;i++){const order=compareText(left[i],right[i]);if(order)return order}return 0;
}
function recommendationSections(list){
 return Object.keys(recommendationBuckets).map(bucket=>{
  const groups=groupCompanies(list.filter(r=>recommendationBucket(r.data)===bucket));
  for(const items of groups.values())items.sort(compareRecommendations);
  return {bucket,groups:new Map([...groups].sort(([a,left],[b,right])=>compareRecommendationTier(left[0],right[0])||compareText(a,b)))};
 }).filter(section=>section.groups.size);
}
function groupCompanies(list){const groups=new Map();for(const r of list){const name=r.data.company||'公司待确认';if(!groups.has(name))groups.set(name,[]);groups.get(name).push(r)}return groups}
function companyNames(list){return [...new Set(list.map(r=>r.data.company||'公司待确认'))].sort((a,b)=>a.localeCompare(b,'zh-CN'))}
function recordCities(value){return value?String(value).split(/[/／、,，;；|\s]+/).filter(Boolean):['城市待确认']}

function groupedJobTables(groups,{collapsed,tableClass,head,row,summary,groupAttribute='data-company-group',groupKey=name=>name}){
 return [...groups].map(([name,items])=>`<details class="company-group" ${groupAttribute}="${esc(groupKey(name))}" ${collapsed.has(groupKey(name))?'':'open'}><summary><strong>${esc(name)}</strong><span>${esc(summary(items))}</span></summary><div class="panel table-wrap"><table class="${tableClass}"><thead><tr>${head.map(text=>`<th>${esc(text)}</th>`).join('')}</tr></thead><tbody>${items.map(row).join('')}</tbody></table></div></details>`).join('');
}
function overview(){
 const all=Object.values(state.records||{}).flat(),apps=applicationRecords(),candidates=recommendationRecords().filter(r=>recruitmentType(r.data)==='campus'),profileRecords=all.filter(r=>r.kind!=='application'),unconfirmed=profileRecords.filter(r=>r.status==='unconfirmed'||r.status==='unknown').length,basic=rows('basic')[0];
 return `${applicationCoverageNotice()}<div class="summary-grid"><div class="stat"><div class="label">投递记录</div><div class="number">${apps.length}</div><div class="hint">含准备中，不含推荐岗位</div></div><div class="stat"><div class="label">正式校招推荐</div><div class="number">${candidates.length}</div><button class="button ghost small" data-go="recommendations">查看推荐岗位</button></div><div class="stat"><div class="label">经历与资料</div><div class="number">${profileRecords.length}</div><div class="hint">可用于后续准备</div></div><div class="stat"><div class="label">简历材料</div><div class="number">${state.documents?.length||0}</div><div class="hint">原始材料与有效交付</div></div></div><div class="columns"><section class="panel"><div class="section-header"><h2>最近的申请记录</h2><button class="button ghost" data-go="applications">查看全部</button></div>${apps.length?apps.slice(0,5).map(r=>`<div class="list-line"><div class="record-top"><strong>${esc(r.data.company)} · ${esc(r.data.title)}</strong>${badge(stageNames[r.data.stage]||r.data.stage||'待核验','blue')}</div><p>${esc(r.data.city||'城市待确认')}　${esc(r.data.raw_status||'暂无官网原始状态')}</p></div>`).join(''):empty('还没有申请记录','可以在推荐岗位中选择岗位并开始准备，或让 Codex 读取已有投递。','<button class="button secondary small" data-go="recommendations">查看推荐岗位</button>')}</section><section class="panel"><div class="section-header"><h2>我的资料</h2><button class="button ghost" data-go="profile">查看资料</button></div>${basic?`<p class="profile-name">${esc(basic.data.name||'我的档案')}</p><p class="muted">${esc(basic.data.location||'所在地待补充')}</p><p class="record-summary">${esc(basic.data.summary||'从已经确认的经历开始准备。')}</p>`:empty('建立个人档案','导入本地简历供 Codex 整理，或直接手工补充。',editButton('basic','','填写资料'))}${unconfirmed?`<div class="pending-count">有 ${unconfirmed} 条资料待确认，可在个人资料中逐项查看。</div>`:''}</section></div><section class="panel"><div class="section-header"><h2>最近修改</h2><button class="button ghost" id="show-history">查看变更记录</button></div><p class="muted">资料来源、用户修正与公司专用信息分别保留。可以查看历史并撤销最近的修改。</p></section>`;
}
function profile(){const list=rows(profileTab);return `<div class="tabs">${['basic','education','experience','family','preference'].map(k=>`<button data-tab="${k}" class="${profileTab===k?'active':''}">${labels[k]} <span class="muted">${rows(k).length}</span></button>`).join('')}</div><div class="section-header"><div><h2>${labels[profileTab]}</h2><p>${profileTab==='family'?'只记录已知的家庭简况；没有记录不代表已确认无亲属任职。':profileTab==='preference'?'通用偏好和公司例外分别设置适用范围。':'保留事实来源，未知内容可以留空。'}</p></div>${editButton(profileTab,'','新增资料')}</div>${list.length?`<div class="card-grid">${list.map(r=>recordCard(r)).join('')}</div>`:empty('暂无'+labels[profileTab],'手工新增，或让 Codex 根据你提供的材料整理。')}<div class="plain-actions spaced"><button class="button secondary" id="export-profile">导出结构化 JSON</button><button class="button ghost" id="import-profile">导入档案 JSON</button><input type="file" id="profile-file" class="hidden-file" accept=".json"></div>`}
function recordCard(r){const d=r.data||{},parts=fields[r.kind].filter(([k])=>!['name','title','school','relation'].includes(k)).map(([k,l])=>d[k]?`${l}：${typeof d[k]==='object'?JSON.stringify(d[k]):d[k]}`:'').filter(Boolean);return `<article class="record-card"><div class="record-top"><h3>${esc(titleOf(r))}</h3>${editButton(r.kind,r.id)}</div><div class="record-summary">${esc(parts.join('\n').slice(0,900))}</div><div class="record-meta">${badge(statusNames[r.status]||r.status,r.status==='confirmed'?'good':'warning')}<span>${esc(r.scope||'global')}</span><span>${esc(r.source||'未标注来源')}</span><button class="button ghost small" data-history="${esc(r.id)}">修改记录</button><button class="button ghost small" data-archive="${esc(r.id)}">归档</button></div></article>`}
function applicationFollowup(data){if(applicationEnded(data))return `${data.decision?`<div class="record-summary">本人决定：${esc(data.decision)}</div>`:''}<small>已结束，停止例行跟进。历史待办仍保留在编辑和变更记录中。</small>`;const items=[['decision','本人决定'],['next_action','下一步'],['blocker','当前阻点']].filter(([key])=>data[key]);return items.length?items.map(([key,label])=>`<div class="record-summary">${label}：${esc(data[key])}</div>`).join(''):'<small>尚未记录后续安排</small>'}
function applicationStatusConflict(data){return data.stage==='closed'&&/(新投递|投递简历|筛选中|评估中|待处理)/.test(data.raw_status||'')}
function applicationEnded(data){return data.stage==='closed'&&!applicationStatusConflict(data)}
function applicationRow(r){const d=r.data,conflict=applicationStatusConflict(d);return `<tr><td><strong>${esc(d.title||'岗位待确认')}</strong>${link(d.url)}${d.job_id?`<br><small>岗位编号：${esc(d.job_id)}</small>`:''}${d.application_id?`<br><small>申请编号：${esc(d.application_id)}</small>`:''}</td><td>${esc(d.city||'待确认')}<br><small>${esc(d.program||'')}</small></td><td>${badge(conflict?'状态待核对':stageNames[d.stage]||'待核验',conflict?'warning':applicationEnded(d)?'':'blue')}</td><td>${esc(d.raw_status||'未记录')}<br><small>最后成功核验：${d.checked_at?when(d.checked_at):'未记录'}<br>${{complete:'完整范围',partial:'部分页面',unknown:'范围未知'}[d.coverage]||'范围未知'}${d.last_attempt_at?`<br>最近尝试核验：${when(d.last_attempt_at)}`:''}</small>${d.evidence_url?`<br>${link(d.evidence_url,'核验依据')}`:''}</td><td>${applicationFollowup(d)}</td><td>${editButton('application',r.id)}</td></tr>`}
function applications(){
 const all=applicationRecords(),companies=companyNames(all),list=all.filter(r=>{const d=r.data,ended=applicationEnded(d);return (!companyFilter||(d.company||'公司待确认')===companyFilter)&&(!stageFilter||d.stage===stageFilter)&&(progressFilter==='all'||(progressFilter==='open'&&!ended)||(progressFilter==='closed'&&ended)||(progressFilter==='preparing'&&['preparing','profile_saved'].includes(d.stage)))&&JSON.stringify(d).toLowerCase().includes(query.toLowerCase())}),groups=groupCompanies(list);
 return `${applicationCoverageNotice()}<div class="section-header"><div><h2>投递情况</h2><p>默认显示非已结束记录；推荐岗位单独查看，选定后进入准备中。</p></div>${editButton('application','','新增投递记录')}</div><div class="filters"><input id="app-query" aria-label="搜索公司或岗位" placeholder="搜索公司、岗位或城市" value="${esc(query)}"><select id="app-progress" aria-label="筛选跟进范围"><option value="open" ${progressFilter==='open'?'selected':''}>非已结束</option><option value="all" ${progressFilter==='all'?'selected':''}>全部投递记录</option><option value="closed" ${progressFilter==='closed'?'selected':''}>已结束</option><option value="preparing" ${progressFilter==='preparing'?'selected':''}>准备中与简历已保存</option></select><select id="app-company" aria-label="筛选公司"><option value="">全部公司</option>${companies.map(name=>`<option value="${esc(name)}" ${companyFilter===name?'selected':''}>${esc(name)}</option>`).join('')}</select><select id="app-stage" aria-label="筛选阶段"><option value="">全部阶段</option>${Object.entries(stageNames).filter(([k])=>k!=='candidate').map(([k,v])=>`<option value="${k}" ${k===stageFilter?'selected':''}>${v}</option>`).join('')}</select></div><p class="filter-summary">显示 ${list.length} / ${all.length} 条岗位，${groups.size} 家公司。公司标题可折叠。</p>${list.length?groupedJobTables(groups,{collapsed:collapsedCompanies,tableClass:'applications-table',head:['岗位','地点与项目','当前程度','官网状态与核验','后续安排','操作'],row:applicationRow,summary:items=>`${items.length} 条岗位 · ${items.filter(r=>!applicationEnded(r.data)).length} 条非已结束`}):empty('没有匹配的投递记录','调整筛选条件，或到推荐岗位选择岗位。','<button class="button secondary small" data-go="recommendations">查看推荐岗位</button>')}`;
}
function recommendationRow(r){
 const d=r.data,type=recruitmentType(d),bucket=recommendationBucket(d),tier=recommendationTier(d);
 return `<tr><td><strong>${esc(d.title||'岗位待确认')}</strong>${link(d.url)}${d.job_id?`<br><small>岗位编号：${esc(d.job_id)}</small>`:''}${d.evidence_url?`<br>${link(d.evidence_url,'岗位核验依据')}`:''}<br><small>岗位信息最后核验：${d.checked_at?when(d.checked_at):'未记录'}<br>${{complete:'完整范围',partial:'部分页面',unknown:'范围未知'}[d.coverage]||'范围未知'}</small></td><td>${esc(d.city||'城市待确认')}<br><small>${esc(d.program||'招聘项目待确认')}</small></td><td><div class="recommendation-badges">${badge(recruitmentTypeNames[type],type==='campus'?'blue':type==='unknown'?'warning':'')}${badge(recommendationBuckets[bucket],bucket==='recommended'?'good':bucket==='pending'?'warning':'')}${badge(tier?recommendationTiers[tier]:'未分档',tier==='A'?'blue':'')}</div>${d.recommendation_scope?`<small class="recommendation-scope">评估范围：${esc(d.recommendation_scope)}</small>`:tier?'<small class="recommendation-scope">评估范围待确认</small>':''}</td><td>${d.notes?`<div class="record-summary">${esc(d.notes)}</div>`:'<small>尚未记录匹配理由或资格限制</small>'}${['decision','next_action','blocker'].filter(key=>d[key]).map(key=>`<div class="record-summary">${{decision:'本人决定',next_action:'下一步',blocker:'待核问题'}[key]}：${esc(d[key])}</div>`).join('')}</td><td><div class="recommendation-actions">${editButton('application',r.id)}<button class="button small" data-prepare-recommendation="${esc(r.id)}" ${preparingRecommendations.has(r.id)?'disabled':''}>${preparingRecommendations.has(r.id)?'正在保存…':'选定并准备'}</button></div></td></tr>`;
}
function recommendations(){
 const all=recommendationRecords(),typed=all.filter(r=>recommendType==='all'||recruitmentType(r.data)===recommendType),companies=companyNames(typed),cities=[...new Set(typed.flatMap(r=>recordCities(r.data.city)))].sort((a,b)=>a.localeCompare(b,'zh-CN')),list=typed.filter(r=>(!recommendCompany||(r.data.company||'公司待确认')===recommendCompany)&&(!recommendCity||recordCities(r.data.city).includes(recommendCity))&&JSON.stringify(r.data).toLowerCase().includes(recommendQuery.toLowerCase())),sections=recommendationSections(list);
 return `${applicationCoverageNotice()}<div class="section-header"><div><h2>推荐岗位</h2><p>默认只推荐正式校招；实习候选需主动切换查看。招聘类型不明的岗位单列待确认。</p></div>${editButton('application','','新增推荐岗位')}</div><div class="recommendation-note">按资格状态分区，公司按当前可见岗位的最佳梯队排列，公司内按 A → B → C → 未分档排列。同梯队并列，不使用具体名次或更新时间。梯队只表示职责匹配；待核验岗位仍需确认资格。选择岗位后可转入准备中，由你审阅简历并决定最终投递。</div><div class="filters recommendation-filters"><input id="recommend-query" aria-label="搜索推荐岗位" placeholder="搜索公司、岗位、城市或匹配说明" value="${esc(recommendQuery)}"><select id="recommend-type" aria-label="筛选招聘类型">${Object.entries({...recruitmentTypeNames,all:'全部类型（含历史实习）'}).map(([key,text])=>`<option value="${key}" ${recommendType===key?'selected':''}>${esc(text)}</option>`).join('')}</select><select id="recommend-company" aria-label="筛选推荐公司"><option value="">全部公司</option>${companies.map(name=>`<option value="${esc(name)}" ${recommendCompany===name?'selected':''}>${esc(name)}</option>`).join('')}</select><select id="recommend-city" aria-label="筛选推荐城市"><option value="">全部城市</option>${cities.map(city=>`<option value="${esc(city)}" ${recommendCity===city?'selected':''}>${esc(city)}</option>`).join('')}</select></div><p class="filter-summary">显示 ${list.length} / ${typed.length} 条${recommendType==='all'?'推荐岗位':esc(recruitmentTypeNames[recommendType])}，${companyNames(list).length} 家公司。共保留 ${all.length} 条候选记录。</p>${list.length?sections.map(({bucket,groups})=>`<section class="recommendation-section" data-recommendation-bucket="${bucket}"><h3>${esc(recommendationBuckets[bucket])}</h3>${groupedJobTables(groups,{collapsed:collapsedRecommendationCompanies,tableClass:'recommendations-table',head:['岗位','地点与项目','类型与匹配梯队','匹配说明与资格限制','操作'],row:recommendationRow,summary:items=>{const tier=recommendationTier(items[0].data);return `${items.length} 条推荐岗位 · 最佳岗位${tier?`${tier}档${!items[0].data.recommendation_scope?'（范围待确认）':items[0].data.recommendation_scope!=='整岗'?'（限定范围）':''}`:'未分档'}`},groupAttribute:'data-recommendation-company',groupKey:name=>`${bucket}|${name}`})}</section>`).join(''):empty('暂无匹配的推荐岗位','调整公司、城市或搜索条件；类型待确认及历史实习可在招聘类型中切换查看。')}`;
}
async function prepareRecommendation(id,button){
 if(preparingRecommendations.has(id))return;
 const record=recommendationRecords().find(r=>r.id===id);
 if(!record)throw Error('岗位状态已变化，请刷新后再选择。');
 preparingRecommendations.add(id);button.disabled=true;
 try{
  const saved=await api('record.save',{kind:'application',id:record.id,revision:record.revision,data:{...record.data,stage:'preparing'},scope:record.scope||'global',status:record.status,source:'user_edit'});
  if(saved?.id!==record.id||saved.kind!=='application'||saved.data?.stage!=='preparing')throw Error('保存请求已完成，但返回状态无法确认，请刷新核对。');
  state.records.application=rows('application').map(r=>r.id===record.id?saved:r);
  query='';stageFilter='';progressFilter='preparing';companyFilter=record.data.company||'公司待确认';
  const refreshed=await navigate('applications');
  if(!refreshed){await render();notify('已保存，列表刷新失败，请刷新核对。',true);}
  else if(!applicationRecords().some(r=>r.id===record.id&&r.data.stage==='preparing'))notify('已保存，但最新列表尚未确认准备中状态，请刷新核对。',true);
  else notify('已选定并转入准备中，请审阅资料后决定投递。');
 }finally{preparingRecommendations.delete(id);button.disabled=false;}
}
function documents(){const docs=state.documents||[],account=accountContext().userId;return `<div class="upload-area"><div><h3>导入本地材料</h3><p>支持 Word、文本型 PDF、Markdown、文本和 JSON。导入后由 Codex 整理并向你确认。</p></div><button class="button" id="upload-document">选择文件</button><input type="file" id="document-file" class="hidden-file" accept=".docx,.pdf,.md,.txt,.json"></div><section class="panel table-wrap">${docs.length?`<table><thead><tr><th>材料</th><th>解析情况</th><th>导入时间</th><th>操作</th></tr></thead><tbody>${docs.map(d=>`<tr><td><strong>${esc(d.filename||d.original_name||d.name)}</strong><small>${((d.size||d.bytes||0)/1024).toFixed(1)} KB</small></td><td>${esc(d.extraction_status||d.status||'已导入')}</td><td>${when(d.created_at)}</td><td><div class="plain-actions"><button class="button secondary small" data-document="${esc(d.id)}">查看文本</button><a class="button ghost small" href="/api/documents/${encodeURIComponent(d.id)}${account?'?user_id='+encodeURIComponent(account):''}" download>下载原件</a></div></td></tr>`).join('')}</tbody></table>`:empty('还没有简历材料','原件保存在本机，不进入公开资源包。')}</section>`}
const taskKindNames={search:'岗位检索',prepare:'申请准备',followup:'进度跟进',custom:'自定义'},taskStatusNames={pending:'待执行',running:'执行中',completed:'已完成',cancelled:'已取消'};
function tasks(){return `<div class="section-header"><div><h2>Agent 任务</h2><p>任务保存在当前账号数据库。网页编辑后，Codex 下次执行读取最新要求与偏好。</p></div><button class="button" id="task-new">新建任务</button></div><div class="recommendation-note">保存任务不会直接启动 Codex 或创建自动化。任务说明仅引用账号与任务 ID；执行前读取数据库中的最新上下文，执行后写回进度。</div><div class="filters"><select id="task-status" aria-label="筛选任务状态"><option value="">全部状态</option>${Object.entries(taskStatusNames).map(([key,text])=>`<option value="${key}" ${taskStatus===key?'selected':''}>${esc(text)}</option>`).join('')}</select></div>${taskRecords.length?`<div class="card-grid">${taskRecords.map(task=>`<article class="record-card"><div class="record-top"><h3>${esc(task.title)}</h3>${badge(taskStatusNames[task.status]||task.status,task.status==='completed'?'good':task.status==='running'?'blue':'')}</div><p class="help">${esc(taskKindNames[task.kind]||task.kind)} · 更新于 ${when(task.updated_at||task.created_at)}</p><div class="record-summary">${esc(task.instructions||'尚未填写任务要求')}</div><div class="plain-actions"><button class="button secondary small" data-task-edit="${esc(task.id)}">编辑任务</button><button class="button secondary small" data-task-prompt="${esc(task.id)}">Codex 执行说明</button><button class="button ghost small" data-task-context="${esc(task.id)}">查看最新上下文</button></div></article>`).join('')}</div>`:empty('暂无匹配任务','新建任务，保存需要 Codex 执行的要求。','<button class="button secondary" data-task-create>新建任务</button>')}<div class="task-pagination"><span class="help">${taskTotal?`显示 ${taskOffset+1}–${Math.min(taskOffset+taskRecords.length,taskTotal)} / ${taskTotal} 条任务`:'0 条任务'}</span><div class="plain-actions"><button class="button secondary small" id="task-prev" ${taskOffset?'':'disabled'}>上一页</button><button class="button secondary small" id="task-next" ${taskOffset+taskRecords.length<taskTotal?'':'disabled'}>下一页</button></div></div>`;}
async function openTask(id){
 const context=accountContext(),task=id?await api('task.get',{id},context):null;ensureAccount(context);taskEditing={task,context};$('#task-editor-title').textContent=task?'编辑 Agent 任务':'新建 Agent 任务';
 $('#task-editor-fields').innerHTML=`<label>任务标题<input name="title" required maxlength="200" value="${esc(task?.title||'')}"></label><div class="form-grid"><label>任务类型<select name="kind">${Object.entries(taskKindNames).map(([key,text])=>`<option value="${key}" ${(task?.kind||'search')===key?'selected':''}>${esc(text)}</option>`).join('')}</select></label><label>任务状态<select name="status">${Object.entries(taskStatusNames).map(([key,text])=>`<option value="${key}" ${(task?.status||'pending')===key?'selected':''}>${esc(text)}</option>`).join('')}</select></label></div><label>具体要求<textarea name="instructions" rows="6" placeholder="例如：按我的最新偏好查找正式校招岗位，并记录来源与资格限制。">${esc(task?.instructions||'')}</textarea></label><details class="source-fields"><summary>补充任务数据（可选）</summary><label>JSON 对象<textarea name="data" class="mono" rows="5">${esc(JSON.stringify(task?.data||{},null,2))}</textarea><small>用于保存结构化参数或结果；个人偏好应在设置页维护，避免把旧偏好复制进任务。</small></label></details>`;$('#task-editor').showModal();
}
async function saveTask(event){
 event.preventDefault();if(!taskEditing)return;const {task,context}=taskEditing;ensureAccount(context);const form=new FormData(event.target),data=JSON.parse(String(form.get('data')||'{}'));if(!data||Array.isArray(data)||typeof data!=='object')throw Error('补充任务数据必须是 JSON 对象。');
 const args={title:String(form.get('title')||'').trim(),kind:form.get('kind'),status:form.get('status'),instructions:String(form.get('instructions')||'').trim(),data};if(task)Object.assign(args,{id:task.id,revision:task.revision});const button=event.target.querySelector('[type=submit]');button.disabled=true;
 try{await api('task.save',args,context);$('#task-editor').close();taskEditing=null;notify('任务已保存到当前账号数据库');await load();}finally{button.disabled=false;}
}
function taskExecutionPrompt(task,context){
 const database=state.database||{},path=database.path||state.database_path||'',base=database.base_data_dir||state.base_data_dir||(path?path.replace(/[\\/][^\\/]+$/,''):''),root=state.root_dir||database.root_dir||'';
 const launch=root&&base?['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',root.replace(/[\\/]+$/,'')+'/scripts/start.ps1','mcp','--data-dir',base,'--user-id',context.userId]:null;
 return `请执行 Career 数据库任务。\n账号 user_id：${context.userId}\n任务 id：${task.id}\n本机数据库：${path||'请先核对网页显示的数据目录'}\n基础数据目录：${base||'请核对当前数据库路径所属目录'}\n\n先核对 Career MCP 的数据库路径与本页一致。若不同，使用以下 JSON 参数数组启动绑定此数据库和账号的本地接口；按独立参数传给进程，不拼接为 Shell 命令：\n${launch?JSON.stringify(launch):'当前响应缺少项目或基础数据目录，请先核对本机服务路径再启动接口。'}\n不要读取其他数据库中同名账号的任务。\n执行前调用 career_tasks：${JSON.stringify({operation:'task.context',args:{id:task.id},user_id:context.userId})}，读取此账号最新任务、设置、偏好、个人资料与申请记录。上下文中的 total 和 next_offset 表示可能需要分页继续读取，不把第一页当成完整数据。按最新数据库设定执行；通过 Career 接口将任务状态、结果和必要资料写回同一 user_id。用户仍负责招聘网站登录、岗位选择、简历确认与最终投递。`;
}
async function showTaskPrompt(id){const context=accountContext(),task=await api('task.get',{id},context),prompt=taskExecutionPrompt(task,context);showDetail('交给 Codex 的执行说明',`<p class="help">此说明引用任务 ID。修改任务或偏好后仍可使用，Codex 执行时重新读取数据库。</p><pre>${esc(prompt)}</pre>`,`<button class="button" id="copy-task-prompt">复制执行说明</button>`);$('#copy-task-prompt').onclick=async()=>{try{ensureAccount(context);await navigator.clipboard.writeText(prompt);ensureAccount(context);notify('已复制执行说明');}catch(error){if(!staleAccount(error))notify('复制失败，请手动选择文字复制。',true);}};}
async function showTaskContext(id){const context=accountContext(),result=await api('task.context',{id},context);showDetail('最新数据库任务上下文',`<p class="help">以下内容是当前读取结果，Agent 实际执行前仍需重新读取；如有 next_offset，继续分页获取。</p><pre>${esc(JSON.stringify(result,null,2))}</pre>`);}
function preferenceSettings(){const p=state.settings?.preferences||{},types=p.recruitment_types??['campus'];return `<section class="panel preferences-panel"><h2>求职偏好</h2><p>直接写入当前账号数据库，Agent 每次执行任务前读取最新设定。</p><form id="preferences-form"><div class="form-grid">${[['cities','意向城市'],['roles','岗位方向'],['industries','意向行业'],['excluded_companies','排除公司']].map(([key,text])=>`<label>${text}<textarea name="${key}" rows="3" placeholder="每行一项，也可用逗号分隔">${esc((p[key]||[]).join('\n'))}</textarea></label>`).join('')}</div><fieldset class="preference-recruitment"><legend>招聘范围</legend><p class="help">新账号默认正式校招，可按需要调整。勾选实习表示明确纳入招聘范围；任务仍以本次要求为准。</p>${[['include_campus','campus','正式校招'],['include_social','social','社会招聘'],['include_internships','internship','实习']].map(([name,type,label])=>`<label class="check"><input name="${name}" type="checkbox" ${types.includes(type)?'checked':''}>${label}</label>`).join('')}</fieldset><label>补充偏好<textarea name="notes" rows="4" placeholder="例如：优先工作地点、希望避免的岗位职责。">${esc(p.notes||'')}</textarea></label><button class="button" type="submit">保存求职偏好</button></form></section>`;}
function preferenceList(value){return [...new Set(String(value||'').split(/[\n,，;；]+/).map(v=>v.trim()).filter(Boolean))];}
async function savePreferences(event){event.preventDefault();const context=accountContext(),form=new FormData(event.target),preferences={};for(const key of ['cities','roles','industries','excluded_companies'])preferences[key]=preferenceList(form.get(key));preferences.recruitment_types=[['include_campus','campus'],['include_internships','internship'],['include_social','social']].filter(([name])=>form.get(name)==='on').map(([,type])=>type);if(!preferences.recruitment_types.length)throw Error('请选择至少一种招聘类型。');preferences.notes=String(form.get('notes')||'').trim();const button=event.target.querySelector('[type=submit]');button.disabled=true;try{await saveSettings({preferences},context);}finally{button.disabled=false;}}
function capabilityBadges(c){return Object.entries(c||{}).filter(([,v])=>v?.status!=='unavailable').slice(0,5).map(([k,v])=>badge(({applications:'进度读取',profile_fields:'字段填写',resume:'简历读取',candidates:'岗位检索',login:'登录',search:'岗位搜索',field_fill:'字段填写',save:'保存',profile_prepare:'专业字段准备',resume_audit:'简历核验',candidate_catalog:'岗位目录',onboarding:'入口指引'}[k]||k)+(v?.status==='documentation'?' · 指引':''))).join('')}
function resourcePage(){return `<div class="section-header"><div><h2>公司资源包</h2><p>查看适用网站与已验证能力，按版本安装。</p></div><button class="button secondary" id="import-package">导入资源包</button><input type="file" id="package-file" class="hidden-file" accept=".zip"></div><div class="card-grid">${resources.map(r=>`<article class="resource-card"><div class="record-top"><h3>${esc(r.name||r.id)}</h3>${badge(r.installed?'已安装 '+(r.installed_version||r.version):'可用 '+r.version,r.installed?'good':'')}</div><p>${esc(r.description||'公司网站适配资源')}</p><div class="capabilities">${capabilityBadges(r.capabilities)}</div><div class="muted caption">${esc(r.origin||'')}<br>${r.verification?.live_reverified?'包内附有现场复核记录，请核对适用范围':'包内未附本版本现场复核结论；本地验收另行记录'}</div><div class="form-actions"><button class="button ghost small" data-resource-guide="${esc(r.id)}">使用说明</button><button class="button secondary small" data-export-package="${esc(r.id)}">导出共建包</button><button class="button small" data-install="${esc(r.id)}">${r.installed?'核对安装':'安装'}</button></div></article>`).join('')}</div>${publicResourceSection()}<section class="panel spaced"><h2>共建与下载</h2><p class="muted">导出的公司包只包含通用方法和代码。投稿前请预览内容；个人档案单独保存在本机。</p><div class="plain-actions"><a class="button secondary" href="/catalog.html" target="_blank" rel="noopener">打开公开目录预览</a>${safeUrl(state.settings.contribution_url)?link(state.settings.contribution_url,'打开投稿入口'):'<span class="help">发布仓库尚未配置，当前可导入／导出资源包。</span>'}</div></section>`}
function settings(){const s=state.settings||{},sc=s.schedule||{};return `<div class="settings-grid">${preferenceSettings()}<section class="panel"><h2>每日跟进</h2><p>生成任务说明时会先保存当前时间与范围，再交给 Codex 创建或更新自动化。此页面不会直接启动调度。</p><form id="schedule-form"><div class="form-grid"><label>检查时间<input name="time" type="time" required value="${esc(sc.time||'10:00')}"></label><label>时区<input name="timezone" required value="${esc(sc.timezone||'Asia/Shanghai')}"></label></div><label class="check"><input name="allow_relogin" type="checkbox" ${sc.allow_relogin?'checked':''}>允许通过已配置验证码来源重新登录</label><p class="help">已结束岗位保留历史，不再例行核验或提醒；同公司新岗位按本人后续决定处理。其余岗位无变化保持安静，登录失效或新状态需要处理时通知。</p><div class="plain-actions"><button class="button" type="submit">保存跟进设置</button><button class="button secondary" type="button" id="schedule-prompt">生成 Codex 任务说明</button></div></form><p>${sc.host_task_id?'已登记宿主任务（状态请由 Codex 核验）':'尚未登记宿主自动化任务'}</p></section>${syncSettings()}${otpSettings()}<section class="panel"><h2>资源与共建入口</h2><p>公司资源可从公共静态目录下载，或离线导入；填写完整的 index.json 地址。贡献入口另行配置。</p><form id="registry-form"><label>公共目录索引地址<input name="registry_url" type="url" value="${esc(s.registry_url||'')}" placeholder="https://你的目录地址/index.json"></label><label>贡献表单地址<input name="contribution_url" type="url" value="${esc(s.contribution_url||'')}" placeholder="GitHub Issue 表单链接"></label><button class="button secondary" type="submit">保存入口</button></form></section><section class="panel"><h2>本机数据库</h2><p>当前账号：${esc(state.account?.name||'默认账号')}。资料、偏好、任务和变更记录共用 SQLite 数据库；附件原件保存在本机。</p><pre>${esc(state.database?.path||state.database_path||state.data_dir||'')}</pre><div class="plain-actions"><button id="export-profile" class="button secondary">导出结构化 JSON</button><button id="migration-help" class="button ghost">备份与换机</button></div><p class="help">JSON 只含当前账号的结构化记录；完整换机备份包含全部账号、设置、任务历史和附件。备份与原件包含个人信息，保存在自己的本地目录。本地账号分区不提供网络身份认证。</p></section></div>`}
function editorFields(kind,data){return kind==='application'&&data.stage==='candidate'?[...fields[kind],['recommendation_bucket','推荐分区','select',recommendationBuckets],['fit_tier','匹配梯队','select',recommendationTiers],['recommendation_scope','梯队适用范围']]:fields[kind]}
function openEdit(kind,id){if(accountSwitching)return;const r=id?rows(kind).find(x=>x.id===id):null;editing={kind,record:r,context:accountContext()};$('#editor-title').textContent=(r?'编辑':'新增')+labels[kind];const data=r?.data||(kind==='application'?{stage:page==='recommendations'?'candidate':'preparing'}:{});editing.fields=editorFields(kind,data);$('#editor-fields').innerHTML=`<div class="form-grid">${editing.fields.map(([k,l,type,options])=>{let v=data[k]??'';if(typeof v==='object')v=JSON.stringify(v);return `<label class="${type==='textarea'?'wide':''}">${esc(l)}${type==='textarea'?`<textarea name="${k}">${esc(v)}</textarea>`:type==='select'?`<select name="${k}"><option value="">尚未确认</option>${Object.entries(options).map(([key,val])=>`<option value="${key}" ${String(v)===key?'selected':''}>${esc(val)}</option>`).join('')}</select>`:`<input name="${k}" type="${type||'text'}" value="${esc(v)}">`}</label>`}).join('')}</div><details class="source-fields"><summary>来源与适用范围</summary><div class="form-grid"><label>确认状态<select name="_status">${Object.entries(statusNames).map(([k,v])=>`<option value="${k}" ${(r?.status||'confirmed')===k?'selected':''}>${v}</option>`).join('')}</select></label><label>适用范围<input name="_scope" value="${esc(r?.scope||'global')}"><small>global 表示通用，也可填写公司或岗位。</small></label></div><p class="help">本次修改会记录为用户编辑，原始来源保留在历史中。</p></details>`;$('#editor').showModal();}
async function saveEdit(event){event.preventDefault();if(!editing)return;const f=new FormData(event.target),{kind,record,context}=editing,data={...(record?.data||{})};for(const [k] of editing.fields){const value=String(f.get(k)||'');if(['recommendation_bucket','fit_tier','recommendation_scope'].includes(k)&&!Object.hasOwn(record?.data||{},k)&&!value.trim())continue;data[k]=value.trim()?(['decision','next_action','blocker'].includes(k)?value:value.trim()):null}const args={kind,data,scope:f.get('_scope')||'global',status:f.get('_status'),source:'user_edit'};if(record)Object.assign(args,{id:record.id,revision:record.revision});const b=event.target.querySelector('[type=submit]');b.disabled=true;try{await api('record.save',args,context);$('#editor').close();notify('修改已保存');await load()}catch(e){if(!staleAccount(e))notify(e.message,true)}finally{b.disabled=false}}
function showDetail(title,html,buttons=''){$('#detail-title').textContent=title;$('#detail-content').innerHTML=html;$('#detail-actions').innerHTML=buttons;$('#detail').showModal()}
async function history(id){
 const context=accountContext(),result=await api('history.list',{record_id:id||undefined,limit:30},context),latest=new Map();
 for(const e of result.items||[])if(!latest.has(e.record_id))latest.set(e.record_id,e.id);
 showDetail('变更记录',result.items?.length?`<div class="timeline">${result.items.map(e=>{
  const current=Object.values(state.records||{}).flat().find(r=>r.id===e.record_id);
  const restore=!current&&latest.get(e.record_id)===e.id&&e.action==='archive'&&e.after?.archived===true;
  const revision=current&&e.after?.revision===current.revision?current.revision:restore?e.after.revision:null;
  return `<div class="list-line"><strong>${esc(labels[e.kind]||e.kind||'资料')} · ${esc(e.action)}</strong><p>${when(e.at)} · ${esc(e.actor)}</p><details><summary>查看修改内容</summary><pre>${esc(JSON.stringify({before:e.before,after:e.after},null,2))}</pre></details>${Number.isInteger(revision)&&revision>0?`<button class="button secondary small" data-undo="${esc(e.id)}" data-revision="${revision}">${restore?'恢复这条资料':'撤销这次修改'}</button>`:''}</div>`
 }).join('')}</div>`:empty('暂无修改历史','后续修改会保留来源和时间。'));
 $$('[data-undo]').forEach(b=>b.onclick=async()=>{try{await api('history.undo',{event_id:b.dataset.undo,revision:Number(b.dataset.revision)},context);$('#detail').close();notify('已撤销，并保留历史');await load()}catch(e){if(!staleAccount(e))notify(e.message,true)}})
}
async function fileData(file){if(file.size>15*1024*1024)throw Error('文件超过 15 MB，请选择更小的材料。');return new Promise((resolve,reject)=>{const r=new FileReader;r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(Error('读取文件失败'));r.readAsDataURL(file)})}
function download(name,data,type='application/json'){const blob=data instanceof Blob?data:new Blob([data],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000)}
function showMigrationHelp(){
 const db=state.database||{},base=db.base_data_dir||state.base_data_dir,root=db.root_dir||state.root_dir;
 const launch=base&&root?['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',root.replace(/\\/g,'/').replace(/\/$/,'')+'/scripts/start.ps1','backup','--data-dir',base,'--output','<本人本地备份 ZIP 路径>']:null;
 showDetail('备份与换机',`<p>先停止本机服务和求职任务。完整备份包含此数据库的全部账号、记录、设置、任务历史、附件与公司资源；不能只复制某个账号目录或结构化 JSON。</p><p>请让 Codex 按独立参数运行以下备份入口，并把最后一个参数换成自己的仓库外本地 ZIP 路径：</p><pre>${esc(launch?JSON.stringify(launch):'当前服务未提供基础目录，请核对数据目录后执行 scripts/start.ps1 backup --data-dir &lt;基础目录&gt; --output &lt;本地 ZIP&gt;')}</pre><p>在新电脑安装同版本或兼容版本，到项目目录用 scripts/start.ps1 restore --archive 指定此 ZIP。默认恢复到新电脑的本地数据目录；指定目录必须全新或为空。启动后核对所有账号和原资料，再用本页的新目录生成任务说明。</p><p>原 ID 与业务历史保留，运行中任务转为待继续。网站重新登录，验证码来源重新配置，自动跟进重新绑定；附件按 ID 解析当前路径。请先停用旧电脑的跟进任务，避免两份数据并行更新。</p><p class="help">ZIP 含私有原件且未加密。妥善保管，不放公开仓库或云同步目录。命令完整选项与迁移范围见项目 references/migration.md。</p>`);
}
async function exportProfile(){const d=await api('profile.export');download('career-profile-'+new Date().toISOString().slice(0,10)+'.json',JSON.stringify(d,null,2));notify('已导出当前账号结构化记录；附件、设置和任务未导出')}
async function saveSettings(settings,context=accountContext()){ensureAccount(context);await api('settings.save',{settings,revision:state.settings._revision},context);notify('设置已保存到当前账号数据库');await load()}
function showArchive(record){
 const context=accountContext();
 showDetail('归档资料',`<p>将“${esc(titleOf(record))}”归档？历史记录仍会保留，可在变更记录中恢复。</p>`,`<button class="button secondary" id="cancel-archive">取消</button><button class="button" id="confirm-archive">确认归档</button>`);
 $('#cancel-archive').onclick=()=>$('#detail').close();
 $('#confirm-archive').onclick=async()=>{
  const button=$('#confirm-archive');button.disabled=true;
  try{await api('record.archive',{id:record.id,revision:record.revision},context);$('#detail').close();notify('已归档，可在变更记录中恢复');await load()}
  catch(error){if(!staleAccount(error)){$('#detail').close();notify(error.message,true)}}finally{button.disabled=false}
 };
}
function bind(){const run=fn=>async e=>{const context=accountContext();try{ensureAccount(context);await fn(e,context);}catch(err){if(context.epoch===accountEpoch&&!staleAccount(err))notify(err.message,true)}};$$('[data-go]').forEach(b=>b.onclick=()=>navigate(b.dataset.go));$$('[data-tab]').forEach(b=>b.onclick=()=>{profileTab=b.dataset.tab;render()});$$('[data-edit]').forEach(b=>b.onclick=()=>openEdit(b.dataset.edit,b.dataset.id));$$('[data-history]').forEach(b=>b.onclick=run(()=>history(b.dataset.history)));$$('[data-archive]').forEach(b=>b.onclick=run(async()=>{const r=Object.values(state.records).flat().find(r=>r.id===b.dataset.archive);showArchive(r)}));if($('#show-history'))$('#show-history').onclick=run(()=>history());if($('#app-query'))$('#app-query').oninput=e=>{query=e.target.value;const start=e.target.selectionStart;render();$('#app-query').focus();$('#app-query').setSelectionRange(start,start)};if($('#app-progress'))$('#app-progress').onchange=e=>{progressFilter=e.target.value;if(stageFilter==='closed'&&progressFilter==='open')stageFilter='';render()};if($('#app-company'))$('#app-company').onchange=e=>{companyFilter=e.target.value;render()};if($('#app-stage'))$('#app-stage').onchange=e=>{stageFilter=e.target.value;if(stageFilter==='closed')progressFilter='closed';else if(progressFilter==='closed'&&stageFilter)progressFilter='all';render()};$$('[data-company-group]').forEach(group=>group.ontoggle=()=>{if(group.open)collapsedCompanies.delete(group.dataset.companyGroup);else collapsedCompanies.add(group.dataset.companyGroup)});if($('#export-profile'))$('#export-profile').onclick=run(exportProfile);if($('#migration-help'))$('#migration-help').onclick=run(showMigrationHelp);
 if($('#recommend-query'))$('#recommend-query').oninput=e=>{recommendQuery=e.target.value;const start=e.target.selectionStart;render();$('#recommend-query').focus();$('#recommend-query').setSelectionRange(start,start)};
 if($('#recommend-type'))$('#recommend-type').onchange=e=>{recommendType=e.target.value;recommendCompany='';recommendCity='';render()};
 if($('#recommend-company'))$('#recommend-company').onchange=e=>{recommendCompany=e.target.value;render()};
 if($('#recommend-city'))$('#recommend-city').onchange=e=>{recommendCity=e.target.value;render()};
 $$('[data-recommendation-company]').forEach(group=>group.ontoggle=()=>{if(group.open)collapsedRecommendationCompanies.delete(group.dataset.recommendationCompany);else collapsedRecommendationCompanies.add(group.dataset.recommendationCompany)});
 $$('[data-prepare-recommendation]').forEach(b=>b.onclick=run(()=>prepareRecommendation(b.dataset.prepareRecommendation,b)));
 if($('#import-profile'))$('#import-profile').onclick=()=>$('#profile-file').click();if($('#profile-file'))$('#profile-file').onchange=run(async(e,context)=>{const file=e.target.files[0];if(!file)return;const profile=JSON.parse(await file.text());await api('profile.import',{profile,source_label:'local_profile_import'},context);notify('档案导入完成');await load()});
 if($('#upload-document'))$('#upload-document').onclick=()=>$('#document-file').click();if($('#document-file'))$('#document-file').onchange=run(async(e,context)=>{const file=e.target.files[0];if(!file)return;notify('正在导入本地材料…');await api('document.import',{filename:file.name,content_base64:await fileData(file)},context);notify('材料已保存，解析内容可供 Codex 整理');await load()});$$('[data-document]').forEach(b=>b.onclick=run(async()=>{const d=await api('document.read',{id:b.dataset.document,limit:16000});showDetail('材料文本',`<p class="help">解析内容需核对，不自动成为已确认事实。</p><pre>${esc(d.text||d.content||'暂无可提取文本')}</pre>${d.next_offset?'<p class="help">此处显示部分内容，Codex 可分页继续读取。</p>':''}`)}));
 $$('[data-install]').forEach(b=>b.onclick=run(async()=>{b.disabled=true;try{const pack=resources.find(r=>r.id===b.dataset.install);if(pack?.installed){const d=await api('resource.read',{id:pack.id,path:'manifest.json'});showDetail('当前资源版本', '<pre>'+esc(d.content)+'</pre>')}else{await api('resource.install',{id:b.dataset.install});notify('资源版本已安装');await load()}}finally{b.disabled=false}}));$$('[data-resource-guide]').forEach(b=>b.onclick=run(async()=>{const d=await api('resource.read',{id:b.dataset.resourceGuide,path:'guide.md'});showDetail('公司使用说明',`<pre>${esc(d.content)}</pre>`)}));$$('[data-export-package]').forEach(b=>b.onclick=run(async()=>{const d=await api('resource.export',{id:b.dataset.exportPackage});const bytes=Uint8Array.from(atob(d.content_base64),c=>c.charCodeAt(0));download(d.filename,new Blob([bytes],{type:'application/zip'}));notify('已导出公共资源包，请在分享前审阅')}));if($('#import-package'))$('#import-package').onclick=()=>$('#package-file').click();if($('#package-file'))$('#package-file').onchange=run(async(e,context)=>{const file=e.target.files[0];if(!file)return;const d=await api('resource.import',{filename:file.name,content_base64:await fileData(file)},context);showPackageReview(d,context)});
 if($('#schedule-form'))$('#schedule-form').onsubmit=run(async e=>{e.preventDefault();await saveSettings({schedule:readScheduleForm()});});
 if($('#preferences-form'))$('#preferences-form').onsubmit=run(savePreferences);
 if($('#task-new'))$('#task-new').onclick=run(()=>openTask());$$('[data-task-create]').forEach(b=>b.onclick=run(()=>openTask()));
 $$('[data-task-edit]').forEach(b=>b.onclick=run(()=>openTask(b.dataset.taskEdit)));$$('[data-task-prompt]').forEach(b=>b.onclick=run(()=>showTaskPrompt(b.dataset.taskPrompt)));$$('[data-task-context]').forEach(b=>b.onclick=run(()=>showTaskContext(b.dataset.taskContext)));
 if($('#task-status'))$('#task-status').onchange=run(async e=>{taskStatus=e.target.value;taskOffset=0;await load();});if($('#task-prev'))$('#task-prev').onclick=run(async()=>{taskOffset=Math.max(0,taskOffset-50);await load();});if($('#task-next'))$('#task-next').onclick=run(async()=>{taskOffset+=50;await load();});
 if($('#schedule-prompt'))$('#schedule-prompt').onclick=run(showSchedulePrompt);
 if($('#registry-form'))$('#registry-form').onsubmit=run(async e=>{e.preventDefault();const f=new FormData(e.target);await saveSettings({registry_url:f.get('registry_url'),contribution_url:f.get('contribution_url')});});
 if($('#registry-refresh'))$('#registry-refresh').onclick=run((e,context)=>refreshPublicRegistry(context));$$('[data-registry-package]').forEach(button=>button.onclick=run((e,context)=>downloadPublicPackage(button.dataset.registryPackage,button.dataset.registryVersion,context)));
 if($('#sync-form')){$('#sync-form').onsubmit=saveSync;for(const id of ['sync-url','sync-username','sync-workspace','sync-password'])$('#'+id).oninput=syncFields;$('#sync-automatic').onchange=syncFields;$('#sync-now').onclick=()=>runSync('sync.run');$('#sync-handoff').onclick=()=>runSync('sync.handoff');$('#sync-takeover').onclick=confirmSyncTakeover;$('#sync-disable').onclick=confirmSyncDisable;}
 if($('#otp-form')){$('#otp-form').onsubmit=saveOtp;$('#otp-provider').onchange=syncOtpFields;$('#otp-url').oninput=syncOtpFields;$('#otp-token').oninput=syncOtpFields;$('#otp-test').onclick=testOtp;}
}
function otpSettings(){return `<section class="panel"><h2>自动验证码</h2><p id="otp-status" role="status">正在读取配置…</p><form id="otp-form"><fieldset id="otp-fields" disabled><label>验证码来源<select id="otp-provider" name="provider"><option value="manual">本人手动输入</option><option value="gotify">SmsForwarder → Gotify</option><option value="kde" disabled>KDE Connect（暂不支持）</option></select></label><div id="gotify-fields" hidden><label>Gotify 服务地址<input id="otp-url" name="url" type="url" placeholder="http://127.0.0.1:端口 或 HTTPS 地址"><small>本机地址可用 HTTP，远程地址须用 HTTPS；填写服务根地址，不要附带 Token 或查询参数。</small></label><label>Gotify 客户端读取 Token<input id="otp-token" name="token" type="password" autocomplete="new-password"><small id="otp-token-status"></small><small>在 Gotify 管理网页的 Clients 中新建一个客户端，将其 Token 填在这里。手机短信转发使用的 Applications Token 用于发送，不能用于这里的读取。保存后输入框会清空，Token 不会回显。</small></label></div><p id="otp-manual-help" class="help">保存为手动输入会关闭自动取码，并清除本机已保存的 Gotify 来源和 Token。</p><p id="otp-error" role="alert" hidden></p><div class="plain-actions"><button class="button" id="otp-save" type="submit">保存来源</button><button class="button secondary" id="otp-test" type="button" disabled>测试已保存来源</button></div><p id="otp-test-status" role="status"></p><p class="help">保存 Gotify 来源后会自动测试连接和消息读取权限；测试不发送短信、不请求网站验证码。</p></fieldset></form></section>`;}
function otpError(error){return ({invalid_url:'地址未保存：请使用 HTTPS，或 http://127.0.0.1:端口；不要附带账号、Token 或查询参数。',token_required:'来源未保存：首次配置或更换服务地址时，需要填写 Gotify 客户端读取 Token。',invalid_token:'来源未保存：Token 格式无效，请检查是否多复制了空格或换行。',provider_unavailable:'此验证码来源暂不支持，请选择 Gotify 或本人手动输入。',secret_storage_failed:'来源未保存：Windows 未能加密 Token，请重试。',platform_unavailable:'来源未保存：Token 加密保存目前需要 Windows。',otp_storage_error:'来源未保存：无法写入本机验证码配置，请检查数据目录访问权限。',otp_config_invalid:'无法读取本机验证码配置，请先排查配置文件。'})[error.code]||error.message;}
function normalizedOtpUrl(value){return String(value||'').trim().replace(/\/+$/,'');}
function syncSettings(){return `<section class="panel"><h2>跨电脑同步 · 全部账号</h2><p>WebDAV 保存此基础数据目录的全部账号业务数据和原始附件；轮流使用，一次只让一台电脑写入。</p><p id="sync-status" role="status">正在读取同步状态…</p><form id="sync-form"><fieldset id="sync-fields" disabled><label>WebDAV 基目录地址<input id="sync-url" name="url" type="url" required placeholder="https://你的服务地址/远端目录/"><small>使用本人私有 HTTPS 目录。云端快照包含个人资料和附件，未另行加密。</small></label><div class="form-grid"><label>用户名<input id="sync-username" name="username" autocomplete="username"></label><label>远端工作区目录<input id="sync-workspace" name="workspace" required pattern="[A-Za-z0-9][A-Za-z0-9_\\-]{0,63}" maxlength="64" value="career"><small>各电脑填写相同地址和目录，使用兼容版本。</small></label></div><label>WebDAV 密码<input id="sync-password" name="password" type="password" autocomplete="new-password"><small id="sync-password-status">密码保存在本机，保存后清空，不会回显。</small></label><label class="check"><input id="sync-automatic" name="automatic" type="checkbox">本地服务运行时自动同步（每 30 秒）</label><p class="help">程序关闭后不会同步。无法连接 WebDAV 时可查看资料，恢复连接并完成同步后再修改。</p><div class="plain-actions"><button id="sync-save" class="button" type="submit">保存并同步</button><button id="sync-now" class="button secondary" type="button" disabled>立即同步</button><button id="sync-handoff" class="button secondary" type="button" disabled>同步并交接</button><button id="sync-takeover" class="button secondary" type="button" disabled>在此电脑接管</button><button id="sync-disable" class="button secondary" type="button" disabled>停用同步</button></div><p id="sync-error" role="alert" hidden></p></fieldset></form><p class="help">换机前点击“同步并交接”，再到新电脑同步。公司资源、待审包和版本锁不进入个人同步；在新电脑从公共目录下载公司资源，或离线导入。网站登录、验证码凭据和本机自动化绑定保留在各自电脑。</p></section>`;}
function syncPasswordMatches(){const c=syncState?.config||{};return c.password_set&&normalizedOtpUrl($('#sync-url').value)===normalizedOtpUrl(c.url)&&$('#sync-username').value===c.username&&$('#sync-workspace').value===c.workspace;}
function publicResourceSection(){
 const configured=!!state.settings?.registry_url,items=publicRegistry?.items||[];
 return `<section class="panel spaced"><div class="section-header"><div><h2>公共下载目录</h2><p>${configured?'点击刷新后读取已配置的公开索引；下载的包先进入本机审阅。':'在设置页填写公共目录的 index.json 地址，或继续离线导入 ZIP。'}</p></div><button id="registry-refresh" class="button secondary" ${configured&&!registryBusy?'':'disabled'}>刷新公共目录</button></div>${publicRegistry?`<p class="help">${esc(publicRegistry.url||'尚未配置')} · ${items.length} 个公开版本</p>`:''}${items.length?`<div class="card-grid">${items.map(pack=>`<article class="resource-card"><h3>${esc(pack.name)} ${badge(pack.version)}</h3><p>${esc(pack.description)}</p><div class="capabilities">${capabilityBadges(pack.capabilities)}</div><p class="help">${esc(pack.origin)}<br>${(pack.download.bytes/1024).toFixed(1)} KB<br>下载后仍需核对代码和验证范围。</p><div class="form-actions"><button class="button secondary small" data-registry-package="${esc(pack.id)}" data-registry-version="${esc(pack.version)}" ${registryBusy?'disabled':''}>下载并审阅</button></div></article>`).join('')}</div>`:''}<p class="help">公共目录只分发公司方法；求职数据库和附件使用个人 WebDAV。公司包不会自动启用或执行。</p></section>`;
}
function setRegistryBusy(value){registryBusy=value;if($('#registry-refresh'))$('#registry-refresh').disabled=value||!state.settings?.registry_url;$$('[data-registry-package]').forEach(button=>button.disabled=value);}
async function refreshPublicRegistry(context){
 if(registryBusy||!state.settings?.registry_url)return;setRegistryBusy(true);
 try{const result=await api('resource.refresh',{url:state.settings.registry_url},context);publicRegistry=result;await render();notify('已读取公共目录，下载后需本人审阅。');}
 finally{if(context.epoch===accountEpoch)setRegistryBusy(false);}
}
function showPackageReview(result,context){
 showDetail('审阅导入资源',`<p>资源包将保存在本机。检查来源与全部代码后再启用；下载和校验不会执行公司脚本。</p><pre>${esc(JSON.stringify(result,null,2))}</pre>`,`<button class="button" id="approve-package">确认已审阅并启用</button>`);
 $('#approve-package').onclick=async()=>{try{ensureAccount(context);await api('resource.approve',{candidate_id:result.candidate_id},context);$('#detail').close();notify('资源已启用');await load();}catch(error){if(context.epoch===accountEpoch&&!staleAccount(error))notify(error.message,true);}};
}
async function downloadPublicPackage(id,version,context){
 if(registryBusy)return;const pack=publicRegistry?.items.find(item=>item.id===id&&item.version===version);if(!pack)return;setRegistryBusy(true);
 try{const result=await api('resource.download',{id,version,sha256:pack.download.sha256,url:publicRegistry.url},context);showPackageReview(result,context);}
 finally{if(context.epoch===accountEpoch)setRegistryBusy(false);}
}
function syncFields(){
 const saved=syncPasswordMatches();$('#sync-password').required=!saved;
 $('#sync-password').placeholder=saved?'已保存，留空保留密码':'首次配置或更换位置时填写';
 $('#sync-password-status').textContent=saved?'密码已加密保存在本机；留空保留，不会回显。':'密码保存在本机，保存后清空，不会回显。';
 const unchanged=syncState?.configured&&saved&&!$('#sync-password').value&&$('#sync-automatic').checked===!!syncState.config?.automatic;
 $('#sync-now').disabled=syncBusy||!unchanged;$('#sync-handoff').disabled=syncBusy||!unchanged||syncState.mode!=='writer';
 $('#sync-takeover').disabled=syncBusy||!unchanged||!['reader','uninitialized'].includes(syncState.mode);
 $('#sync-disable').disabled=syncBusy||!syncState?.configured;
}
function applySyncStatus(status,fill=false){
 syncState=status;const c=status.config||{};
 if(fill){$('#sync-url').value=c.url||'';$('#sync-username').value=c.username||'';$('#sync-workspace').value=c.workspace||'career';$('#sync-automatic').checked=!!c.automatic;$('#sync-password').value='';}
 const mode={local:'尚未配置，资料保存在本机。',writer:'此电脑可写入。',reader:'此电脑只读；切换使用前请交接或接管。',uninitialized:'尚未完成首次同步，暂时只读。',conflict:'两份资料存在冲突，已停止覆盖；请保留双方数据后处理。'}[status.mode]||'同步状态待核对。';
 $('#sync-status').textContent=mode+(status.configured?(status.dirty?' 有未同步改动。':'')+(status.last_synced_at?' 上次同步：'+when(status.last_synced_at)+'。':' 尚未成功同步。')+(status.remote_owner&&status.remote_owner!==status.device_id?' 当前写入设备：'+status.remote_owner+'。':'')+(status.last_error?' '+status.last_error:''):'');
 $('#sync-fields').disabled=syncBusy;syncFields();
}
function pollSyncStatus(version){setTimeout(async()=>{
 if(version!==renderVersion||page!=='settings')return;
 if(!syncBusy){try{const status=await api('sync.status');if(version===renderVersion)applySyncStatus(status);}catch(error){if(version===renderVersion)$('#sync-status').textContent='同步状态读取失败：'+error.message;}}
 if(version===renderVersion)pollSyncStatus(version);
 },5000);}
function setSyncBusy(value){syncBusy=value;$('#sync-fields').disabled=value||!syncState;syncFields();}
async function refreshAfterSync(result){if(!result.changed)return true;++accountEpoch;clearAccountView();return await load();}
async function saveSync(event){
 event.preventDefault();const form=event.target;if(!syncState||syncBusy||!form.reportValidity())return;
 const version=renderVersion,context=accountContext(),f=new FormData(form),args={url:String(f.get('url')||'').trim(),username:String(f.get('username')||''),workspace:String(f.get('workspace')||'career').trim(),password:String(f.get('password')||''),automatic:f.get('automatic')==='on'};
 let saved=false;setSyncBusy(true);$('#sync-error').hidden=true;
 try{
  const status=await api('sync.configure',args,context);saved=true;if(version!==renderVersion)return;applySyncStatus(status,true);
  const result=await api('sync.run',{},context);if(version!==renderVersion)return;applySyncStatus(result);if(await refreshAfterSync(result))notify('同步配置已保存，并完成同步。');else notify('同步已完成，但网页资料尚未刷新，请刷新核对。',true);
 }catch(error){if(version===renderVersion&&!staleAccount(error)){$('#sync-error').textContent=(saved?'配置已保存，但同步未完成：':'同步配置未保存：')+error.message;$('#sync-error').hidden=false;notify($('#sync-error').textContent,true);}}
 finally{if(version===renderVersion)setSyncBusy(false);}
}
async function runSync(operation){
 if(!syncState||syncBusy)return;const version=renderVersion,context=accountContext();setSyncBusy(true);$('#sync-error').hidden=true;
 try{const result=await api(operation,{},context);if(version!==renderVersion)return;applySyncStatus(result);if(await refreshAfterSync(result))notify(operation==='sync.disable'?'已停用同步，资料保存在本机。':operation==='sync.handoff'?'已同步并交接，此电脑转为只读。':operation==='sync.takeover'?'已在此电脑接管，请保持另一台服务关闭。':'同步完成。');else notify('同步已完成，但网页资料尚未刷新，请刷新核对。',true);}
 catch(error){if(version===renderVersion&&!staleAccount(error)){$('#sync-error').textContent=error.message;$('#sync-error').hidden=false;notify(error.message,true);}}
 finally{if(version===renderVersion)setSyncBusy(false);}
}
function confirmSyncTakeover(){
 const context=accountContext();showDetail('在此电脑接管',`<p>先关闭另一台电脑的本地服务和求职任务，再接管写入权。</p><p>接管会读取远端最新资料，不合并两台电脑各自的离线修改。另一台服务保持关闭，避免继续写入。</p>`,`<button class="button" id="confirm-sync-takeover">已关闭另一台服务，接管</button>`);
 $('#confirm-sync-takeover').onclick=async()=>{try{ensureAccount(context);$('#detail').close();await runSync('sync.takeover');}catch(error){if(!staleAccount(error))notify(error.message,true);}};
}
function confirmSyncDisable(){
 const context=accountContext();showDetail('停用同步',`<p>停用后保留本机资料，本机后续修改不再上传。换机使用请先同步并交接；重新启用时可能出现资料冲突。</p>`,`<button class="button" id="confirm-sync-disable">确认停用同步</button>`);
 $('#confirm-sync-disable').onclick=async()=>{try{ensureAccount(context);$('#detail').close();await runSync('sync.disable');}catch(error){if(!staleAccount(error))notify(error.message,true);}};
}
function syncOtpFields(){
 const gotify=$('#otp-provider').value==='gotify',sameToken=gotify&&otpState?.token_configured&&otpState.provider==='gotify'&&normalizedOtpUrl($('#otp-url').value)===normalizedOtpUrl(otpState.url);
 $('#gotify-fields').hidden=!gotify;$('#otp-manual-help').hidden=gotify;
 $('#otp-url').disabled=!gotify;$('#otp-url').required=gotify;
 $('#otp-token').disabled=!gotify;$('#otp-token').required=gotify&&!sameToken;
 $('#otp-token').placeholder=sameToken?'已保存，留空保留现有 Token':'首次配置或更换地址时必填';
 $('#otp-token-status').textContent=sameToken?'此服务的 Token 已加密保存在本机；留空可保留。':'此服务尚无已保存 Token，请填写客户端读取 Token。';
 $('#otp-save').textContent=gotify?'保存并测试来源':'保存来源';
 $('#otp-test').disabled=otpBusy||!sameToken||!otpState?.enabled||!!$('#otp-token').value;
 $('#otp-test').title=gotify&&!sameToken||$('#otp-token').value?'请先保存当前修改，再测试新配置。':'仅测试本机已保存的来源。';
}
function applyOtpStatus(status){
 otpState=status;$('#otp-provider').value=status.provider||'manual';$('#otp-url').value=status.url||'';$('#otp-fields').disabled=otpBusy;
 $('#otp-status').textContent=status.provider==='gotify'?(status.enabled?'已保存 Gotify 来源':'已保存 Gotify 来源，自动取码已停用')+(status.token_configured?' · Token 已加密保存。':' · 尚无 Token。'):'已保存状态：本人手动输入，未启用自动验证码。';
 renderOtpTest(status.connection_test);syncOtpFields();
}
function renderOtpTest(result){
 const target=$('#otp-test-status');
 if(!result){target.textContent=otpState?.provider==='gotify'?'尚未测试连接与读取权限。':'本人手动输入，无需连接测试。';target.dataset.result='untested';return;}
 const stamp=result.checked_at?`（${when(result.checked_at)}）`:'';
 target.dataset.result=result.status;
 target.textContent=result.status==='passed'?`连接测试通过${stamp}：Gotify 可连接，Token 可以读取消息。手机短信转发和招聘网站取码尚未验证。`:`来源已保存，但连接测试失败${stamp}：${result.message||'请检查服务地址和客户端读取 Token。'}`;
}
function setOtpBusy(value){otpBusy=value;$('#otp-fields').disabled=value||!otpState;syncOtpFields();}
function otpTestFailed(error){
 $('#otp-test-status').dataset.result='failed';
 $('#otp-test-status').textContent='来源已保存，但连接测试未完成：'+(error.message||'请稍后重试。');
 notify('来源已保存，但连接测试未完成，请查看测试结果。',true);
}
async function runOtpTest(version){
 $('#otp-test-status').dataset.result='testing';$('#otp-test-status').textContent='来源已保存，正在测试 Gotify 连接与消息读取权限…';
 const result=await api('otp.test');if(version!==renderVersion)return;
 if(result.code==='otp_source_changed'){
  const current=await api('otp.status');if(version!==renderVersion)return;
  applyOtpStatus(current);$('#otp-error').textContent=result.message;$('#otp-error').hidden=false;
  notify('配置已改变，旧测试结果已丢弃，请核对当前来源后再测试。',true);return;
 }
 otpState.connection_test=result;renderOtpTest(result);
 notify(result.status==='passed'?'来源已保存，连接与读取权限测试通过':'来源已保存，但连接测试失败，请查看测试结果。',result.status!=='passed');
}
async function testOtp(){
 if(!otpState||otpBusy||$('#otp-test').disabled)return;
 const version=renderVersion;setOtpBusy(true);$('#otp-error').hidden=true;
 try{await runOtpTest(version);}catch(error){if(version===renderVersion)otpTestFailed(error);}
 finally{if(version===renderVersion)setOtpBusy(false);}
}
async function saveOtp(event){
 event.preventDefault();const form=event.target;if(!otpState||otpBusy||!form.reportValidity())return;
 const version=renderVersion,fields=$('#otp-fields'),errorBox=$('#otp-error'),f=new FormData(form),provider=f.get('provider');
 const args={provider,enabled:provider==='gotify'};
 if(provider==='gotify'){args.url=String(f.get('url')||'').trim();args.token=f.get('token')||'';}
 setOtpBusy(true);errorBox.hidden=true;
 try{
  await api('otp.configure',args);
  if(version!==renderVersion)return;
  $('#otp-token').value='';
  let status;
  try{status=await api('otp.status');if(version!==renderVersion)return;applyOtpStatus(status);}
  catch(error){if(version===renderVersion){otpState=null;$('#otp-status').textContent='保存请求已成功，但读取确认失败。请刷新核对。';errorBox.textContent=otpError(error);errorBox.hidden=false;}return;}
  if(provider==='gotify'&&status.provider==='gotify'&&status.enabled){
   try{await runOtpTest(version);}catch(error){if(version===renderVersion)otpTestFailed(error);}
  }else{notify('验证码来源已保存并读回');}
 }catch(error){if(version===renderVersion){errorBox.textContent=otpError(error);errorBox.hidden=false;notify(otpError(error),true);}}
 finally{if(version===renderVersion)setOtpBusy(false);}
}

function readScheduleForm(){const f=new FormData($('#schedule-form'));return {time:f.get('time'),timezone:f.get('timezone'),allow_relogin:f.get('allow_relogin')==='on'};}
async function showSchedulePrompt(){
 if(!$('#schedule-form').reportValidity())return;
 const button=$('#schedule-prompt'),context=accountContext();button.disabled=true;
 try{
  state.settings=await api('settings.save',{settings:{schedule:readScheduleForm()},revision:state.settings._revision},context);
  const result=await api('schedule.prepare',{},context);
  showDetail('交给 Codex 的任务说明',`<p>当前跟进设置已保存。将以下文字发送给 Codex；说明包含本机资料位置，不要求预先加载指定技能。</p><pre>${esc(result.prompt)}</pre>`,`<button class="button" id="copy-prompt">复制任务说明</button>`);
  $('#copy-prompt').onclick=async()=>{try{ensureAccount(context);await navigator.clipboard.writeText(result.prompt);ensureAccount(context);notify('已复制');}catch(error){if(context.epoch===accountEpoch&&!staleAccount(error))notify('复制失败，请手动选择任务说明复制。',true);}};
 }finally{button.disabled=false;}
}

async function navigate(p){page=titles[p]?p:'overview';location.hash=page;$('#notice').hidden=true;return await load()}
$$('nav button').forEach(b=>b.onclick=()=>navigate(b.dataset.page));$$('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());$('#edit-form').onsubmit=saveEdit;$('#refresh').onclick=()=>load();
if($('#account-select'))$('#account-select').onchange=e=>switchAccount(e.target.value);if($('#account-new'))$('#account-new').onclick=()=>{if(!accountSwitching)$('#account-editor').showModal();};
if($('#account-form'))$('#account-form').onsubmit=createAccount;if($('#task-form'))$('#task-form').onsubmit=async e=>{try{await saveTask(e);}catch(error){if(!staleAccount(error))notify(error.message,true);}};
window.addEventListener('hashchange',()=>{const next=location.hash.slice(1);if(next!==page)navigate(next)});page=titles[location.hash.slice(1)]?location.hash.slice(1):'overview';load();
