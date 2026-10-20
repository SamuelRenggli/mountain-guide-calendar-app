/* Tourchef widget loader.
 * Usage: <script src="https://<host>/static/js/embed.js" data-tourchef="<guide-slug>" async></script>
 * Optional data-lang="en" preselects English emails for guests (default: the visitor's browser language).
 * Inserts the booking widget iframe and keeps its height in sync with the content.
 */
(function () {
  var script = document.currentScript;
  if (!script) return;
  var slug = script.getAttribute("data-tourchef");
  if (!slug) return;
  var origin = new URL(script.src).origin;

  var iframe = document.createElement("iframe");
  var lang = script.getAttribute("data-lang");
  iframe.src = origin + "/w/" + encodeURIComponent(slug) + "/" + (lang ? "?lang=" + encodeURIComponent(lang) : "");
  iframe.title = "Buchungskalender";
  iframe.loading = "lazy";
  iframe.style.width = "100%";
  iframe.style.border = "0";
  iframe.style.minHeight = "520px";
  script.parentNode.insertBefore(iframe, script);

  window.addEventListener("message", function (event) {
    if (event.origin !== origin || event.source !== iframe.contentWindow) return;
    var data = event.data;
    if (data && data.type === "tourchef:height" && typeof data.height === "number") {
      iframe.style.height = Math.min(Math.max(data.height, 300), 3000) + "px";
    }
  });
})();
