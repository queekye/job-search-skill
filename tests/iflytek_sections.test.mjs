import test from "node:test";
import assert from "node:assert/strict";
import { fillParsedSectionsOnce } from "../companies/iflytek/profile-sections.mjs";

const origin = "https://iflytek.zhiye.com";
const authorization = { prepareProfile: true, origins: [origin], shareSensitiveWith: { [origin]: true } };
const regionFacts = { hometown: { province: "示例省", city: "样本市" }, residence: { province: "测试省", city: "测试市" } };
const educationFacts = [
  { degree: "博士研究生", school: "Example University", startDate: "2020-09", endDate: "2025-06", studyMode: "统招全日制", highest: true, rank: "其他" },
  { degree: "硕士研究生", school: "Sample University", startDate: "2017-09", endDate: "2020-06", studyMode: "统招全日制", highest: false, rank: "前50%" },
  { degree: "本科", school: "Synthetic University", startDate: "2013-09", endDate: "2017-06", studyMode: "统招全日制", highest: false, rank: "其他" }
];

function fake({ route = `${origin}/form?fromPage=editMyResume`, regions = {}, cards, duplicateCity = false, regionReadbackFails = false, developmentLanguage1 = "" } = {}) {
  const calls = [], selectedRegions = { ...regions };
  const basic = { developmentLanguage1 };
  const educations = cards ?? educationFacts.map(({ studyMode, rank, highest, ...identity }) => ({ ...identity, studyMode: "", rank: "", highest: undefined }));
  let currentRoute = route, activeRegion = null, activeSelect = null, selectedCity = null;
  const labels = { 籍贯: "hometown", 现居住地: "residence", 学历类型: "studyMode", 专业排名: "rank", 擅长的开发语言1: "developmentLanguage1" };
  const visible = extra => ({ count: async () => 1, isVisible: async () => true, isEnabled: async () => true, ...extra });
  function control(label, index) {
    return visible({ innerText: async () => label === "擅长的开发语言1" ? basic.developmentLanguage1 : index === undefined ? selectedRegions[labels[label]] ?? "" : educations[index]?.[labels[label]] ?? "",
      click: async () => { calls.push(`open:${label}:${index ?? "basic"}`); if (label === "擅长的开发语言1") activeSelect = { label, index }; else if (index === undefined) { activeRegion = labels[label]; selectedCity = null; } else activeSelect = { label, index }; } });
  }
  function input(label, index) {
    const key = ({学校名称:"school",开始时间:"startDate",结束时间:"endDate",学院名称:"department",专业名称:"major",实验室:"laboratory",研究方向:"researchField",导师:"tutor"})[label];
    return visible({evaluate:async fn=>fn({value:educations[index]?.[key]??""}),fill:async value=>{educations[index][key]=value;calls.push(`text:${index}:${label}`);}});
  }
  function scope(label, index) {
    return visible({ label, index, kind: "field",
      locator(selector) {
        if (selector === ".phoenix-select") return control(label, index);
        if (selector.startsWith("input")) return input(label, index);
        if (selector === ".phoenix-radio-group__radioItem") return { evaluateAll: async fn => fn(["是", "否"].map(text => ({ textContent: text,
          querySelector: () => educations[index].highest === (text === "是") ? {} : null }))) };
        throw new Error("unsupported synthetic selector");
      },
      innerText: async () => label === "学历" ? `学历 ${educations[index]?.degree}` : `${label} ${index === undefined ? selectedRegions[labels[label]] ?? "" : ""}`,
      getByText(text) { return visible({ kind: "degreeToken", degree: text,
        click: async () => { calls.push(`radio:${index}:${text}`); educations[index].highest = text === "是"; } }); }
    });
  }
  function fields(index) { return { filter: ({ has }) => scope(has.text, index) }; }
  function card(index) { return visible({ count: async () => index < 0 ? 0 : 1, locator: selector => {
    assert.equal(selector, ".form-item"); return fields(index);
  } }); }
  const cardsCollection = { count: async () => educations.length, nth:index=>card(index),
    filter: ({ has }) => card(educations.findIndex(education => education.degree === has.degree)) };
  const tab = { url: async () => currentRoute, playwright: {
    domSnapshot: async () => `个人信息 教育经历 保存 ${selectedCity ? `1/1 ${selectedCity}` : ""}`,
    getByText: text => visible({ text, click: async () => {
      if (text === "确定") { calls.push("confirm_region"); if (!regionReadbackFails) selectedRegions[activeRegion] = selectedCity; activeRegion = null; }
      else calls.push(`province:${text}`);
    } }),
    locator(selector) {
      if (selector === ".form-item") return fields();
      if (selector === ".form-part-body") return { filter: () => cardsCollection };
      if (selector === ".area-item-container") return { filter: ({ has }) => ({ locator: child => {
        assert.equal(child, ".icon-container"); return visible({ count: async () => duplicateCity ? 2 : 1,
          click: async () => { calls.push(`city_checkbox:${has.text}`); selectedCity = has.text; } });
      } }) };
      if (selector === ".phoenix-selectList__singleLabel") return { getByText: text => visible({
        click: async () => { calls.push(`select:${activeSelect.index}:${text}`); if (activeSelect.index === undefined) basic[labels[activeSelect.label]] = text; else educations[activeSelect.index][labels[activeSelect.label]] = text; }
      }) };
      throw new Error("unsupported synthetic selector");
    }
  } };
  return { tab, calls, selectedRegions, educations, basic, move: value => { currentRoute = value; } };
}

test("selects a city checkbox after entering its province and confirms both regions", async () => {
  const run = fake();
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: regionFacts, authorization });
  assert.equal(out.ok, true);
  assert.deepEqual(out.completed, ["hometown", "residence"]);
  assert.deepEqual(run.calls, ["open:籍贯:basic", "province:示例省", "city_checkbox:样本市", "confirm_region", "open:现居住地:basic", "province:测试省", "city_checkbox:测试市", "confirm_region"]);
  assert.equal(out.saved, false); assert.equal(out.submitted, false);
  assert.equal(JSON.stringify(out).includes("样本市"), false);
});

test("checks all provided regions before writing and preserves a conflicting later region", async () => {
  const run = fake({ regions: { residence: "另一个市" } });
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: regionFacts, authorization });
  assert.equal(out.blocked.code, "existing_region_conflict");
  assert.deepEqual(run.calls, []);
});

test("existing matching cities are idempotent", async () => {
  const run = fake({ regions: { hometown: "样本市", residence: "测试市" } });
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: regionFacts, authorization });
  assert.equal(out.ok, true); assert.deepEqual(run.calls, []);
});

test("rejects missing destination-sensitive authorization and wrong route before reading controls", async () => {
  const run = fake();
  assert.equal((await fillParsedSectionsOnce({ tab: run.tab, facts: regionFacts, authorization: { prepareProfile: true, origins: [origin] } })).blocked.code, "authorization_missing");
  run.move("https://other.example/form?fromPage=editMyResume");
  assert.equal((await fillParsedSectionsOnce({ tab: run.tab, facts: regionFacts, authorization })).blocked.code, "resume_route_not_verified");
  assert.deepEqual(run.calls, []);
});

test("duplicate city controls stop without selecting or confirming", async () => {
  const run = fake({ duplicateCity: true });
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: { hometown: regionFacts.hometown }, authorization });
  assert.equal(out.blocked.code, "city_checkbox_not_verified");
  assert.deepEqual(run.calls, ["open:籍贯:basic", "province:示例省"]);
});

test("failed region readback stops before changing a second region", async () => {
  const run = fake({ regionReadbackFails: true });
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: regionFacts, authorization });
  assert.equal(out.blocked.code, "region_readback_failed");
  assert.equal(run.calls.some(call => call === "open:现居住地:basic"), false);
});

test("fills supplied type, highest-degree and ranking facts on three identity-matched cards", async () => {
  const run = fake();
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: { educations: educationFacts }, authorization });
  assert.equal(out.ok, true);
  assert.equal(out.completed.length, 9);
  for (let index = 0; index < 3; index++) {
    assert.equal(run.educations[index].studyMode, educationFacts[index].studyMode);
    assert.equal(run.educations[index].highest, educationFacts[index].highest);
    assert.equal(run.educations[index].rank, educationFacts[index].rank);
  }
  assert.equal(out.saved, false); assert.equal(out.submitted, false);
  assert.equal(JSON.stringify(out).includes("Example University"), false);
});

test("checks education dates before changing any provided region or education value", async () => {
  const run = fake();
  run.educations[2].endDate = "2017-05";
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: { ...regionFacts, educations: educationFacts }, authorization });
  assert.equal(out.blocked.code, "education_identity_not_verified");
  assert.deepEqual(run.calls, []);
});

test("preflights a later education conflict before writing earlier empty cards", async () => {
  const run = fake();
  run.educations[2].rank = "前50%";
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: { educations: educationFacts }, authorization });
  assert.equal(out.blocked.code, "existing_education_conflict");
  assert.deepEqual(run.calls, []);
});

test("refuses a conflicting checked highest-degree flag without overwriting it", async () => {
  const run = fake();
  run.educations[1].highest = true;
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: { educations: educationFacts }, authorization });
  assert.equal(out.blocked.code, "existing_education_conflict");
  assert.deepEqual(run.calls, []);
});

test("existing matching education selections are idempotent", async () => {
  const run = fake({ cards: educationFacts.map(education => ({ ...education })) });
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: { educations: educationFacts }, authorization });
  assert.equal(out.ok, true); assert.deepEqual(run.calls, []);
});

test("accepts fewer identity-matched cards and refuses duplicate caller identities", async () => {
  const run = fake({ cards: educationFacts.slice(0, 2) });
  assert.equal((await fillParsedSectionsOnce({ tab: run.tab, facts: { educations: educationFacts.slice(0, 2) }, authorization })).ok, true);
  const full = fake();
  assert.equal((await fillParsedSectionsOnce({ tab: full.tab, facts: { educations: [educationFacts[0], educationFacts[0]] }, authorization })).blocked.code, "education_identity_not_unique");
  assert.deepEqual(full.calls, []);
});

test("selects an observed language option and accepts an exact duplicated rendered label", async () => {
  const run = fake();
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: { developmentLanguage1: "Python" }, authorization });
  assert.equal(out.ok, true); assert.equal(run.basic.developmentLanguage1, "Python");
  assert.deepEqual(out.completed, ["developmentLanguage1"]);
  const existing = fake({ developmentLanguage1: "PythonPython" });
  assert.equal((await fillParsedSectionsOnce({ tab: existing.tab, facts: { developmentLanguage1: "Python" }, authorization })).ok, true);
  assert.deepEqual(existing.calls, []);
});

test("a conflicting language stops before any provided region is filled", async () => {
  const run = fake({ developmentLanguage1: "Java" });
  const out = await fillParsedSectionsOnce({ tab: run.tab, facts: { ...regionFacts, developmentLanguage1: "Python" }, authorization });
  assert.equal(out.blocked.code, "existing_language_conflict"); assert.deepEqual(run.calls, []);
});

test("same-degree educations are matched by school and months after website reordering",async()=>{
 const first={...educationFacts[2],department:"Synthetic Department"},second={...educationFacts[2],school:"Other Synthetic University",startDate:"2018-09",endDate:"2022-06",major:"Synthetic Major"};
 const cards=[second,first].map(({studyMode,rank,highest,department,major,...identity})=>({...identity,studyMode:"",rank:""})),f=fake({cards});
 const out=await fillParsedSectionsOnce({tab:f.tab,facts:{educations:[first,second]},authorization});
 assert.equal(out.ok,true);assert.equal(f.educations[0].major,second.major);assert.equal(f.educations[1].department,first.department);
});
test("duplicate website education identities stop before changing regions or cards",async()=>{
 const identity={...educationFacts[2]},f=fake({cards:[identity,{...identity}]});
 const out=await fillParsedSectionsOnce({tab:f.tab,facts:{...regionFacts,educations:[identity]},authorization});
 assert.equal(out.blocked.code,"education_identity_not_verified");assert.deepEqual(f.calls,[]);
});
test("one undergraduate education card is sufficient for scoped text completion",async()=>{
 const identity={...educationFacts[2],tutor:"Synthetic Tutor"},f=fake({cards:[{...educationFacts[2]}]});
 const out=await fillParsedSectionsOnce({tab:f.tab,facts:{educations:[identity]},authorization});
 assert.equal(out.ok,true);assert.equal(f.educations[0].tutor,"Synthetic Tutor");
});
