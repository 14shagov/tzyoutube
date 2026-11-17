// Behavioural test for src/app/static/channel.js (task D): channel PATCH
// success shows «Сохранено» in #channel-edit-status then reloads; failure
// writes the API error text into #channel-edit-error.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement, FakeForm, loadScript, makeEnv, tick } from "./harness.mjs";

function buildDom(env) {
  const { document, byId } = env;
  const head = new FakeElement("div", { class: "channel-head", "data-channel-id": "ch-1" });
  document._q = (sel) => (sel === ".channel-head" ? head : null);
  const form = new FakeForm({
    id: "channel-edit-form",
    fields: { channel_name: "New name", description: "d" },
  });
  byId["channel-edit-form"] = form;
  byId["channel-edit-btn"] = new FakeElement("button", { id: "channel-edit-btn" });
  byId["channel-edit-error"] = new FakeElement("div", { id: "channel-edit-error", hidden: "" });
  byId["channel-edit-status"] = new FakeElement("div", { id: "channel-edit-status", hidden: "" });
  return { head, form, errBox: byId["channel-edit-error"], statusBox: byId["channel-edit-status"] };
}

test("channel PATCH success shows Сохранено then reloads", async () => {
  const env = makeEnv();
  let call = null;
  env.window.tzApi.patch = (p, body) => { call = { p, body }; return Promise.resolve({}); };
  const { form, errBox, statusBox } = buildDom(env);
  loadScript(env, "src/app/static/channel.js");
  form.dispatch("submit", {});
  await tick();
  assert.equal(call.p, "/api/channels/ch-1");
  assert.equal(call.body.channel_name, "New name");
  assert.equal(statusBox.textContent, "Сохранено");
  assert.equal(statusBox.hidden, false);
  assert.equal(errBox.hidden, true);
  await tick(800);
  assert.equal(env.reloads.length, 1);
});

test("channel PATCH failure shows API error text in #channel-edit-error", async () => {
  const env = makeEnv();
  const err = Object.assign(new Error("channel name already taken"), { status: 409 });
  env.window.tzApi.patch = () => Promise.reject(err);
  const { form, errBox, statusBox } = buildDom(env);
  loadScript(env, "src/app/static/channel.js");
  form.dispatch("submit", {});
  await tick();
  assert.equal(errBox.textContent, "channel name already taken");
  assert.equal(errBox.hidden, false);
  assert.equal(statusBox.hidden, true, "no success status on failure");
  await tick(800);
  assert.equal(env.reloads.length, 0);
});
