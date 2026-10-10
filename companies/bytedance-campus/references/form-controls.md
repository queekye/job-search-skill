# Creation-form controls, schema 1

`../form-controls.mjs` exports `createBytedanceFormEditor`. It uses an existing authorized `agent.browsers` tab and supported Playwright operations. No browser is launched, no private API is called, and no profile values are persisted or returned. This module does not save, complete, upload, delete records, or submit an application.

## Evidence and scope

On 2026-09-12, the parent task successfully used `session.fillProfileField` for name, phone, email, school, college, major, research direction and supervisor. The parent also manually verified year/month picker selection and dropdown selection through the supported browser runtime. Later, the installed form-controls module passed 15 live education operations covering text filling, dropdown selection and checks of already-matching dates; project name and role filling also succeeded. Work links and descriptions for three records and a certificate description were subsequently filled and read back successfully. This does not verify successful completion/save or every changed-date path. Do not alter already-correct dates solely to manufacture a live test.

Two attempted changes of month within the already displayed year stopped when the module unnecessarily clicked the year heading. The parent then selected the visible target month directly and verified the resulting date. The revised module reads the visible year and skips that heading click when the year already matches. This revised branch has regression tests; its own live verification remains separate.

All operations require current URL `https://jobs.bytedance.com/campus/resume/edit`, either observed title `创建简历` or `编辑简历`, and both `基本信息` and `教育经历`. Generic resume text is insufficient. The edit title was observed after opening Edit from a saved resume, with the same fields and no verification overlay. Writes also require caller authorization `origins:["https://jobs.bytedance.com"]`, `prepareProfile:true`, and the selected nonempty `specificJobId`. These are current-session facts, not authorization granted by a skill. Existing preparation pauses still apply.

Repeated education entries were observed beneath `div.applyFormModuleWrapper-windows`, identified by `p.applyFormModuleWrapper-text` text `教育经历`. Each record is `div.apply-form-array-card__3QF2H`, containing `div.apply-form-array-card-content__eZqzM` and `div.register-form-group-wrapper`. Identify a record from its observed selected degree, major, and dates before injecting a unique locator. A selected-degree filter can help when its uniqueness is verified. The module does not choose a record by global position or treat an inferred degree as a user fact.

```js
const editor = createBytedanceFormEditor({
  tab: existingAuthorizedTab,
  authorization: existingPreparationAuthorization,
  scope: { verified: true, locator: observedUniqueEducationRecord }
});
await editor.selectYearMonth({ endpoint: "start", year: verifiedStartYear, month: verifiedStartMonth });
await editor.selectDropdown({ label: "学历", option: verifiedDegree });
await editor.fillFieldText({ label: "专业", value: verifiedMajor });
```

Omit `scope` for fields that are unique across the page. Alternatively pass `field:{verified:true,locator:observedUniqueField}` to a method. `scope` and a per-call `field` override are mutually exclusive. The caller must supply locators belonging to the injected tab; document URL is rechecked before writes, and cross-document URLs are refused. A URL comparison does not distinguish two tabs displaying the same URL, so never pass a locator from another tab.

All fields use the observed direct child `div.ud-formily-item-label` of `div.ud-formily-item`; normalize whitespace and trailing required asterisks. Inputs must belong to that closest field. Ambiguous labels, stale scopes and missing structure return a structured block with `next_action`, usually `needs_discovery`.

## Month picker

`selectYearMonth({label:"起止时间",endpoint:"start"|"end",year,month})` supports only this observed date range. The two inputs may omit their HTML `type` attribute while DOM `.type` is `text`; the selector accepts both. Both must be visible, owned by the field, and horizontally arranged at the same height. The left input is start and right input is end; the mapping is measured afresh and never uses global input indices.

Direct date filling followed by Tab was observed to clear the value. Instead, click the target input and read its unique visible `YYYY年` header. If the displayed year equals the target year, directly click the exact `MM月`. Otherwise click the year header, inspect the 20 unique consecutive visible four-digit year texts, select the exact year, then the exact month. To move to another 20-year range, use the observed `.ud__picker-panel-header-inner:visible button` filtered by `svg[data-icon="LeftBoldOutlined"]` or `RightBoldOutlined`. Verify the next range moved by exactly 20. The header itself shows a year, not a range. Unexpected year sets stop discovery; at most eight range changes are allowed per operation.

Clicking an input while another picker is open can merely close that picker. If the year header does not appear, stop and inspect; do not blindly click again. After selection, read the input in-page and return only `hasValue` and `matchesExpected`. A click alone never proves the date was set.

## Dropdowns and text

Observed `学历类型` options: 海外及港澳台、统招全日制、统招非全日制、自考、其他. Observed `学历` options: 博士、MBA、硕士、本科、大专、高中、专职、初中、小学. Unknown options require targeted discovery.

Click the unique owned combobox. Choose exact text within `.ud__select__list__item:visible`, since another record may already show the same selected text. Read only the owned `.ud__select__selector__selectItem` marker and compare it exactly in-page. Whole-field text may contain menu options and is not proof of selection. `期望工作地点` has a verified 北京 option, but its multiple-selection readback is pending; the module stops before changing it until that structure is verified.

`fillFieldText({label,value})` supports 姓名、手机号码、邮箱、学校名称、学院、专业、实验室、领域方向、导师、作品链接、项目名称、项目角色、项目链接、描述 and 自我评价. Description and self-evaluation use a textarea; the other fields require one owned non-search text input. These work, project and self-evaluation controls were observed by the parent task. Use only verified facts. Document number/type, unknown fields and blank values are rejected. Returned data contains the label and boolean readback, never the supplied value.

Every action refreshes the page observation. A failed action or mismatched readback returns `ok:false`, `data:null`, action-attempt count and whether the outcome may be uncertain. Inspect that state before any retry. Even `ok:true` means only the field was prepared: results explicitly state `saved:false` and `submitted:false`.

## Verification overlay failure

The parent observed an empty verification layer after pressing the page's completion control. The DOM had visible `div.sliderV2.atsx-slide-wrapper.zh`, fixed across the viewport with `z-index:9999`, containing `.atsx-slide-wrapper__mask` and `.atsx-slide-wrapper__content > .atsx-slide-wrapper__inner >` an empty div. It had no text, buttons, iframe or image, so a text-only snapshot missed it. Repeated completion clicks stacked three layers. Escape did not close them, and a reload still showed three empty layers. No network/CDN root cause has been verified.

The installed DOM checker was then verified live: the old page returned `empty_overlay,count:3`, and a clean page returned `none,count:0`. After restoring the draft into that clean page through the short-field workflow, a single completion action produced a normal visible slider challenge. The user completed it; the site reached `/campus/resume/view` with saved fields and attachment successfully read back. This is evidence of that recovery and save, not proof that reload alone clears empty overlays or that later saves will succeed. See [the mapping and restoration contract](field-map.md). Current review-only instructions or preparation pauses still govern any future work.

`inspectVerificationOverlay({tab})` from `../verification-overlay.mjs` uses read-only DOM inspection on the existing official-site tab. It reads no network state, application state, identifiers, form values or raw challenge text. It returns `none`, `visible_challenge` or `empty_overlay`, visible layer `count`, empty/challenge counts and a `next_action`. For stacked layers, the uppermost observed z-index/DOM-order layer determines the status. A DOM read failure or navigation gives `ok:false,status:"unknown",count:null`; it never becomes `none`.

`empty_overlay` returns `needs_user_or_page_recovery` and `retrySaveAllowed:false`. Preserve available draft information and stop blindly retrying save or completion. Use only an established recovery path under the current session's authorization; this helper neither removes the overlay nor proves that reload or a new tab can recover the draft. A visible challenge returns `assess_captcha_or_handoff`, still subject to current authorization and tools. `none` means no visible instance of this observed wrapper was found, not universal proof that no other overlay exists or that the form saved.

The form editor calls the checker during every observation before and after actions. Empty and visible challenge layers, as well as failed inspection, block further field actions. The block includes sanitized overlay counts. There is no new save, submission or verification-trigger interface.
