// Behavioural test for src/app/static/manage.js (task D): PATCH success must
// write «Сохранено» into the row's .save-status element and then reload; a
// failed PATCH must put the API error text into the same element.
import assert from "node:assert/strict";
import test from "node:test";
import { FakeElement, FakeForm, FakeInput, loadScript, makeEnv, tick } from "./harness.mjs";

function buildDom(env) {
  const { document } = env;
  const table = new FakeElement("table", { class: "table my-videos" });
  document._q = (sel) => (sel === ".my-videos" ? table : null);
  const tbody = table.appendChild(new FakeElement("tbody"));
  const row = tbody.appendChild(new FakeElement("tr", { "data-video-id": "vid-1" }));
  const actions = row.appendChild(new FakeElement("td", { class: "mv-actions" }));
  actions.appendChild(new FakeElement("button", { class: "btn sm edit-btn" }));
  actions.appendChild(new FakeElement("button", { class: "btn sm danger del-btn" }));
  const status = actions.appendChild(new FakeElement("span", { class: "save-status", hidden: "" }));
  const editRow = tbody.appendChild(
    new FakeElement("tr", { class: "edit-row", "data-video-id": "vid-1", hidden: "" })
  );
  const form = editRow
    .appendChild(new FakeElement("td"))
    .appendChild(new FakeForm({ class: "edit-form", fields: { title: "New title", description: "d" } }));
  const privRow = tbody.appendChild(new FakeElement("tr", { "data-video-id": "vid-2" }));
  const privTd = privRow.appendChild(new FakeElement("td"));
  const privStatus = privTd.appendChild(new FakeElement("span", { class: "save-status", hidden: "" }));
  const privCheck = privTd.appendChild(
    new FakeInput({ class: "priv", name: "is_private" })
  );
  return { table, row, status, form, privRow, privStatus, privCheck };
}

test("edit-form PATCH success shows Сохранено in .save-status then reloads", async () => {
  const env = makeEnv();
  let call = null;
  env.window.tzApi.patch = (p, body) => { call = { p, body }; return Promise.resolve({}); };
  const { table, form, status } = buildDom(env);
  loadScript(env, "src/app/static/manage.js");
  table.dispatch("submit", { target: form });
  await tick();
  assert.equal(call.p, "/api/videos/vid-1");
  assert.equal(call.body.title, "New title");
  assert.equal(status.textContent, "Сохранено");
  assert.equal(status.hidden, false);
  assert.equal(status.classList.contains("form-error"), false);
  assert.equal(env.reloads.length, 0, "no immediate reload — user must see the status");
  await tick(800);
  assert.equal(env.reloads.length, 1, "reload after the status is visible");
});

test("edit-form PATCH failure shows the API error text in .save-status, no reload", async () => {
  const env = makeEnv();
  const err = Object.assign(new Error("название занято"), { status: 409 });
  env.window.tzApi.patch = () => Promise.reject(err);
  const { table, form, status } = buildDom(env);
  loadScript(env, "src/app/static/manage.js");
  table.dispatch("submit", { target: form });
  await tick();
  assert.equal(status.textContent, "название занято");
  assert.equal(status.hidden, false);
  assert.equal(status.classList.contains("form-error"), true);
  await tick(800);
  assert.equal(env.reloads.length, 0);
});

test("is_private change PATCH success shows Сохранено in its row", async () => {
  const env = makeEnv();
  let call = null;
  env.window.tzApi.patch = (p, body) => { call = { p, body }; return Promise.resolve({}); };
  const { table, privCheck, privStatus } = buildDom(env);
  loadScript(env, "src/app/static/manage.js");
  privCheck.checked = true;
  table.dispatch("change", { target: privCheck });
  await tick();
  assert.equal(call.p, "/api/videos/vid-2");
  assert.deepEqual(call.body, { is_private: true });
  assert.equal(privStatus.textContent, "Сохранено");
});
