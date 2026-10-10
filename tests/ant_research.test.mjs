import test from "node:test";
import assert from "node:assert/strict";
import { fillResearchOnce } from "../companies/antgroup/profile-research.mjs";

const facts = { papers: [0, 1, 2].map(index => ({ index, title: `Synthetic study ${index}`, description: `Synthetic methods ${index}`, status: "已发布" })), otherInfo: "Synthetic research summary" };
const auth = { prepareProfile: true, origins: ["https://talent.antgroup.com"] };

function fake(options = {}) {
  const values = { ...options.values }, selected = { ...options.selected }, calls = [];
  let active;
  let route = options.route ?? "https://talent.antgroup.com/personal/campus-resume";
  const tab = { url: async () => route, playwright: {
    domSnapshot: async () => "Synthetic publication option 已发布",
    locator: selector => {
      if (selector === ".ant-select-item-option-content") return { filter: ({ hasText, visible }) => {
        assert.equal(visible, true);
        return { count: async () => options.optionCount ?? 1, isEnabled: async () => true,
          innerText: async () => options.optionText ?? "已发布", click: async () => {
            calls.push("status:select");
            if (!options.statusReadbackFail) selected[active] = hasText;
          } };
      } };
      assert.ok(/^#basic_paperPublications_\d+_(name|description|status)$/.test(selector) || selector === "#basic_otherInformation");
      const status = selector.endsWith("_status");
      const textarea = selector.endsWith("_description") || selector === "#basic_otherInformation";
      return { count: async () => options.missing === selector ? 0 : options.fieldCount ?? 1,
        isVisible: async () => true, isEnabled: async () => true,
        evaluate: async fn => fn({ value: values[selector] ?? "", tagName: textarea ? "TEXTAREA" : "INPUT", readOnly: status,
          closest: () => ({ innerText: selected[selector] ?? "", querySelector: () => selected[selector] ? {} : null }) }),
        fill: async value => { calls.push(selector); if (!options.textReadbackFail) values[selector] = value; if (options.changeRoute) route = "https://talent.antgroup.com/campus"; },
        click: async () => { active = selector; calls.push("status:open"); }
      };
    }
  } };
  return { tab, values, selected, calls };
}
const run = (f, override = {}) => fillResearchOnce({ tab: f.tab, facts, authorization: auth, ...override });

test("fills three existing papers and other research text with exact readback", async () => {
  const f = fake();
  const result = await run(f);
  assert.equal(result.ok, true);
  assert.equal(result.changed, true);
  assert.equal(result.completed.length, 10);
  assert.equal(f.values["#basic_otherInformation"], facts.otherInfo);
  assert.equal(f.selected["#basic_paperPublications_2_status"], "已发布");
  assert.equal(result.saved, false);
  assert.equal(result.submitted, false);
});

test("selected status is read from rendered select even though input value is empty", async () => {
  const values = { "#basic_otherInformation": facts.otherInfo }, selected = {};
  for (const paper of facts.papers) {
    values[`#basic_paperPublications_${paper.index}_name`] = paper.title;
    values[`#basic_paperPublications_${paper.index}_description`] = paper.description;
    selected[`#basic_paperPublications_${paper.index}_status`] = paper.status;
  }
  const f = fake({ values, selected });
  const result = await run(f);
  assert.equal(result.ok, true);
  assert.equal(result.changed, false);
  assert.deepEqual(f.calls, []);
});

test("preflights all text including the last paper and otherInfo before edits", async () => {
  for (const id of ["#basic_paperPublications_2_description", "#basic_otherInformation"]) {
    const f = fake({ values: { [id]: "Existing user edit" } });
    assert.equal((await run(f)).blocked.code, "existing_research_conflict");
    assert.deepEqual(f.calls, []);
  }
});

test("preflights all publication statuses before text edits", async () => {
  const f = fake({ selected: { "#basic_paperPublications_2_status": "审核中" } });
  assert.equal((await run(f)).blocked.code, "existing_publication_status_conflict");
  assert.deepEqual(f.calls, []);
});

test("requires all requested cards to exist uniquely", async () => {
  for (const options of [{ missing: "#basic_paperPublications_2_name" }, { fieldCount: 2 }]) {
    const f = fake(options);
    assert.equal((await run(f)).ok, false);
    assert.deepEqual(f.calls, []);
  }
});

test("rejects unsupported indexes, duplicate indexes, statuses and too many papers", async () => {
  for (const papers of [[{ ...facts.papers[0], index: 50 }], [facts.papers[0], facts.papers[0]], [{ ...facts.papers[0], status: "未知" }], [...facts.papers, facts.papers[0]]]) {
    const f = fake();
    assert.equal((await run(f, { facts: { papers } })).blocked.code, "invalid_papers");
    assert.deepEqual(f.calls, []);
  }
});

test("requires preparation permission and exact destination", async () => {
  for (const authorization of [{}, { prepareProfile: true, origins: ["https://another.example"] }]) {
    const f = fake();
    assert.equal((await run(f, { authorization })).blocked.code, "authorization_missing");
    assert.deepEqual(f.calls, []);
  }
  const f = fake({ route: "https://talent.antgroup.com/campus" });
  assert.equal((await run(f)).blocked.code, "resume_route_not_verified");
  assert.deepEqual(f.calls, []);
});

test("duplicate or inexact published options cannot be selected", async () => {
  for (const options of [{ optionCount: 2 }, { optionText: "已发布（其他）" }]) {
    const f = fake(options);
    assert.equal((await run(f)).blocked.code, "publication_status_option_not_unique_or_available");
    assert.equal(f.calls.includes("status:select"), false);
  }
});

test("failed text/status readback never claims success or persistence", async () => {
  for (const options of [{ textReadbackFail: true }, { statusReadbackFail: true }]) {
    const result = await run(fake(options));
    assert.equal(result.ok, false);
    assert.equal(result.saved, false);
    assert.equal(result.submitted, false);
  }
});

test("route change stops subsequent fields", async () => {
  const f = fake({ changeRoute: true });
  assert.equal((await run(f)).blocked.code, "route_changed");
  assert.equal(f.calls.length, 1);
});

test("other research can be prepared alone without touching publication levels", async () => {
  const f = fake();
  const result = await run(f, { facts: { otherInfo: facts.otherInfo } });
  assert.equal(result.ok, true);
  assert.deepEqual(result.completed, ["otherInfo"]);
  assert.deepEqual(f.calls, ["#basic_otherInformation"]);
});

test("existing publication identifiers beyond the third row are supported without creating cards",async()=>{
 const f=fake(),paper={index:4,title:"Fifth Synthetic Paper",description:"Synthetic Fifth Result",status:"已发布"};
 const out=await fillResearchOnce({tab:f.tab,facts:{papers:[paper]},authorization:auth});
 assert.equal(out.ok,true);assert.equal(f.values["#basic_paperPublications_4_name"],paper.title);assert.equal(out.saved,false);
});
test("publication title changes after preflight stop before a description is written",async()=>{
 const f=fake(),title=f.tab.playwright.locator("#basic_paperPublications_0_name"),base=f.tab.playwright.locator;
 let reads=0;
 f.tab.playwright.locator=selector=>selector==="#basic_paperPublications_0_name"?{...title,evaluate:async fn=>{
  reads++;if(reads===4)f.values[selector]="Different Synthetic Paper";return title.evaluate(fn);
 }}:base(selector);
 const out=await run(f,{facts:{papers:[facts.papers[0]]}});
 assert.equal(out.blocked.code,"publication_identity_changed");assert.deepEqual(f.calls,["#basic_paperPublications_0_name"]);
});
