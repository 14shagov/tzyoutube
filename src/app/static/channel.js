/* F5 channel page: subscribe toggle + owner channel edit form. */
(function () {
  "use strict";
  var head = document.querySelector(".channel-head");
  if (!head) return;
  var channelId = head.getAttribute("data-channel-id");
  var subBtn = document.getElementById("subscribe-btn");

  if (subBtn) {
    subBtn.addEventListener("click", function () {
      var subscribed = head.getAttribute("data-subscribed") === "1";
      var req = subscribed
        ? window.tzApi.del("/api/channels/" + channelId + "/subscribe")
        : window.tzApi.post("/api/channels/" + channelId + "/subscribe", {});
      req.then(function () {
        head.setAttribute("data-subscribed", subscribed ? "0" : "1");
        subBtn.textContent = subscribed ? "Подписаться" : "Вы подписаны";
        subBtn.classList.toggle("subscribed", !subscribed);
      }).catch(function (err) {
        if (err.status === 401) { window.location.href = "/login?next=/channel/" + channelId; return; }
        window.tzToast(err.message);
      });
    });
  }

  var editBtn = document.getElementById("channel-edit-btn");
  var form = document.getElementById("channel-edit-form");
  if (editBtn && form) {
    var errBox = document.getElementById("channel-edit-error");
    var cancel = document.getElementById("channel-edit-cancel");
    editBtn.addEventListener("click", function () { form.hidden = !form.hidden; });
    if (cancel) cancel.addEventListener("click", function () { form.hidden = true; });
    var statusBox = document.getElementById("channel-edit-status");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var fd = new FormData(form);
      errBox.hidden = true;
      if (statusBox) statusBox.hidden = true;
      window.tzApi.patch("/api/channels/" + channelId, {
        channel_name: String(fd.get("channel_name")),
        description: String(fd.get("description")),
      }).then(function () {
        // visible «Сохранено», then reload so the header shows the new name
        if (statusBox) { statusBox.textContent = "Сохранено"; statusBox.hidden = false; }
        setTimeout(function () { window.location.reload(); }, 700);
      }).catch(function (err) { errBox.textContent = err.message; errBox.hidden = false; });
    });
  }
})();
