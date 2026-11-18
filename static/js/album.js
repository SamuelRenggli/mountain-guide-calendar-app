/* Tour album: upload originals straight to the photo bucket (signed links from the app), one after the other,
 * and "download all" (the browser fetches each original; it may ask once to allow several downloads). */
(function () {
  var TYPES = { heic: "image/heic", heif: "image/heif", jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp" };

  function typeOf(file) {
    if (file.type) return file.type;
    var ext = (file.name.split(".").pop() || "").toLowerCase();
    return TYPES[ext] || "";
  }

  function post(url, csrf, data) {
    return fetch(url, {
      method: "POST", credentials: "same-origin", body: JSON.stringify(data),
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }
    }).then(function (r) {
      return r.json().then(function (body) { if (!r.ok) throw new Error(body.error || r.status); return body; });
    });
  }

  var box = document.querySelector("[data-album-upload]");
  if (box) {
    var input = box.querySelector('input[type="file"]');
    var nameInput = box.querySelector("#album-name");
    var consent = box.querySelector("#album-consent");
    var status = box.querySelector("[data-album-status]");
    var button = box.querySelector("[data-album-go]");
    var csrf = box.querySelector("[name=csrfmiddlewaretoken]").value;
    var maxBytes = parseInt(box.dataset.maxBytes, 10);
    var say = function (template, done, total) {
      status.textContent = template.replace("%(done)s", done).replace("%(total)s", total);
    };

    button.addEventListener("click", function () {
      var name = nameInput ? nameInput.value.trim() : "";
      if (nameInput && !name) { status.textContent = box.dataset.textName; nameInput.focus(); return; }
      var files = Array.prototype.slice.call(input.files || []);
      if (!files.length) { input.focus(); return; }
      var errors = [];
      var done = 0;
      button.disabled = true;
      function next(i) {
        if (i >= files.length) {
          say(box.dataset.textDone, done, files.length);
          if (errors.length) status.textContent += " " + errors.join(" · ");
          if (done) setTimeout(function () { window.location.reload(); }, errors.length ? 4000 : 600);
          else button.disabled = false;
          return;
        }
        var file = files[i];
        say(box.dataset.textProgress, i + 1, files.length);
        if (file.size > maxBytes) { errors.push(box.dataset.textTooBig + " " + file.name); next(i + 1); return; }
        var type = typeOf(file);
        post(box.dataset.start, csrf, { filename: file.name, type: type, size: file.size, name: name,
                                        consent: consent ? consent.checked : false })
          .then(function (slot) {
            return fetch(slot.url, { method: "PUT", body: file, headers: { "Content-Type": type } }).then(function (r) {
              if (!r.ok) throw new Error(file.name);
              return post(box.dataset.finish, csrf, { key: slot.key });
            });
          })
          .then(function () { done += 1; })
          .catch(function (error) { errors.push(file.name + ": " + error.message); })
          .finally(function () { next(i + 1); });
      }
      next(0);
    });
  }

  var all = document.querySelector("[data-album-all]");
  if (all) {
    all.addEventListener("click", function () {
      var label = all.textContent;
      all.disabled = true;
      all.textContent = all.dataset.textBusy;
      fetch(all.dataset.albumAll, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (data) {
        if (!data.links.length) { all.disabled = false; all.textContent = label; }
        data.links.forEach(function (link, i) {
          setTimeout(function () {
            var a = document.createElement("a");
            a.href = link;
            a.rel = "noopener";
            document.body.appendChild(a);
            a.click();
            a.remove();
            if (i === data.links.length - 1) { all.disabled = false; all.textContent = label; }
          }, i * 900);
        });
      });
    });
  }
})();
