/* F6 moderation page: resolve reports (hide/unhide/dismiss + note),
   admin: save role/is_blocked per user row. External file only (CSP). */
(function () {
  "use strict";
  var errBox = document.getElementById("mod-error");

  function fail(err) {
    if (err.status === 401) { window.location.href = "/login?next=/mod"; return; }
    if (errBox) { errBox.textContent = err.message; errBox.hidden = false; }
    else window.tzToast(err.message);
  }

  var table = document.querySelector('[data-testid="reports-table"]');
  if (table) {
    table.addEventListener("click", function (e) {
      var btn = e.target;
      if (!(btn instanceof Element) || !btn.classList.contains("btn")) return;
      var action = btn.getAttribute("data-action");
      if (!action) return;
      var row = btn.closest("tr[data-report-id]");
      if (!row) return;
      var reportId = row.getAttribute("data-report-id");
      var noteInput = row.querySelector(".resolve-note");
      var payload = { action: action };
      if (noteInput && noteInput.value) payload.note = noteInput.value;
      window.tzApi.post("/api/mod/reports/" + reportId + "/resolve", payload)
        .then(function () { window.location.reload(); })
        .catch(fail);
    });
  }

  var users = document.querySelector('[data-testid="admin-users"]');
  if (users) {
    users.addEventListener("click", function (e) {
      var btn = e.target;
      if (!(btn instanceof Element) || !btn.classList.contains("user-save")) return;
      var row = btn.closest("tr[data-user-id]");
      if (!row) return;
      var userId = row.getAttribute("data-user-id");
      var roleSel = row.querySelector('select[name="role"]');
      var blocked = row.querySelector('input[name="is_blocked"]');
      var payload = {};
      if (roleSel) payload.role = roleSel.value;
      if (blocked) payload.is_blocked = blocked.checked;
      window.tzApi.patch("/api/admin/users/" + userId, payload)
        .then(function () { window.tzToast("Сохранено", "ok"); })
        .catch(fail);
    });
  }
})();
