/* Price suggestions and live invoice total. Previews only: the guide types the price, the server computes the bill.
 *  - <form data-pricing [data-days]>: days (from data-days or the form's date fields) × day rates of the form's
 *    guide/aspirant fields → [data-calc-result]; [data-calc-apply] copies it into the price field.
 *  - [data-calc] on the invoice page: the same with its own inputs, applied to every price field.
 *  - #invoice-form: tour price + expenses = total. */
(function () {
  function num(input) {
    var value = input ? parseFloat(input.value) : NaN;
    return isNaN(value) ? 0 : value;
  }

  function chf(value) {
    return value.toLocaleString("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function daysOf(form) {
    if (form.hasAttribute("data-days")) return parseFloat(form.getAttribute("data-days")) || 0;
    var start = form.querySelector('input[name$="start_date"]');
    var end = form.querySelector('input[name$="end_date"]');
    if (!start || !end || !start.value || !end.value) return 0;
    return Math.max(Math.round((new Date(end.value) - new Date(start.value)) / 86400000) + 1, 0);
  }

  function wire(form, getDays, field, targets) {
    var suggestion = 0;
    var update = function () {
      var days = getDays();
      suggestion = days * num(field("guide-rate")) * (1 + num(field("extra-guides"))) +
        days * num(field("aspirants")) * num(field("aspirant-rate"));
      var result = form.querySelector("[data-calc-result]");
      if (result) result.textContent = chf(suggestion);
    };
    form.addEventListener("input", update);
    var apply = form.querySelector("[data-calc-apply]");
    if (apply) apply.addEventListener("click", function () {
      targets().forEach(function (input) {
        input.value = suggestion.toFixed(2);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
    update();
  }

  document.querySelectorAll("form[data-pricing]").forEach(function (form) {
    wire(form, function () { return daysOf(form); },
      function (name) { return form.querySelector("[data-calc-" + name + "]"); },
      function () { return form.querySelectorAll("input[data-price]"); });
  });

  var calc = document.querySelector("[data-calc]");
  if (calc) {
    wire(calc, function () { return num(calc.querySelector("[data-calc-days]")); },
      function (name) { return calc.querySelector("[data-calc-" + name + "]"); },
      function () { return document.querySelectorAll("input[data-price]"); });
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
