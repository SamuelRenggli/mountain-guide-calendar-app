/* Runs inside the widget iframe: reports the content height to the embedding page
 * and scrolls the request form into view. */
(function () {
  function reportHeight() {
    if (window.parent === window) return;
    window.parent.postMessage({ type: "tourchef:height", height: document.documentElement.scrollHeight }, "*");
  }
  window.addEventListener("load", reportHeight);
  if ("ResizeObserver" in window) new ResizeObserver(reportHeight).observe(document.body);

  var form = document.getElementById("booking-form");
  if (form) form.scrollIntoView({ block: "nearest" });
})();
