/* theme.js — runs in <head> before first paint: applies the saved theme or
 * prefers-color-scheme to <html data-theme>, so there is no flash.
 * External file only (CSP script-src 'self', no inline scripts). */
(function () {
  "use strict";
  var theme = "light";
  try {
    var saved = window.localStorage.getItem("theme");
    if (saved === "light" || saved === "dark") {
      theme = saved;
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      theme = "dark";
    }
  } catch (e) {
    /* localStorage unavailable — fall back to light */
  }
  document.documentElement.dataset.theme = theme;
})();
