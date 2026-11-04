/* Runs inside the widget iframe: reports the content height to the embedding page, scrolls the form
 * into view, shows the day rate for the chosen group size (R-201) and the total for the chosen seats. */
(function () {
  function reportHeight() {
    if (window.parent === window) return;
    window.parent.postMessage({ type: "tourchef:height", height: document.documentElement.scrollHeight }, "*");
  }
  window.addEventListener("load", reportHeight);
  if ("ResizeObserver" in window) new ResizeObserver(reportHeight).observe(document.body);

  var form = document.getElementById("booking-form");
  if (form) form.scrollIntoView({ block: "nearest" });

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
      hint.textContent = hint.getAttribute("data-template").replace("{rate}", chf(rate));
    };
    size.addEventListener("input", updateRate);
    updateRate();
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
