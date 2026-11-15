/* F5 playlist page (owner only): rename/privacy, delete playlist,
   add video by UUID, remove item. */
(function () {
  "use strict";
  var head = document.querySelector(".pl-head");
  if (!head || head.getAttribute("data-is-owner") !== "1") return;
  var plId = head.getAttribute("data-playlist-id");
  var errBox = document.getElementById("pl-error");

  function fail(err) {
    if (err.status === 401) { window.location.href = "/login?next=/playlist/" + plId; return; }
    errBox.textContent = err.message;
    errBox.hidden = false;
  }

  var editBtn = document.getElementById("pl-edit-btn");
  var form = document.getElementById("pl-edit-form");
  if (editBtn && form) {
    editBtn.addEventListener("click", function () { form.hidden = !form.hidden; });
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var fd = new FormData(form);
      window.tzApi.patch("/api/playlists/" + plId, {
        title: String(fd.get("title")),
        is_private: fd.get("is_private") === "on",
      }).then(function () { window.location.reload(); }).catch(fail);
    });
  }

  var delBtn = document.getElementById("pl-del-btn");
  if (delBtn) {
    delBtn.addEventListener("click", function () {
      if (!window.confirm("Удалить плейлист?")) return;
      window.tzApi.del("/api/playlists/" + plId)
        .then(function () { window.location.href = "/me/videos"; })
        .catch(fail);
    });
  }

  var addForm = document.getElementById("pl-add-form");
  if (addForm) {
    addForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var videoId = String(new FormData(addForm).get("video_id") || "").trim();
      errBox.hidden = true;
      window.tzApi.post("/api/playlists/" + plId + "/items", { video_id: videoId })
        .then(function () { window.location.reload(); })
        .catch(fail);
    });
  }

  document.querySelectorAll(".pl-remove").forEach(function (btn) {
    btn.addEventListener("click", function () {
      window.tzApi.del("/api/playlists/" + plId + "/items/" + btn.getAttribute("data-video"))
        .then(function () { window.location.reload(); })
        .catch(fail);
    });
  });
})();
