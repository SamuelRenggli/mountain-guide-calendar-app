/* Small helpers for every page: notices top right, picture size check, autosave of drafts. */
(function () {
  "use strict";

  var MAX_PICTURE_BYTES = 8 * 1024 * 1024;

  // --- Notices top right -------------------------------------------------------------------------
  function toastArea() {
    var area = document.querySelector("[data-toast-area]");
    if (!area) {
      area = document.createElement("div");
      area.className = "toast-area";
      area.setAttribute("data-toast-area", "");
      document.body.appendChild(area);
    }
    return area;
  }

  function closeButton(toast) {
    var button = document.createElement("button");
    button.type = "button";
    button.className = "toast-close";
    button.setAttribute("aria-label", "×");
    button.textContent = "×";
    button.addEventListener("click", function () { toast.remove(); });
    return button;
  }

  function showToast(text, kind) {
    var toast = document.createElement("div");
    toast.className = "toast toast-" + (kind || "error");
    toast.setAttribute("role", "alert");
    toast.textContent = text;
    toast.appendChild(closeButton(toast));
    toastArea().appendChild(toast);
    setTimeout(function () { toast.remove(); }, 12000);
  }

  // Server-rendered notices (form errors) move into the top-right area.
  document.querySelectorAll("[data-toast]").forEach(function (toast) {
    toastArea().appendChild(toast);
    var close = toast.querySelector("[data-toast-close]");
    if (close) close.addEventListener("click", function () { toast.remove(); });
  });

  // --- Pictures: refuse files that are too big right when they are picked --------------------------
  document.querySelectorAll('input[type="file"][accept^="image"]').forEach(function (input) {
    input.addEventListener("change", function () {
      var tooBig = Array.prototype.filter.call(input.files || [], function (f) { return f.size > MAX_PICTURE_BYTES; });
      if (tooBig.length) {
        showToast((input.dataset.tooBig || "Das Bild ist zu gross (höchstens 8 MB).") + " " +
                  tooBig.map(function (f) { return f.name; }).join(", "));
        input.value = "";
      }
    });
  });

  // --- Irreversible actions (cancelling): ask once more ---------------------------------------------
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm"))) event.preventDefault();
    });
  });

  // --- Drafts: save while typing -------------------------------------------------------------------
  document.querySelectorAll("form[data-autosave]").forEach(function (form) {
    var url = form.getAttribute("data-autosave");
    var idInput = form.querySelector("[data-draft-id]");
    var status = form.querySelector("[data-autosave-status]");
    var timer = null;
    var busy = false;
    var again = false;

    function save() {
      if (busy) { again = true; return; }
      busy = true;
      var data = new FormData(form);
      data.delete("photo");
      fetch(url, {
        method: "POST", body: data, credentials: "same-origin",
        headers: {"X-CSRFToken": (form.querySelector("[name=csrfmiddlewaretoken]") || {}).value || ""}
      }).then(function (response) {
        return response.ok ? response.json() : null;
      }).then(function (result) {
        if (result && result.id) {
          idInput.value = result.id;
          if (status) status.textContent = (status.dataset.saved || "Entwurf gespeichert") + " " + result.saved;
        }
      }).catch(function () {
        if (status) status.textContent = status.dataset.failed || "Entwurf konnte nicht gespeichert werden.";
      }).finally(function () {
        busy = false;
        if (again) { again = false; save(); }
      });
    }

    function schedule(event) {
      if (event.target && event.target.type === "file") return;
      clearTimeout(timer);
      timer = setTimeout(save, 1500);
    }

    form.addEventListener("input", schedule);
    form.addEventListener("change", schedule);
    form.addEventListener("submit", function () { clearTimeout(timer); });
  });
})();
