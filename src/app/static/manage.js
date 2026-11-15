/* F4 "Мои видео" page: inline edit (PATCH), delete (DELETE + confirm),
   privacy checkbox, ACL add/remove by email for private videos. */
(function () {
  "use strict";
  var table = document.querySelector(".my-videos");
  if (!table) return;

  function rowOf(btn) { return btn.closest("tr[data-video-id]"); }

  table.addEventListener("click", function (e) {
    var t = e.target;
    if (!(t instanceof Element)) return;
    var row = rowOf(t);
    if (!row) return;
    var id = row.getAttribute("data-video-id");
    if (t.classList.contains("edit-btn")) {
      var editRow = row.nextElementSibling;
      if (editRow && editRow.classList.contains("edit-row")) editRow.hidden = !editRow.hidden;
    } else if (t.classList.contains("del-btn")) {
      if (!window.confirm("Удалить видео? Объекты будут удалены воркером.")) return;
      window.tzApi.del("/api/videos/" + id).then(function () {
        window.location.reload();
      }).catch(function (err) {
        if (err.status === 401) { window.location.href = "/login?next=/me/videos"; return; }
        window.tzToast(err.message);
      });
    } else if (t.classList.contains("acl-remove")) {
      var uid = t.getAttribute("data-user");
      window.tzApi.del("/api/videos/" + id + "/acl/" + uid).then(function () {
        window.location.reload();
      }).catch(function (err) { window.tzToast(err.message); });
    }
  });

  table.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    e.preventDefault();
    var row = rowOf(form);
    if (!row) return;
    var id = row.getAttribute("data-video-id");
    if (form.classList.contains("edit-form")) {
      var fd = new FormData(form);
      window.tzApi.patch("/api/videos/" + id, {
        title: String(fd.get("title")),
        description: String(fd.get("description")),
      }).then(function () { window.location.reload(); })
        .catch(function (err) { window.tzToast(err.message); });
    } else if (form.classList.contains("acl-form")) {
      var email = String(new FormData(form).get("email") || "");
      window.tzApi.post("/api/videos/" + id + "/acl", { email: email })
        .then(function () { window.location.reload(); })
        .catch(function (err) { window.tzToast(err.message); });
    }
  });

  table.addEventListener("change", function (e) {
    var t = e.target;
    if (!(t instanceof Element)) return;
    if (t instanceof HTMLInputElement && t.name === "is_private") {
      var row = rowOf(t);
      if (!row) return;
      window.tzApi.patch("/api/videos/" + row.getAttribute("data-video-id"), {
        is_private: t.checked,
      }).then(function () { window.location.reload(); })
        .catch(function (err) { window.tzToast(err.message); });
    }
  });
})();
