/* Independent autofill UI; all operations use the same account-bound service as MCP. */
globalThis.CareerForms = (() => {
 let host, packs=[], recent=[], descriptor=null, prepared=null, runtime=null, selected='', busy=false, poll=null, generation=0, draftSelection={}, draftFields={};
 const $=id=>document.getElementById(id), esc=value=>host.esc(value), current=()=>host.accountContext();
 function configure(value){host=value;}
 function reset(){clearTimeout(poll);poll=null;generation++;packs=[];recent=[];descriptor=null;prepared=null;runtime=null;selected='';busy=false;draftSelection={};draftFields={};}
 async function load(context){
  const version=generation;
  const result=await host.api('resource.list',{},context);host.ensureAccount(context);
  if(version!==generation)return;
  packs=(result.items||[]).filter(pack=>pack.files?.includes('form.json'));
  const tasks=await host.api('task.list',{limit:200},context);host.ensureAccount(context);
  if(version!==generation)return;
  recent=(tasks.items||[]).filter(task=>task.data?.autofill?.contract_version===1);
  try{runtime=await host.api('browser.status',{},context);}catch(error){runtime={available:false,message:error.message};}
 }
 function render(){
  const pack=packs.find(p=>p.id===selected), form=descriptor?.form||descriptor?.schema;
  return `<section class="panel"><div class="section-header"><div><h2>自动填写公司简历</h2><p>从当前账号资料准备填写内容，使用本机专用招聘浏览器。已实现的字段操作无需启动 Agent。</p></div></div><div class="form-grid"><label>公司资源<select id="fill-company"><option value="">选择公司</option>${packs.map(p=>`<option value="${esc(p.id)}" ${p.id===selected?'selected':''}>${esc(p.name)} · ${esc(p.version)}</option>`).join('')}</select></label><div class="form-actions"><button class="button secondary" id="fill-describe" ${selected&&!busy?'':'disabled'}>${pack?.installed?'读取填表范围':'启用内置资源并读取范围'}</button></div></div><p class="help" id="fill-runtime">${esc(runtime?.message||'招聘浏览器在本机运行，登录状态按本地账号保留。')}</p><div class="plain-actions"><button class="button ghost small" id="fill-setup" ${busy?'disabled':''}>安装浏览器依赖</button><button class="button ghost small" id="fill-close" ${busy?'disabled':''}>关闭招聘浏览器</button></div></section>${form?formPanel(form):selected&&descriptor?`<section class="panel spaced"><p>${esc(descriptor.message||'此公司资源尚未提供统一自动填表入口。')}</p></section>`:''}<section class="panel spaced"><h2>扩展公司资源</h2><p>Agent 可通过 <code>career_resources</code> 获取模板和校验结果，按 <code>$company-resource</code> 的指引观察招聘页面，制作规范的公司资源包。新包在“公司资源”中导入、审阅并启用。</p><p class="help">包中的声明和代码只包含公共方法。个人填写内容、附件及网站登录信息保留在本机。</p></section>`;
 }
 function formPanel(form){
  const schema=form.input_schema||{}, values=prepared?.inputs||prepared?.input||{}, issues=prepared?.issues||[...(prepared?.missing||[]).map(path=>({path,code:'missing'})),...(prepared?.invalid||[])];
  const selectedPack=packs.find(p=>p.id===selected);
  const fields=Object.entries(schema.properties||{}).map(([key,s])=>field(key,s,values[key],(schema.required||[]).includes(key))).join('');
  const saved=recent.filter(task=>task.data.autofill.company_id===selected),savedControl=saved.length?`<div class="plain-actions spaced"><label>继续已保存的准备<select id="fill-saved"><option value="">选择最近的准备任务</option>${saved.map(task=>`<option value="${esc(task.id)}" ${task.id===prepared?.id?'selected':''}>${esc(task.updated_at)} · ${esc(task.data.autofill.phase)}</option>`).join('')}</select></label><button class="button secondary" id="fill-resume" ${busy?'disabled':''}>读取保存内容</button></div>`:'';
  return `<section class="panel spaced"><h2>${esc(form.title||'简历填写')}</h2><p>${esc(selectedPack?.name||selected)} · 固定资源版本 ${esc(prepared?.version||descriptor?.version||selectedPack?.version||'')}</p><p class="help">支持：${esc((form.scope?.supported||[]).join('、')||'以此资源声明为准')}。${form.scope?.unsupported?.length?'待补：'+esc(form.scope.unsupported.join('、'))+'。':''}</p>${savedControl}<label>招聘页面地址<input id="fill-url" type="url" value="${esc(form.start_url||form.origin+(form.routes?.[0]||'/').replace(/\*$/,''))}"></label><div class="plain-actions"><button class="button secondary" id="fill-open" ${busy?'disabled':''}>打开招聘浏览器</button><button class="button ghost" id="fill-inspect" ${busy?'disabled':''}>检查当前页面</button></div><p class="help" id="fill-inspection">${esc(descriptor.inspection?'当前页面：'+JSON.stringify(descriptor.inspection):'在打开的招聘浏览器中自行登录并进入简历页面，随后准备填写内容。')}</p><div class="form-grid spaced">${selectionMarkup(form)}</div><div class="plain-actions spaced"><button class="button secondary" id="fill-prepare" ${busy?'disabled':''}>从档案准备填写内容</button></div><form id="fill-input-form"><div class="form-grid spaced">${fields}</div><p class="help">空值不会猜填。输入的补充内容作为此公司的私有准备资料保存；它不会自动改写通用档案。</p><label class="check"><input id="fill-reviewed" type="checkbox">我已核对这些资料，允许填写到此公司简历</label>${needsSharing(schema)?'<label class="check"><input id="fill-sensitive" type="checkbox">允许向此公司共享本次选择的敏感资料或附件</label>':''}<div class="plain-actions spaced"><button class="button secondary" id="fill-save-inputs" type="submit" ${busy?'disabled':''}>保存补充内容并检查缺项</button><button class="button" id="fill-run" type="button" ${prepared?.ready&&!busy?'':'disabled'}>一键填写</button><button class="button ghost" id="fill-cancel" type="button" ${prepared&&['running','queued'].includes(prepared.phase)?'':'disabled'}>停止填写</button></div></form>${issues.length?`<div class="record-load-warning spaced" role="alert">${issues.map(item=>`<p>${esc(item.path||item.field||'资料')}：${esc(issueName(item.code||item.type))}</p>`).join('')}</div>`:''}<div id="fill-result" class="spaced" role="status">${resultMarkup()}</div></section>`;
 }
 function issueName(code){return ({missing:'缺少资料',unconfirmed:'资料尚未确认',ambiguous:'有多条资料，请选择',invalid_type:'类型不符合要求',invalid:'不符合输入要求',source_ambiguous:'有多条资料，请选择'})[code]||code;}
 function inputType(schema){return Array.isArray(schema.type)?schema.type.find(type=>type!=='null'):schema.type;}
 function needsSharing(schema){return !!(schema['x-sensitive']||schema.format==='document-id'||Object.values(schema.properties||{}).some(needsSharing)||(schema.items&&needsSharing(schema.items)));}
 function sourceKinds(form){
  const kinds={};function visit(schema){const source=schema['x-source']||'',kind=source.split(/[.:]/)[0];if(['basic','education','experience','family','preference'].includes(kind))kinds[kind]=kinds[kind]||inputType(schema)==='array';Object.values(schema.properties||{}).forEach(visit);if(schema.items)visit(schema.items);}visit(form.input_schema);return kinds;
 }
 function selectionMarkup(form){
  return Object.entries(sourceKinds(form)).map(([kind,multiple])=>{
   const items=(host.getState().records?.[kind]||[]).filter(r=>r.status==='confirmed'&&['global',selected].includes(r.scope)),chosen=draftSelection[kind]||prepared?.selection?.[kind]||[];
   if(!multiple&&items.length<=1)return '';
   const title=({basic:'基础资料版本',education:'选用教育经历',experience:'选用项目与经历',family:'选用家庭资料',preference:'选用偏好'})[kind];
   return `<label>${title}<select data-fill-record="${kind}" ${kind==='basic'?'id="fill-basic"':''} ${multiple?'multiple size="4"':''}>${multiple?'':'<option value="">选择已确认资料</option>'}${items.map(r=>`<option value="${esc(r.id)}" ${chosen.includes(r.id)?'selected':''}>${esc(r.data.name||r.data.school||r.data.title||r.data.value||r.id)} · v${r.revision}</option>`).join('')}</select>${multiple?'<small>可按 Ctrl 多选；不选的经历不会自动填入。</small>':''}</label>`;
  }).join('');
 }
 function field(key,s,value,required){
  const title=esc(s.title||key)+(required?' *':''), attr=`data-fill-field="${esc(key)}"`,kind=inputType(s),draft=draftFields[key];
  if(kind==='array'||kind==='object')return `<label class="full">${title}<textarea ${attr} rows="5" data-json="true" placeholder="${kind==='array'?'[]':'{}'}">${esc(draft??(value===undefined||value===null?'':JSON.stringify(value,null,2)))}</textarea><small>${esc(s.description||'结构化条目，保留每段经历身份和日期。')}</small></label>`;
  if(draft!==undefined){value=kind==='boolean'?(draft===''?null:draft==='true'):s.enum?(draft===''?null:JSON.parse(draft)):draft;}
  if(s.format==='document-id'||s['x-document'])return `<label>${title}<select ${attr}><option value="">不选择附件</option>${(host.getState().documents||[]).map(d=>`<option value="${esc(d.id)}" ${d.id===value?'selected':''}>${esc(d.filename||d.original_name||d.id)}</option>`).join('')}</select></label>`;
  if(kind==='boolean')return `<label>${title}<select ${attr} data-boolean="true"><option value="">未知</option><option value="true" ${value===true?'selected':''}>是</option><option value="false" ${value===false?'selected':''}>否</option></select></label>`;
  if(s.enum)return `<label>${title}<select ${attr} data-scalar="true"><option value="">未填写</option>${s.enum.filter(v=>v!==null).map(v=>`<option value="${esc(JSON.stringify(v))}" ${v===value?'selected':''}>${esc(v)}</option>`).join('')}</select></label>`;
  const type=s.format==='date'?'date':s.format==='date-month'?'month':['integer','number'].includes(kind)?'number':'text';
  return `<label>${title}<input ${attr} type="${type}" ${kind==='number'?'step="any"':''} value="${esc(value??'')}" ${s.maxLength?'maxlength="'+s.maxLength+'"':''}><small>${esc(s.description||'')}</small></label>`;
 }
 function resultMarkup(){
  if(!prepared)return '<p class="muted">准备内容与执行结果会保存在当前账号的任务中。</p>';
  const phase=({prepared:'已准备',ready:'可填写',running:'正在填写',queued:'正在启动',completed:'本次执行已完成',failed:'执行未完成',cancelled:'已停止',blocked:'存在待处理项'})[prepared.phase]||prepared.phase||'已准备';
  const result=prepared.result||{}, progress=prepared.progress;
  return `<p><strong>${esc(phase)}</strong></p>${progress?`<pre>${esc(JSON.stringify(progress,null,2))}</pre>`:''}${Object.keys(result).length?`<pre>${esc(JSON.stringify(result,null,2))}</pre>`:''}<p class="help">字段读回成功只说明页面已填入。网站保存、完整核验和最终投递按此公司支持范围分别处理。</p>`;
 }
 function repaint(){if(host.isActive()){$('content').innerHTML=render();bind();}}
 async function action(fn){
  if(busy)return;draftSelection=selection();draftFields=Object.fromEntries([...document.querySelectorAll('[data-fill-field]')].map(el=>[el.dataset.fillField,el.value]));const context=current(),version=generation;let succeeded=false;busy=true;disable(true);
  try{await fn(context);host.ensureAccount(context);succeeded=true;}
  catch(error){if(version===generation)host.notify(error.message,true);}
  finally{if(version===generation){busy=false;if(succeeded){if($('notice'))$('notice').hidden=true;repaint();}else disable(false);}}
 }
 function disable(value){document.querySelectorAll('#content button, #content input, #content select, #content textarea').forEach(b=>{if(value){b.dataset.previousDisabled=b.disabled?'true':'false';b.disabled=true;}else if(b.dataset.previousDisabled!==undefined){b.disabled=b.dataset.previousDisabled==='true';delete b.dataset.previousDisabled;}});}
 function selection(){const value={...draftSelection};document.querySelectorAll('[data-fill-record]').forEach(el=>{value[el.dataset.fillRecord]=[...el.selectedOptions].map(option=>option.value).filter(Boolean);});return value;}
 function setPrepared(value){prepared=value;draftSelection=value.selection||{};draftFields={};}
 function readInputs(){
  const input={};document.querySelectorAll('[data-fill-field]').forEach(el=>{
   const value=el.value;
   input[el.dataset.fillField]=value===''?null:el.dataset.json||el.dataset.scalar?JSON.parse(value):el.dataset.boolean?value==='true':el.type==='number'?Number(value):value;
  });return input;
 }
 function canonical(value){
  if(value===null||value===undefined||value==='')return null;
  if(Array.isArray(value))return value.map(canonical);
  if(value&&typeof value==='object')return Object.fromEntries(Object.keys(value).sort().filter(key=>canonical(value[key])!==null).map(key=>[key,canonical(value[key])]));
  return value;
 }
 function sameInput(a,b){return JSON.stringify(canonical(a))===JSON.stringify(canonical(b));}
 function bind(){
  if(!host.isActive())return;
  if($('fill-company'))$('fill-company').onchange=()=>{clearTimeout(poll);selected=$('fill-company').value;descriptor=null;prepared=null;draftSelection={};draftFields={};repaint();};
  if($('fill-describe'))$('fill-describe').onclick=()=>action(async context=>{
   const p=packs.find(x=>x.id===selected);if(!p.installed)await host.api('resource.install',{id:selected},context);
   descriptor=await host.api('fill.describe',{company_id:selected},context);
  });
  if($('fill-setup'))$('fill-setup').onclick=()=>action(async context=>{await host.api('browser.setup',{},context);runtime=await host.api('browser.status',{},context);host.notify('浏览器依赖已安装，可以打开招聘浏览器。');});
  if($('fill-close'))$('fill-close').onclick=()=>action(async context=>{await host.api('browser.close',{},context);host.notify('招聘浏览器已关闭。');});
  if($('fill-open'))$('fill-open').onclick=()=>action(async context=>{const r=await host.api('browser.open',{company_id:selected,url:$('fill-url').value},context);runtime=r;host.notify('招聘浏览器已打开，请自行登录并进入简历页面。');});
  if($('fill-inspect'))$('fill-inspect').onclick=()=>action(async context=>{const r=await host.api('browser.inspect',{},context);host.notify(r.ok===false?'当前页面尚未满足资源条件。':'已读取当前招聘页面。');descriptor.inspection=r;});
  if($('fill-prepare'))$('fill-prepare').onclick=()=>action(async context=>{setPrepared(await host.api('fill.prepare',{company_id:selected,selection:selection()},context));});
  if($('fill-resume'))$('fill-resume').onclick=()=>action(async context=>{const id=$('fill-saved').value;if(!id)throw Error('请选择已保存的准备任务。');setPrepared(await host.api('fill.status',{id},context));if(prepared.phase==='running')schedulePoll(context);});
  if($('fill-input-form'))$('fill-input-form').onsubmit=e=>{e.preventDefault();action(async context=>{
   const edited=readInputs(),before=prepared?.input||{},inputs=Object.fromEntries(Object.entries(edited).filter(([key,value])=>!sameInput(value,before[key]))),args={company_id:selected,inputs,selection:selection()};if(prepared){args.task_id=prepared.id;args.revision=prepared.revision;}setPrepared(await host.api('fill.prepare',args,context));
  });};
  if($('fill-run'))$('fill-run').onclick=()=>action(async context=>{
   if(!$('fill-reviewed').checked)throw Error('请先核对资料并勾选允许填写。');
   const chosen=Object.fromEntries(Object.entries(selection()).filter(([,ids])=>ids.length)),previous=Object.fromEntries(Object.entries(prepared.selection||{}).filter(([,ids])=>ids.length));
   if(!sameInput(chosen,previous))throw Error('资料选择已修改，请重新从档案准备填写内容。');
   if(!sameInput(readInputs(),prepared.inputs||prepared.input||{}))throw Error('填写内容已修改，请先保存补充内容并检查缺项。');
   const form=descriptor.form||descriptor.schema,share=$('fill-sensitive')?.checked===true;
   prepared=await host.api('fill.run',{id:prepared.id,revision:prepared.revision,authorization:{prepareProfile:true,origins:[form.origin],shareSensitiveWith:{[form.consent_key||form.origin]:share},shareResumeWith:{[form.consent_key||form.origin]:share}}},context);schedulePoll(context);
  });
  if($('fill-cancel'))$('fill-cancel').onclick=()=>action(async context=>{prepared=await host.api('fill.cancel',{id:prepared.id},context);});
 }
 function schedulePoll(context){
  clearTimeout(poll);const version=generation;
  poll=setTimeout(async()=>{
   if(version!==generation||!host.isActive()||!prepared)return;
   try{const next=await host.api('fill.status',{id:prepared.id},context);if(version!==generation)return;prepared=next;const el=$('fill-result');if(el)el.innerHTML=resultMarkup();if(['running','queued'].includes(next.phase))schedulePoll(context);else repaint();}
   catch(error){if(version===generation)host.notify(error.message,true);}
  },700);
 }
 return {configure,reset,load,render,bind};
})();
