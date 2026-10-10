export function fakeTab({
  url = "https://jobs.bytedance.com/campus/login",
  snapshot = '- textbox "手机号码"\n- textbox "验证码"\n- button "获取验证码"\n- button "登录"\n- checkbox [checked]',
  controls = [], onSnapshot, onCount, onAction
} = {}) {
  const model = { url, snapshot, controls, calls: [], snapshots: 0 };
  const locator = predicate => {
    const matches = () => model.controls.filter(predicate);
    return {
      count: async () => { if (onCount) await onCount(model); return matches().length; },
      isVisible: async () => matches()[0]?.visible !== false,
      isEnabled: async () => matches()[0]?.enabled !== false,
      fill: async value => { model.calls.push({ method: "fill", id: matches()[0]?.id }); await onAction?.(model, matches()[0], value); },
      click: async () => { model.calls.push({ method: "click", id: matches()[0]?.id }); await onAction?.(model, matches()[0]); },
      check: async () => { model.calls.push({ method: "check", id: matches()[0]?.id }); await onAction?.(model, matches()[0]); }
    };
  };
  return {
    model,
    url: async () => model.url,
    goto: async value => { model.calls.push({ method: "goto", url: value }); model.url = value; await onAction?.(model, { id: "navigation" }); },
    playwright: {
      domSnapshot: async () => { model.snapshots++; await onSnapshot?.(model); return model.snapshot; },
      getByRole: (role, options) => locator(c => c.role === role && (options.name === undefined ||
        (options.name instanceof RegExp ? options.name.test(c.name) : options.name === c.name))),
      getByPlaceholder: text => locator(c => c.placeholder === text),
      getByLabel: text => locator(c => c.label === text)
    }
  };
}

export const LOGIN_CONTROLS = [
  { id: "phone", role: "textbox", placeholder: "手机号码" },
  { id: "otp", role: "textbox", placeholder: "验证码" },
  { id: "request", role: "button", name: "获取验证码" },
  { id: "login", role: "button", name: "登录" },
  { id: "terms", role: "checkbox", name: "" }
];
