/* Copy-to-clipboard buttons: <button data-copy="#element-id">. */
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-copy]");
  if (!button) return;
  var source = document.querySelector(button.getAttribute("data-copy"));
  if (!source || !navigator.clipboard) return;
  navigator.clipboard.writeText(source.textContent.trim()).then(function () {
    var label = button.textContent;
    button.textContent = "Kopiert ✓";
    setTimeout(function () { button.textContent = label; }, 1500);
  });
});
