import test from "node:test";
import assert from "node:assert/strict";
import { fillResumeOnce } from "../scripts/profile-fill.mjs";
import { profilePlan } from "../companies/antgroup/adapter.mjs";
import { profilePlan as jdPlan } from "../companies/jd/adapter.mjs";
import { profilePlan as iflytekPlan } from "../companies/iflytek/adapter.mjs";
import { profilePlan as ztePlan } from "../companies/zte/adapter.mjs";
import { profilePlan as stepPlan } from "../companies/stepfun/adapter.mjs";

function fake(initial = {}, route = "https://talent.antgroup.com/personal/campus-resume") {
  const values = { ...initial }, calls = [];
  const tab = {
    url: async () => route,
    playwright: {
      domSnapshot: async () => "个人信息 教育经历 保存简历",
      locator: selector => ({
        count: async () => selector.startsWith("#") ? 1 : 0,
        isVisible: async () => true,
        isEnabled: async () => true,
        evaluate: async fn => fn({ value: values[selector] ?? "", closest: () => ({ querySelector: () => ({ textContent: "博士" }) }) }),
        fill: async value => { values[selector] = value; calls.push(selector); }
      })
    }
  };
  return { tab, values, calls };
}
const auth = { prepareProfile: true, origins: ["https://talent.antgroup.com"] };

test("fills only supplied public facts and leaves save to review", async () => {
  const { tab, values, calls } = fake({"#editForm_educations_0_school":"Example University"});
  const result = await fillResumeOnce({ tab, plan: profilePlan, facts: { name: "Example Candidate", doctoralMajor: "Computer Science",educationExpectedSchool:"Example University" }, authorization: auth });
  assert.equal(result.ok, true);
  assert.deepEqual(result.completed, ["name", "doctoralMajor"]);
  assert.equal(result.saved, false);
  assert.equal(result.submitted, false);
  assert.equal(values["#basic_name"], "Example Candidate");
  assert.equal(calls.length, 2);
});

test("blocks changed existing value before overwriting it", async () => {
  const { tab, calls } = fake({ "#basic_name": "User Edit" });
  const result = await fillResumeOnce({ tab, plan: profilePlan, facts: { name: "Example Candidate" }, authorization: auth });
  assert.equal(result.blocked.code, "existing_value_conflict");
  assert.deepEqual(calls, []);
});

test("rejects wrong route and missing authorization", async () => {
  const { tab } = fake({}, "https://elsewhere.example/resume");
  assert.equal((await fillResumeOnce({ tab, plan: profilePlan, facts: { name: "X" }, authorization: auth })).blocked.code, "resume_route_not_verified");
  assert.equal((await fillResumeOnce({ tab, plan: profilePlan, facts: { name: "X" } })).blocked.code, "authorization_missing");
});

test("sensitive field requires destination consent and reports protected readback", async () => {
  let value = "";
  const tab = { url: async () => "https://example.test/resume", playwright: {
    domSnapshot: async () => "resume",
    locator: () => ({ count: async () => 1, isVisible: async () => true, isEnabled: async () => true,
      evaluate: async () => value ? "<redacted>" : "", fill: async input => { value = input; } })
  } };
  const plan = { origin: "https://example.test", matches: url => url === "https://example.test/resume",
    ready: text => text === "resume", fields: [{ key: "email", sensitive: true, control: { kind: "id", id: "email" } }] };
  const denied = await fillResumeOnce({ tab, plan, facts: { email: "candidate@example.test" }, authorization: { prepareProfile: true, origins: [plan.origin] } });
  assert.equal(denied.blocked.code, "sensitive_field_not_authorized");
  const allowed = await fillResumeOnce({ tab, plan, facts: { email: "candidate@example.test" }, authorization: { prepareProfile: true, origins: [plan.origin], shareSensitiveWith: { [plan.origin]: true } } });
  assert.equal(allowed.ok, true);
  assert.equal(allowed.verification.email, "redacted_nonempty");
  const again = await fillResumeOnce({ tab, plan, facts: { email: "candidate@example.test" }, authorization: { prepareProfile: true, origins: [plan.origin], shareSensitiveWith: { [plan.origin]: true } } });
  assert.equal(again.ok, true);
  assert.equal(again.verification.email, "masked_existing_review_required");
  assert.deepEqual(again.completed, []);
});

test("JD plan is restricted to the campus resume and never uses the group form", async () => {
  assert.equal(jdPlan.matches("https://campus.jd.com/home#/resume?type=present"), true);
  assert.equal(jdPlan.matches("https://zhaopin.jd.com/web/ucenter/editResume/2"), false);
  assert.deepEqual(jdPlan.fields.map(field => field.key), ["name", "mobile", "email", "idNumber"]);
});

test("iFlytek plan requires its observed form route and explicit legacy doctoral meaning", async () => {
  assert.equal(iflytekPlan.matches("https://iflytek.zhiye.com/form?fromPage=editMyResume"), true);
  assert.equal(iflytekPlan.matches("https://iflytek.zhiye.com/intern/jobs"), false);
  const mock = { playwright: { locator: () => ({ filter: () => ({ count: async () => 1, innerText: async () => "学历 博士研究生",locator:()=>({count:async()=>1,evaluate:async fn=>fn({value:"Example University"})}) }) }), getByText: () => ({}) } };
  assert.equal(await iflytekPlan.preflight(mock, { doctoralSchool: "Example University" }), true);
});

test("ZTE plan binds the Moka tenant route, not another company's resume", () => {
  assert.equal(ztePlan.matches("https://app.mokahr.com/campus-recruitment/zte/46903#/candidateHome/resume"), true);
  assert.equal(ztePlan.matches("https://app.mokahr.com/campus-recruitment/step/94905#/candidateHome/resume"), false);
  assert.deepEqual(ztePlan.fields.map(field => field.key), ["residenceCity", "idNumber"]);
  assert.notEqual(ztePlan.consentKey, stepPlan.consentKey);
  assert.equal(ztePlan.fields[0].sensitive, true);
  assert.equal(stepPlan.matches("https://app.mokahr.com/campus-recruitment/step/94905#/candidateHome/resume"), true);
  assert.equal(stepPlan.matches("https://app.mokahr.com/campus-recruitment/zte/46903#/candidateHome/resume"), false);
});

test("shared Moka origin still requires consent for the exact company tenant", async () => {
  const { tab } = fake({}, "https://app.mokahr.com/campus-recruitment/step/94905#/candidateHome/resume");
  tab.playwright.domSnapshot = async () => "个人信息 教育背景 保存 Synthetic University";
  const result = await fillResumeOnce({ tab, plan: stepPlan, facts: { idNumber: "SYNTHETIC-ID" },
    authorization: { prepareProfile: true, origins: [stepPlan.origin], shareSensitiveWith: { [ztePlan.consentKey]: true } } });
  assert.equal(result.blocked.code, "sensitive_field_not_authorized");
});

test("Moka resume readiness depends on modules and never on an applicant school",()=>{
 for(const plan of [ztePlan,stepPlan]){
  assert.equal(plan.ready("个人信息 教育背景 保存 Synthetic University"),true);
  assert.equal(plan.ready("职位详情 Synthetic University"),false);
 }
});
test("generic Ant education facts preserve an explicit undergraduate degree and school",async()=>{
 const f=fake({"#editForm_educations_0_school":"Synthetic College"});
 f.tab.playwright.locator=selector=>({count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,evaluate:async fn=>fn({value:f.values[selector]??"",closest:()=>({querySelector:()=>({textContent:"本科"})})}),fill:async value=>{f.values[selector]=value;f.calls.push(selector);}});
 const out=await fillResumeOnce({tab:f.tab,plan:profilePlan,facts:{educationExpectedSchool:"Synthetic College",educationDegree:"本科",educationMajor:"Business"},authorization:auth});
 assert.equal(out.ok,true);assert.deepEqual(out.completed,["educationMajor"]);assert.equal(f.values["#editForm_educations_0_major"],"Business");
});
test("generic education requires the caller's exact existing school and conflicting aliases stop",async()=>{
 const f=fake({"#editForm_educations_0_school":"User College"});
 const mismatch=await fillResumeOnce({tab:f.tab,plan:profilePlan,facts:{educationExpectedSchool:"Other College",educationDegree:"本科",educationMajor:"Business"},authorization:auth});
 assert.equal(mismatch.ok,false);assert.deepEqual(f.calls,[]);
 const alias=await fillResumeOnce({tab:f.tab,plan:profilePlan,facts:{doctoralMajor:"Physics",educationMajor:"Business"},authorization:auth});
 assert.equal(alias.blocked.code,"conflicting_fact_aliases");assert.deepEqual(f.calls,[]);
});
test("a later field conflict prevents earlier writes and preparatory mutations",async()=>{
 const f=fake({"#editForm_educations_0_major":"User Major"});
 const out=await fillResumeOnce({tab:f.tab,plan:profilePlan,facts:{name:"Synthetic Candidate",doctoralMajor:"New Major"},authorization:auth});
 assert.equal(out.blocked.code,"existing_value_conflict");assert.deepEqual(f.calls,[]);
});
test("caller fact changes during an awaited read do not change the reviewed fill",async()=>{
 const facts={name:"Reviewed Synthetic Name"},f=fake();
 f.tab.playwright.domSnapshot=async()=>{facts.name="Concurrent Caller Change";return "个人信息 教育经历 保存简历";};
 const out=await fillResumeOnce({tab:f.tab,plan:profilePlan,facts,authorization:auth});
 assert.equal(out.ok,true);assert.equal(f.values["#basic_name"],"Reviewed Synthetic Name");
});
test("generic iFlytek unique education controls preserve an undergraduate identity",async()=>{
 const values={学校名称:"Synthetic College",专业名称:""},calls=[];
 const tab={url:async()=>"https://iflytek.zhiye.com/form?fromPage=editMyResume",playwright:{
  domSnapshot:async()=>"个人信息 教育经历 保存",
  getByText:text=>({text}),locator:()=>({filter:({has})=>({count:async()=>1,innerText:async()=>has.text==="学历"?"学历 本科":has.text,
   locator:()=>({count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,evaluate:async fn=>fn({value:values[has.text]??""}),fill:async value=>{values[has.text]=value;calls.push(has.text);}})})})}};
 const out=await fillResumeOnce({tab,plan:iflytekPlan,facts:{educationExpectedSchool:"Synthetic College",educationDegree:"本科",educationMajor:"Business"},authorization:{prepareProfile:true,origins:[iflytekPlan.origin]}});
 assert.equal(out.ok,true);assert.equal(values.专业名称,"Business");assert.deepEqual(calls,["专业名称"]);
});

test("empty compatibility education keys and a school anchor do not request a degree mutation",async()=>{
 const facts={doctoralSchool:null,doctoralMajor:undefined,educationMajor:" ",educationDegree:"",educationExpectedSchool:"Synthetic College"};
 const calls=[],tab={playwright:{locator:()=>{calls.push("education-control");throw new Error("degree must not be accessed");}}};
 assert.deepEqual(await profilePlan.prepare(tab,facts),{completed:[]});
 assert.equal(await profilePlan.preflight(tab,facts),true);
 assert.equal(await iflytekPlan.prepare(tab,facts),true);
 assert.equal(await iflytekPlan.preflight(tab,facts),true);
 assert.deepEqual(calls,[]);
 const f=fake();
 const out=await fillResumeOnce({tab:f.tab,plan:profilePlan,facts:{name:"Synthetic Candidate",doctoralSchool:null,educationExpectedSchool:"Synthetic College"},authorization:auth});
 assert.equal(out.ok,true);assert.deepEqual(f.calls,["#basic_name"]);
});
test("generic education text requires an explicit degree even with a legacy compatibility fact",async()=>{
 const facts={educationExpectedSchool:"Synthetic College",educationMajor:"Business",doctoralTutor:"Synthetic Tutor"};
 const f=fake({"#editForm_educations_0_school":"Synthetic College"});
 assert.equal(await profilePlan.prepare(f.tab,facts),false);assert.deepEqual(f.calls,[]);
 const tab={playwright:{getByText:()=>({}),locator:()=>({filter:()=>({count:async()=>1})})}};
 assert.equal(await iflytekPlan.prepare(tab,facts),false);
});
test("Ant school conflicts and alias conflicts stop before selecting a blank degree",async()=>{
 for(const facts of [{doctoralSchool:"Other College"},{doctoralSchool:"Other College",educationSchool:"Synthetic College",educationDegree:"本科",educationExpectedSchool:"Synthetic College"}]){
  const f=fake({"#editForm_educations_0_school":"Synthetic College"});
  const base=f.tab.playwright.locator;
  f.tab.playwright.locator=selector=>selector==="#editForm_educations_0_topDegree"?{count:async()=>1,evaluate:async()=>""}:base(selector);
  f.tab.playwright.getByRole=()=>({click:async()=>f.calls.push("degree-open")});
  f.tab.playwright.getByText=()=>({count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,click:async()=>f.calls.push("degree-select")});
  assert.equal(await profilePlan.prepare(f.tab,facts),false);assert.deepEqual(f.calls,[]);
 }
});
test("navigation during preflight stops before any preparation or text write",async()=>{
 const f=fake();let route="https://talent.antgroup.com/personal/campus-resume",prepared=0;
 f.tab.url=async()=>route;f.tab.playwright.domSnapshot=async()=>{route="https://talent.antgroup.com/campus";return "个人信息 教育经历 保存简历";};
 const plan={...profilePlan,prepare:async()=>{prepared++;return true;}};
 const out=await fillResumeOnce({tab:f.tab,plan,facts:{name:"Synthetic Candidate"},authorization:auth});
 assert.equal(out.blocked.code,"route_changed");assert.equal(prepared,0);assert.deepEqual(f.calls,[]);
});
test("same-route school changes after preparation or a prior field stop education writes",async()=>{
 for(const mutationRead of [2,4]){
  const f=fake({"#editForm_educations_0_school":"School A"});let degreeReads=0;
  const base=f.tab.playwright.locator;
  f.tab.playwright.locator=selector=>selector==="#editForm_educations_0_topDegree"?{count:async()=>1,evaluate:async fn=>{
   if(++degreeReads===mutationRead)f.values["#editForm_educations_0_school"]="School B";
   return fn({closest:()=>({querySelector:()=>({textContent:"本科"})})});
  }}:base(selector);
  const out=await fillResumeOnce({tab:f.tab,plan:profilePlan,facts:{name:"Synthetic Name",educationExpectedSchool:"School A",educationDegree:"本科",educationMajor:"Business"},authorization:auth});
  assert.equal(out.blocked.code,"profile_scope_not_verified");assert.equal(f.values["#editForm_educations_0_major"],undefined);assert.deepEqual(f.calls,mutationRead===2?[]:["#basic_name"]);
  const values={学校名称:"School A",姓名:"",专业名称:""},calls=[];degreeReads=0;
  const tab={url:async()=>"https://iflytek.zhiye.com/form?fromPage=editMyResume",playwright:{domSnapshot:async()=>"个人信息 教育经历 保存",getByText:text=>({text}),locator:()=>({filter:({has})=>({count:async()=>1,innerText:async()=>{
   if(has.text==="学历"){if(++degreeReads===mutationRead)values.学校名称="School B";return "学历 本科";}return has.text;
  },locator:()=>({count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,evaluate:async fn=>fn({value:values[has.text]??""}),fill:async value=>{values[has.text]=value;calls.push(has.text);}})})})}};
  const result=await fillResumeOnce({tab,plan:iflytekPlan,facts:{name:"Synthetic Name",educationExpectedSchool:"School A",educationDegree:"本科",educationMajor:"Business"},authorization:{prepareProfile:true,origins:[iflytekPlan.origin]}});
  assert.equal(result.blocked.code,"profile_scope_not_verified");assert.equal(values.专业名称,"");assert.deepEqual(calls,mutationRead===2?[]:["姓名"]);
 }
});
test("legacy doctoral text requires a school anchor and rechecks it before writing",async()=>{
 for(const change of [false,true]){
  const f=fake({"#editForm_educations_0_school":"Doctoral School"});let reads=0;const base=f.tab.playwright.locator;
  f.tab.playwright.locator=selector=>selector==="#editForm_educations_0_topDegree"?{count:async()=>1,evaluate:async fn=>{
   if(change&&++reads===3)f.values["#editForm_educations_0_school"]="Different School";
   return fn({closest:()=>({querySelector:()=>({textContent:"博士"})})});
  }}:base(selector);
  const out=await fillResumeOnce({tab:f.tab,plan:profilePlan,facts:{doctoralSchool:"Doctoral School",doctoralMajor:"Computer Science"},authorization:auth});
  assert.equal(out.ok,!change);assert.equal(f.values["#editForm_educations_0_major"],change?undefined:"Computer Science");
 }
 const f=fake();assert.equal((await fillResumeOnce({tab:f.tab,plan:profilePlan,facts:{doctoralMajor:"Computer Science"},authorization:auth})).blocked.code,"profile_scope_not_verified");assert.deepEqual(f.calls,[]);
 const tab={playwright:{getByText:()=>({}),locator:()=>({filter:()=>({count:async()=>1,locator:()=>({count:async()=>1})})})}};
 assert.equal(await iflytekPlan.prepare(tab,{doctoralMajor:"Computer Science"}),false);
});
test("legacy iFlytek exact school facts can fill a unique blank school and establish the write anchor",async()=>{
 const values={学校名称:"",专业名称:""},calls=[];
 const tab={url:async()=>"https://iflytek.zhiye.com/form?fromPage=editMyResume",playwright:{domSnapshot:async()=>"个人信息 教育经历 保存",getByText:text=>({text}),locator:()=>({filter:({has})=>({count:async()=>1,innerText:async()=>has.text==="学历"?"学历 博士研究生":has.text,locator:()=>({count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,evaluate:async fn=>fn({value:values[has.text]??""}),fill:async value=>{values[has.text]=value;calls.push(has.text);}})})})}};
 const result=await fillResumeOnce({tab,plan:iflytekPlan,facts:{doctoralSchool:"Doctoral School",doctoralMajor:"Computer Science"},authorization:{prepareProfile:true,origins:[iflytekPlan.origin]}});
 assert.equal(result.ok,true);assert.deepEqual(calls,["学校名称","专业名称"]);assert.equal(values.学校名称,"Doctoral School");
});
