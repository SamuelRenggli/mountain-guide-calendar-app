/* Price calculator ([data-calc]) and live invoice total (#invoice-form).
   Both are previews only: the guide types the price, the server computes the bill. */
(function () {
  function num(input) {
    var value = input ? parseFloat(input.value) : NaN;
    return isNaN(value) ? 0 : value;
  }

  function chf(value) {
    return value.toLocaleString("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  var calc = document.querySelector("[data-calc]");
  var suggestion = 0;
  if (calc) {
    var field = function (name) { return calc.querySelector("[data-calc-" + name + "]"); };
    var updateCalc = function () {
      var days = num(field("days"));
      suggestion = days * num(field("rate")) * (1 + num(field("extra"))) + days * num(field("aspirants")) * num(field("aspirant-rate"));
      field("result").textContent = chf(suggestion);
    };
    calc.addEventListener("input", updateCalc);
    field("apply").addEventListener("click", function () {
      document.querySelectorAll("input[data-price]").forEach(function (input) {
        input.value = suggestion.toFixed(2);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
    updateCalc();
  }

  var invoice = document.getElementById("invoice-form");
  if (invoice) {
    var updateTotal = function () {
      var total = num(invoice.querySelector("input[data-price]"));
      invoice.querySelectorAll('input[name$="-amount"]').forEach(function (input) { total += num(input); });
      document.getElementById("invoice-total").textContent = chf(total);
    };
    invoice.addEventListener("input", updateTotal);
    updateTotal();
  }
})();
