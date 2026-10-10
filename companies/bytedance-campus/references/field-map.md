# Structural field mapping and section restoration

`../field-map.mjs` exports `inspectFieldMap` and `restoreSectionFields`. These helpers reuse an authorized existing tab. They do not navigate, save, submit, upload, delete records or handle document identity fields.

During a live clean-page restoration on 2026-09-12, a complex nested scope locator passed count/visibility checks but its evaluation timed out. Reading global `div.ud-formily-item` indices and then using short `locator(...).nth(index)` fields succeeded. The parent completed 24 education operations including six changed dates, five project operations and five work-value operations, then compared input shapes, values and selections against the in-memory source with no differences. Those operations used the existing editor; the new mapping/restoration wrapper itself is validated offline and does not imply further permission to change a saved resume.

## Read-only map

```js
const map = await inspectFieldMap({ tab: existingAuthorizedTab });
```

The map reports the current global field `index`, direct `label`, normalized `canonicalLabel`, `section`, and input `tag/type` shapes. It reads no input values, checked facts, selected text, identity numbers or attachment content. Global indices include unlabeled outer wrappers even though those wrappers are omitted from the returned fields. This preserves the actual locator index.

Sections are identified with the observed `div.applyFormModuleWrapper-windows` and its `p.applyFormModuleWrapper-text`. A field label must be a direct `div.ud-formily-item-label`, and each input must have this field as its closest `div.ud-formily-item`. A missing section title remains null. The map is valid only for the observed `/campus/resume/edit` route and is refused under a verification overlay or an uncertain DOM check. The saved view route is not an editable field map.

## Restore only an authorized section

```js
const result = await restoreSectionFields({
  tab: existingAuthorizedTab,
  authorization: currentPreparationAuthorization,
  section: observedSectionTitle,
  fields: savedSectionFields
});
```

The in-memory input format is `[{label, inputs:[{tag,type,value,checked}], selected:[text]}]`, in the exact observed order for that whole section. Do not write this personal data into a plugin, test fixture or discovery artifact. All source facts must be verified and current preparation must remain authorized. A user pause or review-only request prevents restoration even if an earlier authorization object existed.

Before any mutation, the helper validates all nonempty source fields and checks section field count, label order and input shapes. It supports the editor's observed text/textarea fields, single-select education type/degree and date-range endpoints in `YYYY-MM` format. `自我评价` uses a textarea. Empty or unselected source fields are skipped rather than interpreted as instructions to erase information. An unknown field with a supplied value, a checked checkbox, unsupported selection or unsupported date format blocks the batch before it starts; identity fields are not handled.

Before each operation, obtain a fresh map and recheck the section structure. Derive the current short field locator from its new global index, then verify its direct label, section and document URL again immediately before calling the existing editor. A saved index is never reused unchecked. The editor still enforces unique controls, field ownership, authorization, page title, overlay guard and boolean readback. Do not run simultaneous restorations against one tab.

The optional `editor` parameter is for an existing `createBytedanceFormEditor` instance bound to this same tab and authorization, without a record scope. Otherwise the helper creates one. It is not an alternative implementation or a way to bypass guards.

On failure, stop immediately and return the completed operation count, stopped field position and structured reason, without source values. On success, coverage is `supported_nonempty_fields`, with explicit skipped entries, `saved:false` and `submitted:false`. This is not an assertion that skipped empty fields, an entire resume or persistent storage match the source. Review the resulting draft; saving remains a separate currently authorized user workflow.
