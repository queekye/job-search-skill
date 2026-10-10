// Entirely synthetic records. Only selector shapes and standard UI labels reflect observed DOM.
export function node(tag, classes = "", children = [], text = null, attributes = {}) {
  const result = {tagName: tag.toUpperCase(), children, parentElement: null, attributes,
    classList: new Set(classes.split(/\s+/).filter(Boolean)),
    style: {display: "block", visibility: "visible", opacity: "1", overflow: "visible"}, rect: {width: 100, height: 20},
    get innerText() {if (this.forbidRead) throw new Error("unrelated synthetic private content read"); return text ?? this.children.map(c => c.innerText).join("\n");},
    getClientRects() {return this.style.display === "none" ? [] : [this.rect];}, getBoundingClientRect() {return this.rect;},
    getAttribute(name) {return this.attributes[name] ?? null;},
    querySelectorAll(selector) {
      const descendants = n => n.children.flatMap(c => [c, ...descendants(c)]);
      const matches = (candidate, token) => {
        const attr = /\[([^\]*=]+)(\*=|=)?(?:"([^"]*)")?\]/.exec(token);
        if (attr) {
          const value = attr[1] === "class" ? [...candidate.classList].join(" ") : candidate.getAttribute(attr[1]);
          if (value === null || (attr[2] === "=" && value !== attr[3]) || (attr[2] === "*=" && !value?.includes(attr[3]))) return false;
        }
        const [tagName, ...cs] = token.replace(/\[[^\]]+\]/g, "").split(".");
        return (!tagName || candidate.tagName === tagName.toUpperCase()) && cs.every(c => candidate.classList.has(c));
      };
      let parents = [this], direct = false;
      for (const token of selector.split(/\s+/)) {
        if (token === ":scope") continue;
        if (token === ">") {direct = true; continue;}
        parents = parents.flatMap(parent => (direct ? parent.children : descendants(parent)).filter(c => matches(c, token)));
        direct = false;
      }
      return parents;
    }
  };
  result.classList.contains = c => result.classList.has(c);
  for (const child of children) child.parentElement = result;
  return result;
}
export const div = (classes, children = [], text = null) => node("div", classes, children, text);
const marker = name => `${name}--synthetic`;
export function record({number = "900000000001", job = "800000000001", title = "合成研究职位甲", collapsed = false} = {}) {
  const position = div(marker("ApplicationTable--completedPosition"), [node("a", "", [], title, {href: `/campus/position/${job}`})]);
  const row = div(`next-row ${marker("Body--tableBodyItem")}`, [
    div(marker("Body--arrowIconWrapper")),
    div(`next-col ${marker("Body--tableText")}`, [position]),
    div(`next-col ${marker("Body--tableText")}`, [], "合成集团-合成研究部"),
    div(`next-col ${marker("Body--tableText")}`, [], "2030-01-02"),
    div(`next-col ${marker("Body--tableText")}`, [], number)
  ]);
  const names = ["简历投递", "评估", "面试", "Offer", "入职", "成功入职"];
  const flow = div(`next-row ${marker("StepProgress--completedStepProgress")}`, names.map((title, i) =>
    div(`next-col ${marker("StepProgress--stepProgressItem")}`, [
      div(`${marker("StepProgress--stepProgressItemBar")} ${marker(`StepProgress--${i === 0 ? "success" : i === 1 ? "error" : "wait"}`)}`),
      div(marker("StepProgress--stepProgressItemText"), [], title)
    ])));
  const wrapper = div(`${marker("Body--stepProgressWrapper")} ${marker(`Body--${collapsed ? "collapsed" : "expanded"}`)}`, [flow]);
  if (collapsed) {wrapper.rect.height = 0; wrapper.style.overflow = "hidden"; flow.forbidRead = true;}
  return {row, wrapper, flow};
}
export function fixture(options = {}) {
  const header = div("next-row", ["职位", "所属部门", "申请日期", "申请编号"].map(t => div(`next-col ${marker("Header--tableText")}`, [], t)));
  const first = record(options), second = record({...options, number: "900000000002", job: "800000000002", title: "合成研究职位乙"});
  const main = div(marker("ApplicationTable--applicationTableMain"), [header, div(marker("Divider--divider")), first.row, first.wrapper, second.row, second.wrapper]);
  const completed = div(marker("CompletedRecord--completedRecordWrapper"), [div(marker("ApplicationTable--applicationTable"), [main])]);
  const title = text => div(marker("ApplicaitonTitle--applicationTitleWrapper"), [div(marker("ApplicaitonTitle--applicationTitle"), [], text)]);
  const ongoing = div(marker("InProgressRecord--inProgressRecordWrapper"), [], "不得读取的合成进行中数据"); ongoing.forbidRead = true;
  const section = div(marker("ApplicationProgress--pcApplicationProgressScrollContainer"), [title("进行中的流程"), ongoing, title("已完成的流程"), completed]);
  const greeting = node("span", marker("TopNav--userName"), [], "你好！ 👋 5555***6666");
  const privateArea = div("unrelatedProfile", [], "secret@example.invalid"); privateArea.forbidRead = true;
  return {document: node("document", "", [div(marker("TopNav--loginTrigger"), [greeting]), section, privateArea]),
    section, completed, main, header, first, second, greeting, privateArea};
}
export function inDocument(doc, fn) {
  const oldDoc = Object.getOwnPropertyDescriptor(globalThis, "document"), oldStyle = Object.getOwnPropertyDescriptor(globalThis, "getComputedStyle");
  try {
    Object.defineProperty(globalThis, "document", {configurable: true, value: doc});
    Object.defineProperty(globalThis, "getComputedStyle", {configurable: true, value: e => e.style});
    return fn();
  } finally {
    if (oldDoc) Object.defineProperty(globalThis, "document", oldDoc); else delete globalThis.document;
    if (oldStyle) Object.defineProperty(globalThis, "getComputedStyle", oldStyle); else delete globalThis.getComputedStyle;
  }
}
