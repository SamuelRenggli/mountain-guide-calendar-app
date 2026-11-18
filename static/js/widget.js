/* Runs inside the widget iframe: reports the content height to the embedding page, scrolls the form
 * into view, shows the day rate for the chosen group size (R-201) and the total for the chosen seats. */
(function () {
  function reportHeight() {
    if (window.parent === window) return;
    window.parent.postMessage({ type: "tourchef:height", height: document.documentElement.scrollHeight }, "*");
  }
  window.addEventListener("load", reportHeight);
  if ("ResizeObserver" in window) new ResizeObserver(reportHeight).observe(document.body);

  // Scroll to the form only after picking dates or when it shows errors; a tour page starts with its description.
  var form = document.getElementById("booking-form");
  if (form && (form.hasAttribute("data-scroll") || form.querySelector(".has-error, .alert.error"))) {
    form.scrollIntoView({ block: "nearest" });
  }

  function chf(value) {
    return value.toLocaleString("de-CH", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  }

  var hint = document.querySelector("[data-rate-hint]");
  var size = document.querySelector("[data-group-size]");
  if (hint && size) {
    var tiers = hint.getAttribute("data-rates").split(",").map(function (pair) {
      var parts = pair.split(":");
      return { from: parseInt(parts[0], 10), rate: parseFloat(parts[1]) };
    });
    var updateRate = function () {
      var n = parseInt(size.value, 10) || 1;
      var rate = tiers[0].rate;
      tiers.forEach(function (tier) { if (n >= tier.from) rate = tier.rate; });
      var days = parseInt(hint.getAttribute("data-days"), 10) || 0;
      var template = days > 1 ? "data-template-total" : days === 1 ? "data-template-day" : "data-template";
      hint.textContent = hint.getAttribute(template).replace("{n}", n).replace("{rate}", chf(rate))
        .replace("{days}", days).replace("{total}", chf(rate * days));
    };
    size.addEventListener("input", updateRate);
    updateRate();
  }

  // Tour gallery: arrows or a thumbnail swap the main picture in place (no new tab).
  var gallery = document.querySelector("[data-gallery]");
  if (gallery) {
    var main = gallery.querySelector("[data-gallery-main]");
    var thumbs = Array.prototype.slice.call(gallery.querySelectorAll("[data-gallery-thumb]"));
    var count = gallery.querySelector("[data-gallery-count]");
    var current = 0;
    var show = function (i) {
      if (!thumbs.length) return;
      current = (i + thumbs.length) % thumbs.length;
      var thumb = thumbs[current];
      main.src = thumb.getAttribute("data-src");
      main.alt = thumb.getAttribute("data-alt");
      thumbs.forEach(function (t, j) { t.classList.toggle("active", j === current); });
      if (count) count.textContent = (current + 1) + " / " + thumbs.length;
      thumb.scrollIntoView({ block: "nearest", inline: "nearest" });
    };
    thumbs.forEach(function (t, j) { t.addEventListener("click", function () { show(j); }); });
    var prev = gallery.querySelector("[data-gallery-prev]");
    var next = gallery.querySelector("[data-gallery-next]");
    if (prev) prev.addEventListener("click", function () { show(current - 1); });
    if (next) next.addEventListener("click", function () { show(current + 1); });
    gallery.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") { show(current - 1); e.preventDefault(); }
      if (e.key === "ArrowRight") { show(current + 1); e.preventDefault(); }
    });
    // Swipe on phones
    var startX = null;
    main.addEventListener("touchstart", function (e) { startX = e.touches[0].clientX; }, { passive: true });
    main.addEventListener("touchend", function (e) {
      if (startX === null) return;
      var dx = e.changedTouches[0].clientX - startX;
      if (Math.abs(dx) > 40) show(current + (dx < 0 ? 1 : -1));
      startX = null;
    });
  }

  var total = document.querySelector("[data-seat-total]");
  var seats = document.querySelector('input[name="seats"]');
  if (total && seats) {
    var price = parseFloat(total.getAttribute("data-price"));
    var updateTotal = function () {
      var n = parseInt(seats.value, 10) || 0;
      total.textContent = total.getAttribute("data-template").replace("{total}", chf(n * price));
    };
    seats.addEventListener("input", updateTotal);
    updateTotal();
  }
})();
