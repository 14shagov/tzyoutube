/* auth.js — login/register forms submit via tzApi (same-origin, cookie
 * session); API errors are shown in the .auth-error box as plain text. */
(function () {
  "use strict";
  function ready(fn) {
    if (document.readyState !== "loading") { fn(); }
    else { document.addEventListener("DOMContentLoaded", fn); }
  }
  ready(function () {
    var form = document.getElementById("login-form") || document.getElementById("register-form");
    if (!form) { return; }
    var errBox = document.getElementById("auth-error");
    var isRegister = form.id === "register-form";
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (errBox) { errBox.hidden = true; }
      var data = {
        email: form.elements.email.value.trim(),
        password: form.elements.password.value,
      };
      if (isRegister) { data.channel_name = form.elements.channel_name.value.trim(); }
      var next = form.elements.next ? form.elements.next.value : "";
      if (next && next.indexOf("/") !== 0) { next = ""; }  // open-redirect guard
      window.tzApi(isRegister ? "/api/auth/register" : "/api/auth/login", {
        method: "POST",
        body: data,
      }).then(function () {
        window.location.href = next || "/";
      }).catch(function (err) {
        if (errBox) { errBox.textContent = err.message; errBox.hidden = false; }
      });
    });
  });
})();
