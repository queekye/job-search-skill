import {guardedPublicRead, publicText, unavailable} from "./surface.mjs";

const SECTION_FIELDS = Object.freeze({"岗位描述": "description", "岗位要求": "requirements", "课题描述": "description", "课题要求": "requirements", "加分项或注意事项": "additional_notes", "参加面试的城市": "interview_arrangement"});

/** Only the observed banner and variant-specific direct detail blocks; never document.body. */
export function extractDetailStructure() {
  const visible = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden" && getComputedStyle(e).display !== "none";
  const titles = [...document.querySelectorAll(".bannerTipLeft > .bannerItem.bannerItemText")].filter(visible);
  const lists = [...document.querySelectorAll("ul.post_detail")].filter(visible);
  if (titles.length !== 1 || lists.length !== 1) return {titleCount: titles.length, listCount: lists.length, sections: []};
  const blocks = [...lists[0].querySelectorAll(":scope > li.detail_box")].filter(visible);
  return {titleCount: 1, title: titles[0].innerText, listCount: 1, sectionCount: blocks.length,
    sections: blocks.map(block => {
      const subtitles = [...block.querySelectorAll(":scope > div.subtitle")].filter(visible);
      const texts = [...block.querySelectorAll(":scope > div.text_box, :scope > div.detail_text")].filter(visible);
      return {labelCount: subtitles.length, contentCount: texts.length,
        label: subtitles.length === 1 ? subtitles[0].innerText : null,
        content: texts.length === 1 ? texts[0].innerText : null,
        contentClass: texts.length === 1 && texts[0].classList.contains("text_box") ? "text_box" : texts.length === 1 && texts[0].classList.contains("detail_text") ? "detail_text" : null};
    })};
}

export function normalizeDetailStructure(structure) {
  const title = publicText(structure?.title, 500)?.replace(/\s+/g, " ");
  if (structure?.titleCount !== 1 || structure?.listCount !== 1 || !title || !Array.isArray(structure.sections) ||
      ![3, 4].includes(structure.sectionCount) || structure.sections.length !== structure.sectionCount) {
    return unavailable("detail_layout_needs_discovery");
  }
  const labels = structure.sections.map(s => s.label?.trim());
  const topic = labels.includes("课题描述") && labels.includes("课题要求");
  const job = labels.includes("岗位描述") && labels.includes("岗位要求");
  if (topic === job) return unavailable("detail_heading_pair_not_verified");
  const expectedLabels = topic ? ["课题描述", "课题要求", "参加面试的城市"] : ["岗位描述", "岗位要求", "加分项或注意事项", "参加面试的城市"];
  if (labels.length !== expectedLabels.length || new Set(labels).size !== labels.length || !expectedLabels.every(label => labels.includes(label))) {
    return unavailable("detail_variant_layout_needs_discovery");
  }
  const fields = {};
  for (const section of structure.sections) {
    const label = section.label?.trim();
    const field = SECTION_FIELDS[label];
    const content = publicText(section.content);
    const expectedClass = label === "参加面试的城市" ? "detail_text" : "text_box";
    if (!field || field in fields || section.labelCount !== 1 || section.contentCount !== 1 || !content || section.contentClass !== expectedClass) {
      return unavailable("detail_section_missing_duplicate_or_changed");
    }
    fields[field] = content;
  }
  if (Object.keys(fields).length !== expectedLabels.length) return unavailable("detail_section_missing_duplicate_or_changed");
  return {ok: true, status: "read", data: {title, detail_kind: topic ? "qingyun_topic" : "ordinary_job", additional_notes: null, ...fields, work_locations: null,
    work_locations_status: "not_expanded_or_verified_on_detail", recruitment_departments: null,
    scope: "observed_banner_and_variant_sections", coverage: "partial", verification: "detail_sections_verified",
    all_site_roles_exhausted: false, application_action_available: false,
    limitations: ["面试安排不是工作城市", "招聘部门和工作地未展开核验", "仅验证普通岗位四区块和青云课题三区块；其他布局需局部发现", "投递简历按钮不提供自动点击或提交能力"]}};
}

export function readCandidateDetailPage({tab, authorization, now} = {}) {
  return guardedPublicRead({tab, authorization, now, kind: "candidate_detail", extractor: extractDetailStructure, normalize: normalizeDetailStructure});
}
