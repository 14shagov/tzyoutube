/* F4 upload page: dropzone + file input, XHR upload with progress, then poll
   GET /api/videos/{id}/status until ready/failed. External file only (CSP). */
(function () {
  "use strict";
  var wrap = document.querySelector(".upload-wrap");
  if (!wrap) return;
  var maxSize = parseInt(wrap.getAttribute("data-max-size"), 10) || 0;
  var dropzone = document.getElementById("dropzone");
  var fileInput = document.getElementById("file-input");
  var fileInfo = document.getElementById("file-info");
  var fileName = document.getElementById("file-name");
  var fileSize = document.getElementById("file-size");
  var titleInput = document.getElementById("title-input");
  var descInput = document.getElementById("desc-input");
  var uploadBtn = document.getElementById("upload-btn");
  var progress = document.getElementById("progress");
  var errBox = document.getElementById("upload-error");
  var statusBox = document.getElementById("upload-status");
  var statusText = document.getElementById("status-text");
  var statusLink = document.getElementById("status-link");
  var file = null;

  function showError(msg) {
    errBox.textContent = msg;
    errBox.hidden = false;
  }
  function clearError() { errBox.hidden = true; errBox.textContent = ""; }
  function fmtSize(n) {
    if (n > 1048576) return (n / 1048576).toFixed(1) + " МБ";
    if (n > 1024) return (n / 1024).toFixed(1) + " КБ";
    return n + " Б";
  }
  function setFile(f) {
    clearError();
    if (!f) return;
    if (maxSize && f.size > maxSize) { showError("Файл больше лимита " + fmtSize(maxSize)); return; }
    file = f;
    fileName.textContent = f.name;
    fileSize.textContent = fmtSize(f.size);
    fileInfo.hidden = false;
    uploadBtn.disabled = !titleInput.value.trim();
  }
  dropzone.addEventListener("click", function () { fileInput.click(); });
  dropzone.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  dropzone.addEventListener("dragover", function (e) { e.preventDefault(); dropzone.classList.add("over"); });
  dropzone.addEventListener("dragleave", function () { dropzone.classList.remove("over"); });
  dropzone.addEventListener("drop", function (e) {
    e.preventDefault();
    dropzone.classList.remove("over");
    if (e.dataTransfer && e.dataTransfer.files.length) setFile(e.dataTransfer.files[0]);
  });
  fileInput.addEventListener("change", function () {
    if (fileInput.files.length) setFile(fileInput.files[0]);
  });
  titleInput.addEventListener("input", function () {
    uploadBtn.disabled = !(file && titleInput.value.trim());
  });

  function pollStatus(videoId) {
    statusBox.hidden = false;
    statusText.textContent = "Обрабатывается…";
    var timer = setInterval(function () {
      window.tzApi.get("/api/videos/" + videoId + "/status").then(function (s) {
        if (s.status === "ready") {
          clearInterval(timer);
          statusText.textContent = "Готово!";
          statusLink.href = "/watch/" + videoId;
          statusLink.hidden = false;
        } else if (s.status === "failed") {
          clearInterval(timer);
          statusText.textContent = "Ошибка обработки: " + (s.failure_reason || "неизвестно");
        }
      }).catch(function () { /* keep polling on transient errors */ });
    }, 2000);
  }

  uploadBtn.addEventListener("click", function () {
    if (!file) return;
    var title = titleInput.value.trim();
    if (!title) { showError("Укажите название"); return; }
    clearError();
    var fd = new FormData();
    fd.append("file", file, file.name);
    fd.append("title", title);
    fd.append("description", descInput.value);
    var xhr = new XMLHttpRequest();  // XHR for upload progress events
    xhr.open("POST", "/api/videos");
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", function (e) {
      if (e.lengthComputable) {
        progress.hidden = false;
        progress.value = Math.round((e.loaded / e.total) * 100);
      }
    });
    xhr.addEventListener("load", function () {
      if (xhr.status === 202 && xhr.response && xhr.response.id) {
        progress.value = 100;
        uploadBtn.disabled = true;
        pollStatus(xhr.response.id);
      } else {
        var detail = xhr.response && xhr.response.detail;
        showError(typeof detail === "string" ? detail : "Не удалось загрузить видео");
      }
    });
    xhr.addEventListener("error", function () { showError("Сеть недоступна"); });
    xhr.send(fd);
  });
})();
