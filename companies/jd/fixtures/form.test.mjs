import test from "node:test";
import assert from "node:assert/strict";
import {inspect,fill,verify} from "../form.mjs";
import {fillResumeOnce} from "../../../scripts/profile-fill.mjs";

const origin = "https://campus.jd.com";
function fixture(initial = {}, url = origin + "/home#/resume?type=present") {
  const values = {...initial}, writes = [];
  const locator = selector => ({count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,
    evaluate:async fn=>fn({value:values[selector] ?? "",closest:()=>({querySelector:()=>({textContent:values[selector] ?? ""})})}),
    fill:async value=>{values[selector]=value;writes.push(selector);},click:async()=>{}});
  const tab = {url:async()=>url,playwright:{domSnapshot:async()=>"教育经历 招聘信息来源 保 存",locator,
    getByRole:(_role,{name})=>({count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,
      evaluate:async fn=>fn({checked:values.gender === name}),check:async()=>{values.gender=name;writes.push("gender");}})}};
  const ctx = {tab,input:{},helpers:{fillResumeOnce},authorization:{prepareProfile:true,origins:[origin],shareSensitiveWith:{[origin]:true}}};
  return {ctx,values,writes};
}

test("standalone JD basic fields use the shared runner and independent page readback",async()=>{
  const f=fixture();f.ctx.input={name:"Synthetic Candidate",mobile:"SYNTHETIC-PHONE",email:"candidate@example.test",idNumber:"SYNTHETIC-ID",gender:"女"};
  const result=await fill(f.ctx);assert.equal(result.ok,true);assert.equal(result.saved,false);assert.equal(result.submitted,false);
  assert.equal(f.values["#mobile"],"SYNTHETIC-PHONE");assert.equal(f.values.gender,"女");
  const checked=await verify(f.ctx);assert.equal(checked.ok,true);assert.equal(checked.fields.length,5);assert.equal(checked.saved,false);
  const before=[...f.writes];f.values["#name"]="User Change";
  const changed=await verify(f.ctx);assert.equal(changed.ok,false);assert.equal(changed.fields.find(field=>field.key==="name").status,"failed");assert.deepEqual(f.writes,before);
});

test("missing input, unknown fields and invalid options do not claim a successful fill",async()=>{
  for(const input of [{},{name:" "},{other:"Synthetic"},{name:42},{name:"Synthetic",gender:"unknown"},{source:"guessed-source"}]){
    const f=fixture();f.ctx.input=input;assert.equal((await fill(f.ctx)).ok,false);assert.deepEqual(f.writes,[]);
  }
});

test("wrong origin, wrong JD surface and cancelled execution stop without writes",async()=>{
  for(const url of ["https://other.example.test/home#/resume?type=present",origin+"/home#/jobs",origin+"/elsewhere"]){
    const f=fixture({},url);f.ctx.input={name:"Synthetic Candidate"};assert.equal((await inspect(f.ctx)).ok,false);assert.equal((await fill(f.ctx)).ok,false);assert.deepEqual(f.writes,[]);
  }
  const f=fixture();f.ctx.input={name:"Synthetic Candidate"};f.ctx.signal={aborted:true};assert.equal((await fill(f.ctx)).blocked.code,"cancelled");assert.deepEqual(f.writes,[]);
});

test("sensitive authorization and nonempty conflicts are preserved by the wrapper",async()=>{
  const f=fixture();f.ctx.input={name:"Synthetic Candidate",mobile:"SYNTHETIC-PHONE"};delete f.ctx.authorization.shareSensitiveWith;
  assert.equal((await fill(f.ctx)).blocked.code,"sensitive_field_not_authorized");assert.deepEqual(f.writes,[]);
  const g=fixture({"#mobile":"User Phone"});g.ctx.input={name:"Synthetic Candidate",mobile:"SYNTHETIC-PHONE"};
  assert.equal((await fill(g.ctx)).blocked.code,"existing_value_conflict");assert.deepEqual(g.writes,[]);
});

test("masked values remain a review requirement and are not exact verified facts",async()=>{
  const f=fixture({"#email":"<redacted>"});f.ctx.input={email:"candidate@example.test"};
  const result=await fill(f.ctx);assert.equal(result.fields[0].status,"review_required");assert.deepEqual(f.writes,[]);
  const checked=await verify(f.ctx);assert.equal(checked.ok,false);assert.equal(checked.fields[0].status,"review_required");
});

test("an explicitly existing source is verified without invoking a guessed dropdown branch",async()=>{
  const f=fixture({"#source":"其他"});f.ctx.input={source:"其他"};
  const result=await fill(f.ctx);assert.equal(result.ok,true);assert.equal((await verify(f.ctx)).ok,true);assert.deepEqual(f.writes,[]);
});
