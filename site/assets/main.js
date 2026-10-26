/* Airgent site interactions */
(function () {
  "use strict";

  /* ------------------------------------------------ copy buttons */
  function bindCopy(btn) {
    btn.addEventListener("click", function () {
      var target = btn.getAttribute("data-copy-target");
      var el = target ? document.getElementById(target) : null;
      var text = el ? el.textContent : "";
      function done() {
        btn.classList.add("copied");
        btn.textContent = "copied";
        setTimeout(function () {
          btn.classList.remove("copied");
          btn.textContent = "copy";
        }, 1400);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, done);
      } else {
        var ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); } catch (e) {}
        document.body.removeChild(ta);
        done();
      }
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll(".copy-btn"), bindCopy);

  /* ------------------------------------------------ install tabs */
  var COMMANDS = {
    uv: "curl -fsSL https://pi.dev/install.sh | sh && uv sync",
    script: "sh scripts/install.sh",
    pi: "npm install -g --ignore-scripts @earendil-works/pi-coding-agent"
  };
  var installText = document.getElementById("install-text");
  Array.prototype.forEach.call(document.querySelectorAll(".install-tabs button"), function (tab) {
    tab.addEventListener("click", function () {
      Array.prototype.forEach.call(document.querySelectorAll(".install-tabs button"), function (t) {
        t.classList.remove("active");
      });
      tab.classList.add("active");
      if (installText) installText.textContent = COMMANDS[tab.getAttribute("data-tab")] || COMMANDS.uv;
    });
  });

  /* ------------------------------------------------ ecosystem marquee */
  var track = document.getElementById("eco-track");
  if (track) track.innerHTML += track.innerHTML; // seamless loop

  /* ------------------------------------------------ docs router */
  var pages = Array.prototype.slice.call(document.querySelectorAll(".doc-page"));
  if (!pages.length) return;

  var crumbGroup = document.getElementById("crumb-group");
  var pager = document.getElementById("pager");

  function slugOf(page) { return page.getAttribute("data-page"); }

  function render(slug) {
    var index = 0;
    var found = pages.some(function (page, i) {
      if (slugOf(page) === slug) { index = i; return true; }
      return false;
    });
    if (!found) slug = null;

    pages.forEach(function (page) {
      page.classList.toggle("active", slug !== null && slugOf(page) === slug);
    });

    Array.prototype.forEach.call(document.querySelectorAll(".sidebar .links a"), function (link) {
      link.classList.toggle("active", (link.getAttribute("data-page") || "") === slug);
    });

    var active = slug === null ? pages[0] : pages[index];
    if (crumbGroup) crumbGroup.textContent = active.getAttribute("data-group") || "Docs";
    if (!slug) history.replaceState(null, "", "#/" + slugOf(active));

    if (pager) {
      var prev = pages[index - 1];
      var next = pages[index + 1];
      pager.innerHTML =
        (prev
          ? '<a href="#/' + slugOf(prev) + '"><span class="label">← Previous</span>' +
            prev.querySelector("h1").textContent + "</a>"
          : "<span></span>") +
        (next
          ? '<a class="next" href="#/' + slugOf(next) + '"><span class="label">Next →</span>' +
            next.querySelector("h1").textContent + "</a>"
          : "<span></span>");
    }
    window.scrollTo(0, 0);
  }

  function currentSlug() {
    var match = location.hash.match(/^#\/([\w-]+)/);
    return match ? match[1] : null;
  }

  window.addEventListener("hashchange", function () { render(currentSlug()); });
  render(currentSlug());
})();
