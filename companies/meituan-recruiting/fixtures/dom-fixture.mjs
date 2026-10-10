// Dependency-free synthetic DOM for the observed selector subset; no private records.
export function node(tag, classes = "", children = [], text = null, attributes = {}) {
  const result = {tag, children, attributes, parentElement: null, style: {display: "block", visibility: "visible"},
    classList: new Set(classes.split(/\s+/).filter(Boolean)),
    get innerText() {
      if (this.forbidRead) throw new Error("Unrelated or hidden synthetic data must not be read");
      return text ?? this.children.map(child => child.innerText).join("\n");
    },
    getClientRects() {return this.style.display === "none" ? [] : [{}];},
    querySelectorAll(selector) {
      const descendants = parent => parent.children.flatMap(child => [child, ...descendants(child)]);
      const matches = (candidate, simple) => {
        const attr = /\[([^=\]]+)="([^"]*)"\]/.exec(simple);
        if (attr && candidate.attributes[attr[1]] !== attr[2]) return false;
        const firstChild = simple.includes(":first-child");
        if (firstChild && candidate.parentElement?.children[0] !== candidate) return false;
        const [tagName, ...classNames] = simple.replace(/\[[^\]]+\]/g, "").replace(":first-child", "").split(".");
        return (!tagName || candidate.tag === tagName) && classNames.every(c => candidate.classList.has(c));
      };
      const tokens = selector.split(/\s+/);
      let parents = [this], direct = false;
      for (const token of tokens) {
        if (token === ":scope") continue;
        if (token === ">") {direct = true; continue;}
        parents = parents.flatMap(parent => (direct ? parent.children : descendants(parent)).filter(child => matches(child, token)));
        direct = false;
      }
      return parents;
    }
  };
  result.classList.contains = value => result.classList.has(value);
  for (const child of children) child.parentElement = result;
  return result;
}

const div = (classes, children = [], text = null) => node("div", classes, children, text);
const span = (classes, text) => node("span", classes, [], text);
export function volunteer(label = "志愿一", title = "合成研究岗位甲") {
  return div("volunteerItemSchedule", [
    div("jobName", [span("", label), span("title", title), span("tag", "合成专项")]),
    div("detailInfo workPlace", [span("", "工作城市："), span("", "合成城市")]),
    div("detailInfo bgName", [], "意向部门：合成研究组"), div("detailInfo time", [], "投递时间：2030-01-02"),
    div("statusWrap", [div("statusText", [span("", "已结束")])])
  ]);
}
export function project({name = "合成校园项目", expanded = true} = {}) {
  const states = ["finish", "finish", "process", "wait", "wait", "wait"];
  const titles = ["合成网申成功", "合成资格核验", "合成筛选中", "合成笔试", "合成面试", "合成结果"];
  const flow = div(`applyProcessWrap${expanded ? "" : " hide"}`, [div("mtd-steps", states.map((state, i) =>
    div(`mtd-steps-item mtd-steps-item-${state}${i === 0 ? " mtd-steps-active" : ""}`, [
      div("mtd-steps-item-content", [div("mtd-steps-item-title", [], titles[i]), div("mtd-steps-item-description", [], `合成说明${i + 1}`)])
    ])))]);
  if (!expanded) flow.forbidRead = true;
  return div("com_campusApplyRecord", [div("projectNameWrap", [span("projectName", name), span("zp-status-on", "进行中")]),
    volunteer(), volunteer("志愿二", "合成研究岗位乙"), div("foldWrap", [], "志愿状态说明"), flow]);
}
export function documentFixture(options = {}) {
  const record = project(options);
  const hiddenHistory = div("hide", [div("recordFoldWrap", [], "不得读取的合成历史")]);
  hiddenHistory.forbidRead = true; hiddenHistory.children[0].forbidRead = true;
  const pager = node("ul", "mtd-pagination-pager", ["上一页", "下一页"].map(title =>
    node("li", "mtd-pagination-item mtd-pagination-item-disabled", [], title, {title})));
  const container = div("delivery_record", [record, hiddenHistory, pager]);
  const privateArea = div("accountProfile", [], "不得读取的合成私人资料"); privateArea.forbidRead = true;
  return {document: node("document", "", [node("main", "mtd-main", [container]), privateArea]), container, record, pager, privateArea};
}
export function inDocument(doc, callback) {
  const oldDoc = Object.getOwnPropertyDescriptor(globalThis, "document");
  const oldStyle = Object.getOwnPropertyDescriptor(globalThis, "getComputedStyle");
  try {
    Object.defineProperty(globalThis, "document", {configurable: true, value: doc});
    Object.defineProperty(globalThis, "getComputedStyle", {configurable: true, value: element => element.style});
    return callback();
  } finally {
    if (oldDoc) Object.defineProperty(globalThis, "document", oldDoc); else delete globalThis.document;
    if (oldStyle) Object.defineProperty(globalThis, "getComputedStyle", oldStyle); else delete globalThis.getComputedStyle;
  }
}
