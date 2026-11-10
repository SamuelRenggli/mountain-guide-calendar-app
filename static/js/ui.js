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
        if (!input.multiple) input.value = "";  // several files: the others are still uploaded
      }
    });
  });

  // --- Date pairs: the end can't be before the start, and its picker opens at the start --------------
  document.querySelectorAll("form").forEach(function (form) {
    var start = form.querySelector('input[type="date"][name$="start_date"]');
    var end = form.querySelector('input[type="date"][name$="end_date"]');
    if (!start || !end) return;
    var previous = start.value;
    function shifted(value, days) {
      var d = new Date(value + "T12:00:00");
      d.setDate(d.getDate() + days);
      return d.toISOString().slice(0, 10);
    }
    function follow() {
      if (!start.value) { end.removeAttribute("min"); return; }
      end.min = start.value;
      var next = end.value;
      if (previous && end.value && end.value >= previous) {  // keep the length of the tour
        next = shifted(end.value, Math.round((new Date(start.value) - new Date(previous)) / 86400000));
      }
      if (!next || next < start.value) next = start.value;
      previous = start.value;
      if (next !== end.value) {
        end.value = next;
        end.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
    start.addEventListener("change", follow);
    if (start.value) end.min = start.value;
  });

  // --- Two-way choice (accept / decline): one button opens its panel, the other closes ---------------
  document.querySelectorAll("[data-choice]").forEach(function (group) {
    var panels = group.querySelectorAll("details");
    panels.forEach(function (panel) {
      panel.addEventListener("toggle", function () {
        if (!panel.open) return;
        panels.forEach(function (other) { if (other !== panel) other.open = false; });
      });
    });
  });

  // --- Picture library: upload several files one after the other (small requests, progress shown) ---
  document.querySelectorAll("form[data-multi-upload]").forEach(function (form) {
    var input = form.querySelector('input[type="file"]');
    var status = form.querySelector("[data-upload-status]");
    var csrf = (form.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
    function say(template, done, total) {
      if (status) status.textContent = template.replace("%(done)s", done).replace("%(total)s", total);
    }
    form.addEventListener("submit", function (event) {
      if (!window.fetch || !input.files || !input.files.length) return;
      event.preventDefault();
      var files = Array.prototype.filter.call(input.files, function (f) { return f.size <= MAX_PICTURE_BYTES; });
      var done = 0;
      form.querySelector("button[type=submit]").disabled = true;
      function next(i) {
        if (i >= files.length) {
          say(status.dataset.textDone, done, files.length);
          window.location.replace(window.location.pathname + "?t=" + Date.now() + (done ? "#neu" : ""));
          return;
        }
        say(status.dataset.textProgress, i + 1, files.length);
        var data = new FormData();
        data.append("photos", files[i]);
        fetch(form.action || window.location.href, {
          method: "POST", body: data, credentials: "same-origin",
          headers: { "X-CSRFToken": csrf, "Accept": "application/json" }
        }).then(function (response) {
          return response.json().catch(function () { return { added: 0, errors: [files[i].name] }; });
        }).then(function (result) {
          done += result.added || 0;
          (result.errors || []).forEach(function (error) { showToast(error); });
        }).catch(function () {
          showToast(files[i].name);
        }).finally(function () { next(i + 1); });
      }
      next(0);
    });
  });

  document.querySelectorAll("[data-confirm-click]").forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (!window.confirm(button.getAttribute("data-confirm-click"))) event.preventDefault();
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
