/* Runs inside the widget iframe: reports the content height to the embedding page
 * and submits the tour picker on change. */
(function () {
  function reportHeight() {
    if (window.parent === window) return;
    window.parent.postMessage({ type: "tourchef:height", height: document.documentElement.scrollHeight }, "*");
  }
  window.addEventListener("load", reportHeight);
  if ("ResizeObserver" in window) new ResizeObserver(reportHeight).observe(document.body);

  var picker = document.getElementById("tour-picker");
  if (picker) {
    picker.addEventListener("change", function () { picker.form.submit(); });
    var button = document.getElementById("tour-picker-submit");
    if (button) button.hidden = true;
  }

  var form = document.getElementById("booking-form");
  if (form) form.scrollIntoView({ block: "nearest" });
})();
