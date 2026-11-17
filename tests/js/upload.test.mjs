// E: upload.js end-to-end in the fake DOM — POST 202 -> poll GET status.
// Covers: poll stops on ready (link shown), on failed (failure_reason shown),
// and on timeout (clear message, interval cleared).
import test from "node:test";
import assert from "node:assert/strict";
import { FakeElement, makeEnv, loadScript, tick } from "./harness.mjs";

const IDS = [
  "dropzone", "file-input", "file-info", "file-name", "file-size",
  "title-input", "desc-input", "upload-btn", "progress",
  "upload-error", "upload-status", "status-text", "status-link",
];

class FakeXHR {
  constructor() {
    this._l = {};
    this.upload = new FakeElement("upload");
    this.status = 202;
    this.response = { id: "vid-9" };
    this.responseType = "";
  }
  open(method, path) { this.method = method; this.path = path; }
  addEventListener(type, fn) { (this._l[type] ||= []).push(fn); }
  send() { (this._l.load || []).forEach((fn) => fn()); }
}

function buildUpload(env, statusPayload) {
  const wrap = new FakeElement("div", { class: "upload-wrap" });
  env.document._q = (sel) => (sel === ".upload-wrap" ? wrap : null);
  for (const id of IDS) {
    const tag = id.endsWith("-input") ? "input" : "div";
    env.byId[id] = new FakeElement(tag, { id });
  }
  env.byId["file-input"].files = [{ name: "v.mp4", size: 10 }];
  env.byId["title-input"].value = "My video";
  env.sandbox.XMLHttpRequest = FakeXHR;
  let getCalls = 0;
  env.window.tzApi.get = (p) => {
    getCalls += 1;
    assert.equal(p, "/api/videos/vid-9/status");
    return Promise.resolve(statusPayload);
  };
  loadScript(env, "src/app/static/upload.js");
  env.byId["file-input"].dispatch("change");
  env.byId["title-input"].dispatch("input");
  env.byId["upload-btn"].dispatch("click");
  return { getCalls: () => getCalls };
}

test("upload 202 then poll ready: stops polling, shows Готово! + watch link", async () => {
  const env = makeEnv();
  buildUpload(env, { status: "ready" });
  await tick();
  assert.equal(env.intervals.length, 1, "poll interval registered");
  env.stepIntervals();
  await tick();
  assert.equal(env.byId["status-text"].textContent, "Готово!");
  assert.equal(env.byId["status-link"].href, "/watch/vid-9");
  assert.equal(env.byId["status-link"].hidden, false);
  assert.equal(env.intervals[0].cleared, true, "interval cleared on ready");
  env.stepIntervals();
  await tick();
  assert.equal(env.intervals[0].cleared, true);
});

test("poll failed: stops and shows failure_reason from the API", async () => {
  const env = makeEnv();
  buildUpload(env, { status: "failed", failure_reason: "ffmpeg boom" });
  await tick();
  env.stepIntervals();
  await tick();
  assert.equal(env.byId["status-text"].textContent, "Ошибка обработки: ffmpeg boom");
  assert.equal(env.intervals[0].cleared, true, "interval cleared on failed");
});

test("poll timeout: stops with a clear message after the deadline", async () => {
  const env = makeEnv();
  let now = 0;
  env.sandbox.Date = { now: () => now };
  buildUpload(env, { status: "processing" });
  await tick();
  env.stepIntervals();          // t=0 poll -> still processing
  await tick();
  assert.equal(env.intervals[0].cleared, false);
  now = 301_000;                // past the 5-minute deadline
  env.stepIntervals();
  await tick();
  assert.match(env.byId["status-text"].textContent, /Превышено время ожидания/);
  assert.equal(env.intervals[0].cleared, true, "interval cleared on timeout");
});
