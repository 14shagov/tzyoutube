/* app.js — shared chrome behaviour: sidebar toggle, theme toggle, chips,
 * logout, toast, and the same-origin JSON helper tzApi() used by all pages.
 * API errors are surfaced to the user as plain text from the API "detail".
 * No inline handlers anywhere: elements are wired here via addEventListener. */
(function () {
  "use strict";

  // Same-origin JSON fetch; rejects with Error(plain-text API detail).
  window.tzApi = function (path, opts) {
    opts = opts || {};
    opts.credentials = "same-origin";
    if (opts.body && typeof opts.body !== "string") {
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(path, opts).then(function (r) {
      return r.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        if (!r.ok) {
          var detail = data && data.detail;
          var msg = typeof detail === "string" ? detail
            : Array.isArray(detail) ? detail.map(function (d) { return d.msg || JSON.stringify(d); }).join("; ")
            : (detail ? JSON.stringify(detail) : "Ошибка " + r.status);
          throw new Error(msg);
        }
        return data;
      });
    });
  };

  window.tzApi.post = function (path, body) {
    return window.tzApi(path, { method: "POST", body: body || {} });
  };
  window.tzApi.del = function (path) {
    return window.tzApi(path, { method: "DELETE" });
  };
  window.tzApi.patch = function (path, body) {
    return window.tzApi(path, { method: "PATCH", body: body || {} });
  };

  window.tzToast = function (msg, ok) {
    var t = document.getElementById("toast");
    if (!t) { return; }
    t.textContent = msg;
    t.className = "toast" + (ok ? " ok" : "");
    t.hidden = false;
    window.setTimeout(function () { t.hidden = true; }, 4000);
  };

  function ready(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
  }

  ready(function () {
    var menuBtn = document.getElementById("menu-btn");
    if (menuBtn) {
      menuBtn.addEventListener("click", function () {
        document.body.classList.toggle("sidebar-collapsed");
      });
    }

    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var cur = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
        var next = cur === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        try { window.localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
      });
    }

    var logout = document.getElementById("logout-btn");
    if (logout) {
      logout.addEventListener("click", function () {
        window.tzApi("/auth/logout", { method: "POST" })
          .then(function () { window.location.href = "/"; })
          .catch(function (err) { window.tzToast(err.message); });
      });
    }

    Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (chip) {
      chip.addEventListener("click", function () {
        Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (c) {
          c.classList.remove("active");
        });
        chip.classList.add("active");
      });
    });
  });
})();
