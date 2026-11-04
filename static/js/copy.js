/* Copy-to-clipboard buttons: <button data-copy="#element-id">.
 * Share buttons: <button data-share="#element-id" data-share-title="…"> open the phone's share sheet
 * (WhatsApp, SMS, …) and fall back to copying where the browser can't share (R-111). */
(function () {
  function copy(button, text) {
    if (!navigator.clipboard) return;
    navigator.clipboard.writeText(text).then(function () {
      var label = button.textContent;
      button.textContent = "✓";
      setTimeout(function () { button.textContent = label; }, 1500);
    });
  }

  document.querySelectorAll("[data-share]").forEach(function (button) {
    if (!navigator.share) button.hidden = true;  // the copy button next to it does the job
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy], [data-share]");
    if (!button) return;
    var source = document.querySelector(button.getAttribute("data-copy") || button.getAttribute("data-share"));
    if (!source) return;
    var text = source.textContent.trim();
    if (button.hasAttribute("data-share") && navigator.share) {
      navigator.share({ title: button.getAttribute("data-share-title") || document.title, url: text }).catch(function () {});
    } else {
      copy(button, text);
    }
  });
})();
