// Minimal DOM/fetch harness to run the real static/*.js under `node --test`.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

export class FakeClassList {
  constructor(initial = "") {
    this._set = new Set(initial.split(/\s+/).filter(Boolean));
  }
  add(...cs) { cs.forEach((c) => this._set.add(c)); }
  remove(...cs) { cs.forEach((c) => this._set.delete(c)); }
  toggle(c, force) {
    const want = force === undefined ? !this._set.has(c) : !!force;
    if (want) this._set.add(c); else this._set.delete(c);
    return want;
  }
  contains(c) { return this._set.has(c); }
}

function matches(el, sel) {
  const m = sel.match(/^([a-zA-Z]*)?(?:\.([\w-]+))?(?:\[([\w-]+)\])?$/);
  if (!m || (!m[1] && !m[2] && !m[3])) return false;
  const [, tag, cls, attr] = m;
  if (tag && el.tagName !== tag.toUpperCase()) return false;
  if (cls && !el.classList.contains(cls)) return false;
  if (attr && el.getAttribute(attr) === null) return false;
  return true;
}

function walk(el, fn) {
  fn(el);
  for (const c of el.children) walk(c, fn);
}

export class FakeElement {
  constructor(tag = "div", attrs = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = { ...attrs };
    this.classList = new FakeClassList(attrs.class || "");
    this.children = [];
    this.parent = null;
    this.hidden = "hidden" in attrs;
    this.textContent = "";
    this.checked = false;
    this.name = attrs.name || "";
    this.id = attrs.id || "";
    this._listeners = {};
    this._fields = attrs.fields || {};
  }
  getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; }
  setAttribute(k, v) { this.attributes[k] = String(v); }
  addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); }
  dispatch(type, event = {}) {
    event.target = event.target || this;
    event.preventDefault = event.preventDefault || (() => {});
    (this._listeners[type] || []).forEach((fn) => fn(event));
  }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  closest(sel) {
    let el = this;
    while (el) { if (matches(el, sel)) return el; el = el.parent; }
    return null;
  }
  querySelector(sel) {
    let found = null;
    walk(this, (el) => { if (!found && el !== this && matches(el, sel)) found = el; });
    return found;
  }
  querySelectorAll(sel) {
    const out = [];
    walk(this, (el) => { if (el !== this && matches(el, sel)) out.push(el); });
    return out;
  }
  get nextElementSibling() {
    if (!this.parent) return null;
    const sibs = this.parent.children;
    return sibs[sibs.indexOf(this) + 1] || null;
  }
}

export class FakeForm extends FakeElement {
  constructor(attrs = {}) { super("form", attrs); }
}
export class FakeInput extends FakeElement {
  constructor(attrs = {}) { super("input", attrs); }
}

export class FakeFormData {
  constructor(form) { this._form = form; }
  get(k) { return this._form._fields[k] ?? null; }
}

export function makeEnv() {
  const byId = {};
  const document = new FakeElement("#document");
  document._q = () => null;
  document._qa = () => [];
  document.querySelector = (sel) => document._q(sel);
  document.querySelectorAll = (sel) => document._qa(sel);
  document.getElementById = (id) => byId[id] || null;
  const reloads = [];
  const toasts = [];
  const intervals = [];
  const window = {
    location: { href: "", reload: () => { reloads.push(1); } },
    confirm: () => true,
    tzToast: (m) => { toasts.push(m); },
    tzApi: Object.assign(() => Promise.resolve({}), {
      get: () => Promise.resolve({}),
      post: () => Promise.resolve({}),
      patch: () => Promise.resolve({}),
      del: () => Promise.resolve({}),
    }),
  };
  const sandbox = {
    window,
    document,
    Element: FakeElement,
    HTMLFormElement: FakeForm,
    HTMLInputElement: FakeInput,
    FormData: FakeFormData,
    setTimeout,
    setInterval: (fn, ms) => {
      const rec = { fn, ms, cleared: false };
      intervals.push(rec);
      return rec;
    },
    clearInterval: (rec) => { if (rec) rec.cleared = true; },
    console,
  };
  const stepIntervals = () => { for (const rec of intervals) { if (!rec.cleared) rec.fn(); } };
  return { sandbox, window, document, byId, reloads, toasts, intervals, stepIntervals };
}

export function loadScript(env, relPath) {
  const abs = path.resolve(relPath);
  const code = fs.readFileSync(abs, "utf8");
  vm.runInContext(code, vm.createContext(env.sandbox), { filename: abs });
}

export const tick = (ms = 0) => new Promise((r) => setTimeout(r, ms));
