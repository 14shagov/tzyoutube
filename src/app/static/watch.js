/* F3 watch page: view registration, reactions, subscribe, comments, report.
   All requests same-origin; errors shown via tzToast. No inline handlers. */
(function () {
  "use strict";
  var root = document.querySelector(".watch");
  if (!root) return;
  var videoId = root.getAttribute("data-video-id");
  var viewed = false;

  function myReaction() {
    var v = root.getAttribute("data-my-reaction");
    return v === "" ? null : Number(v);
  }

  function paintReactions(likes, dislikes, mine) {
    root.setAttribute("data-my-reaction", mine === null || mine === undefined ? "" : String(mine));
    var like = document.getElementById("like-btn");
    var dislike = document.getElementById("dislike-btn");
    if (like) like.classList.toggle("active", mine === 1);
    if (dislike) dislike.classList.toggle("active", mine === -1);
    var lc = document.getElementById("like-count");
    var dc = document.getElementById("dislike-count");
    if (lc) lc.textContent = String(likes);
    if (dc) dc.textContent = String(dislikes);
  }

  // Register a view once per page load (guest fingerprint cookie is set by API).
  var player = document.getElementById("player");
  if (player) {
    player.addEventListener("loadedmetadata", function () {
      if (viewed) return;
      viewed = true;
      window.tzApi.post("/api/videos/" + videoId + "/view", {}).catch(function () { /* silent */ });
    });
  }

  async function react(value) {
    var mine = myReaction();
    var next = mine === value ? 0 : value; // click again to undo
    try {
      var r = await window.tzApi.post("/api/videos/" + videoId + "/reactions", { value: next });
      paintReactions(r.likes, r.dislikes, r.my_reaction);
    } catch (e) {
      if (e.status === 401) window.location.href = "/login?next=/watch/" + videoId;
      else window.tzToast(e.message);
    }
  }
  var likeBtn = document.getElementById("like-btn");
  var dislikeBtn = document.getElementById("dislike-btn");
  if (likeBtn) likeBtn.addEventListener("click", function () { react(1); });
  if (dislikeBtn) dislikeBtn.addEventListener("click", function () { react(-1); });

  var subBtn = document.getElementById("subscribe-btn");
  if (subBtn) {
    subBtn.addEventListener("click", async function () {
      var subscribed = root.getAttribute("data-subscribed") === "1";
      var ownerId = document.querySelector(".channel-box .channel-name").getAttribute("href").split("/").pop();
      try {
        if (subscribed) {
          await window.tzApi.del("/api/channels/" + ownerId + "/subscribe");
          root.setAttribute("data-subscribed", "");
          subBtn.textContent = "Подписаться";
          subBtn.classList.add("primary");
          subBtn.classList.remove("ghost");
        } else {
          await window.tzApi.post("/api/channels/" + ownerId + "/subscribe", {});
          root.setAttribute("data-subscribed", "1");
          subBtn.textContent = "Вы подписаны";
          subBtn.classList.remove("primary");
          subBtn.classList.add("ghost");
        }
      } catch (e) {
        if (e.status === 401) window.location.href = "/login?next=/watch/" + videoId;
        else window.tzToast(e.message);
      }
    });
  }

  var form = document.getElementById("comment-form");
  if (form) {
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      var input = form.querySelector('input[name="text"]');
      var text = input.value.trim();
      if (!text) return;
      try {
        await window.tzApi.post("/api/videos/" + videoId + "/comments", { text: text });
        window.location.reload();
      } catch (e) {
        window.tzToast(e.message);
      }
    });
  }

  document.querySelectorAll(".reply-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var parent = btn.getAttribute("data-parent");
      var text = window.prompt("Ответ на комментарий:");
      if (!text) return;
      window.tzApi.post("/api/videos/" + videoId + "/comments", { text: text, parent_id: parent })
        .then(function () { window.location.reload(); })
        .catch(function (e) { window.tzToast(e.message); });
    });
  });

  var reportForm = document.getElementById("report-form");
  if (reportForm) {
    reportForm.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      var reason = reportForm.querySelector('select[name="reason"]').value;
      try {
        await window.tzApi.post("/api/videos/" + videoId + "/report", { reason: reason });
        window.tzToast("Жалоба отправлена");
        reportForm.reset();
      } catch (e) {
        window.tzToast(e.message);
      }
    });
  }
})();
