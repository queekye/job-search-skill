import test from "node:test";
import assert from "node:assert/strict";
import { uploadResumeOnce, confirmResumeUploadOnce, readResumeUploadStatus } from "../scripts/profile-upload.mjs";
import { resumeUploadPlan as jd, profilePlan as jdProfile } from "../companies/jd/adapter.mjs";
import { resumeUploadPlan as iflytek } from "../companies/iflytek/adapter.mjs";
import { resumeUploadPlan as ant } from "../companies/antgroup/adapter.mjs";

const filePath = "C:\\synthetic-fixtures\\sample-resume.pdf";
const fileLabel = "sample-resume.pdf";
const authorizes = plan => ({ prepareProfile: true, shareResumeWith: { [plan.consentKey ?? plan.origin]: true }, replaceProfile: true });

function fake(plan = jd, { lateDialog = false, route, filenameVisible = true, busy = false, unnamedDialog = false } = {}) {
  const calls = [];
  let dialogVisible = false, attached = false, chooserListener = false;
  let currentRoute = route ?? (plan === jd ? "https://campus.jd.com/home#/resume?type=present" : plan === ant ? "https://talent.antgroup.com/personal/campus-resume" : "https://iflytek.zhiye.com/form?fromPage=editMyResume");
  const usable = extra => ({ count: async () => 1, isVisible: async () => true, isEnabled: async () => true, ...extra });
  const upload = usable({ click: async () => { assert.equal(chooserListener, true); calls.push("open_chooser"); } });
  upload.filter = () => upload;
  const tab = {
    url: async () => currentRoute,
    playwright: {
      domSnapshot: async () => `个人信息 教育经历 招聘信息来源 保 存 保存 保存简历 ${dialogVisible ? `${plan.confirmation.name ?? "温馨提示"} ${plan.confirmation.attachmentOnly}` : ""} ${attached && filenameVisible ? `${fileLabel} 重新上传` : ""} ${busy ? "简历上传解析中，请稍等片刻" : ""}`,
      getByText: () => upload,
      getByRole(role, options = {}) {
        if (role !== "dialog") return upload;
        const dialog = usable({ count: async () => dialogVisible && (!unnamedDialog || !options.name) ? 1 : 0, isVisible: async () => dialogVisible,
          getByRole: (buttonRole, options) => usable({ click: async () => { calls.push(options.name); dialogVisible = false; attached = true; } }) });
        dialog.filter = filter => { assert.equal(filter.hasText, plan.confirmation.dialogFilterText); return dialog; };
        return dialog;
      },
      waitForEvent: async event => { assert.equal(event, "filechooser"); chooserListener = true; calls.push("wait_chooser"); return { setFiles: async paths => { assert.deepEqual(paths, [filePath]); calls.push("select_single_file"); if (plan.confirmation && !lateDialog) dialogVisible = true; if (plan.automaticParse) attached = true; } }; }
    }
  };
  return { tab, calls, showDialog: () => { dialogVisible = true; }, move: value => { currentRoute = value; } };
}

test("upload rejects wrong route, relative path and missing destination consent before any chooser", async () => {
  for (const changes of [{ authorization: {} }, { filePath: "sample-resume.pdf" }, { authorization: { prepareProfile: true, shareResumeWith: { "https://other.example": true } } }]) {
    const { tab, calls } = fake();
    const out = await uploadResumeOnce({ tab, plan: jd, filePath, authorization: authorizes(jd), ...changes });
    assert.equal(out.ok, false);
    assert.deepEqual(calls, []);
  }
  const { tab, calls } = fake(jd, { route: "https://other.example/resume" });
  assert.equal((await uploadResumeOnce({ tab, plan: jd, filePath, authorization: authorizes(jd) })).blocked.code, "resume_route_not_verified");
  assert.deepEqual(calls, []);
});

test("binding parses only with independent replacement authorization", async () => {
  const { tab, calls } = fake();
  const authorization = { ...authorizes(jd), replaceProfile: false };
  assert.equal((await uploadResumeOnce({ tab, plan: jd, filePath, parse: true, authorization })).blocked.code, "profile_replacement_not_authorized");
  assert.deepEqual(calls, []);
});

test("one-file chooser is awaited before click and attachment-only binding leaves parse unverified", async () => {
  const { tab, calls } = fake();
  const out = await uploadResumeOnce({ tab, plan: jd, filePath, authorization: authorizes(jd) });
  assert.deepEqual(calls, ["wait_chooser", "open_chooser", "select_single_file", "否，仅上传附件"]);
  assert.equal(out.uploaded, true);
  assert.equal(out.parsed, null);
  assert.equal(out.saved, false);
  assert.equal(out.submitted, false);
  assert.equal(JSON.stringify(out).includes(fileLabel), false);
  assert.equal(JSON.stringify(out).includes(filePath), false);
});

test("late binding dialog can be confirmed separately without selecting a second file", async () => {
  const run = fake(ant, { lateDialog: true });
  const input = { tab: run.tab, plan: ant, filePath, parse: true, authorization: authorizes(ant) };
  const first = await uploadResumeOnce(input);
  assert.equal(first.uploaded, false);
  assert.equal(first.pending, "upload_pending");
  run.showDialog();
  const final = await confirmResumeUploadOnce(input);
  assert.equal(final.uploaded, true);
  assert.equal(final.parsed, null);
  assert.equal(run.calls.filter(call => call === "select_single_file").length, 1);
  assert.equal(run.calls.at(-1), "是，覆盖掉");
});

test("automatic iFlytek parsing requires explicit replacement, and busy readback stays pending", async () => {
  const { tab, calls } = fake(iflytek, { busy: true });
  const input = { tab, plan: iflytek, filePath, authorization: authorizes(iflytek) };
  assert.equal((await uploadResumeOnce(input)).blocked.code, "automatic_parse_requires_explicit_request");
  assert.deepEqual(calls, []);
  const out = await uploadResumeOnce({ ...input, parse: true });
  assert.equal(out.uploaded, false);
  assert.equal(out.parsed, null);
  assert.equal(out.pending, "parsing_pending");
});

test("file selection without authoritative filename evidence does not claim upload", async () => {
  const { tab } = fake(iflytek, { filenameVisible: false });
  const out = await uploadResumeOnce({ tab, plan: iflytek, filePath, parse: true, authorization: authorizes(iflytek) });
  assert.equal(out.uploaded, false);
  assert.equal(out.pending, "upload_pending");
});

test("readback is bounded to its company route and does not invoke upload", async () => {
  const run = fake();
  const input = { tab: run.tab, plan: jd, filePath, authorization: authorizes(jd) };
  await readResumeUploadStatus(input);
  assert.deepEqual(run.calls, []);
  run.move("https://campus.jd.com/home#/jobs");
  assert.equal((await readResumeUploadStatus(input)).blocked.code, "resume_route_not_verified");
  assert.deepEqual(run.calls, []);
});

test("JD gender checks only the requested radio and refuses a conflicting selection", async () => {
  let male = false, female = false;
  const tab = { playwright: { getByRole: (role, { name }) => ({
    count: async () => 1, isVisible: async () => true, isEnabled: async () => true,
    evaluate: async fn => fn({ checked: name === "男" ? male : female }),
    check: async () => { if (name === "男") male = true; else female = true; }
  }) } };
  assert.deepEqual(await jdProfile.afterFill(tab, { gender: "男" }), { completed: ["gender"] });
  assert.equal(male, true);
  female = true;
  assert.equal(await jdProfile.afterFill(tab, { gender: "男" }), false);
});

test("JD official-site source is not misrepresented by a different site option", async () => {
  assert.equal(await jdProfile.afterFill({ playwright: {} }, { source: "官网" }), false);
});

test("JD explicit Other source is read back and existing different selection is preserved", async () => {
  let selected = "", count = 0;
  const option = { count: async () => 1, isVisible: async () => true, innerText: async () => "其他",
    click: async () => { selected = "其他"; count += 1; } };
  const control = { count: async () => 1, click: async () => {},
    evaluate: async fn => fn({ closest: () => ({ querySelector: () => ({ textContent: selected }) }) }) };
  const tab = { playwright: { locator: selector => selector === "#source" ? control : { filter: () => option } } };
  assert.deepEqual(await jdProfile.afterFill(tab, { source: "其他" }), { completed: ["source"] });
  assert.equal(count, 1);
  selected = "公众号";
  assert.equal(await jdProfile.afterFill(tab, { source: "其他" }), false);
  assert.equal(selected, "公众号");
  assert.equal(count, 1);
});

test("chooser failures return a bounded code without paths or raw exception content", async () => {
  const { tab } = fake();
  tab.playwright.waitForEvent = () => Promise.reject(new Error(`private error ${filePath}`));
  const out = await uploadResumeOnce({ tab, plan: jd, filePath, authorization: authorizes(jd) });
  assert.equal(out.blocked.code, "filechooser_not_confirmed");
  assert.equal(JSON.stringify(out).includes(filePath), false);
  assert.equal(JSON.stringify(out).includes("private error"), false);
});

test("changing company route after chooser opens prevents file transmission", async () => {
  const run = fake();
  run.tab.playwright.waitForEvent = async () => ({ setFiles: async () => assert.fail("must not transmit after route changes") });
  run.tab.playwright.getByRole = () => ({ count: async () => 1, isVisible: async () => true, isEnabled: async () => true,
    click: async () => run.move("https://other.example/resume") });
  const out = await uploadResumeOnce({ tab: run.tab, plan: jd, filePath, authorization: authorizes(jd) });
  assert.equal(out.blocked.code, "route_changed");
});

test("Ant upload is bound to the public button text when a current filename changes its accessible name", () => {
  const calls = [];
  const filtered = {};
  const tab = { playwright: { getByRole: (role, options) => {
    calls.push({ role, options });
    return { filter: filter => { calls.push(filter); return filtered; } };
  } } };
  assert.equal(ant.uploadControl(tab), filtered);
  assert.deepEqual(calls, [{ role: "button", options: undefined }, { hasText: "点击或拖拽简历文件到这里，文件将自动解析并替换原有文件" }]);
});

test("Ant binds its unnamed confirmation dialog by observed question text and selects attachment only", async () => {
  const run = fake(ant, { unnamedDialog: true });
  const out = await uploadResumeOnce({ tab: run.tab, plan: ant, filePath, parse: false, authorization: authorizes(ant) });
  assert.equal(out.uploaded, true);
  assert.equal(run.calls.at(-1), "否，仅替换附件");
  assert.equal(run.calls.includes("是，覆盖掉"), false);
});
