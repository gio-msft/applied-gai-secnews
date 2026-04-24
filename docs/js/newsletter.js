/* ==========================================================================
   Newsletter Archive Overlay
   Lazy-loads data/newsletters.json on first open; two-pane layout with
   week sidebar + rendered HTML content.
   ========================================================================== */
(function () {
  "use strict";

  var overlay  = document.getElementById("newsletter-overlay");
  var toggleBtn = document.getElementById("newsletter-toggle");
  var closeBtn  = document.getElementById("newsletter-close");
  var weekList  = document.getElementById("newsletter-week-list");
  var contentEl = document.getElementById("newsletter-content");

  var data = null;   // cached newsletter array
  var loaded = false;

  // ------------------------------------------------------------------
  // Open / close
  // ------------------------------------------------------------------
  function open() {
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    if (!loaded) {
      loadData();
    }
  }

  function close() {
    overlay.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
  }

  toggleBtn.addEventListener("click", function () {
    if (overlay.classList.contains("hidden")) open();
    else close();
  });

  closeBtn.addEventListener("click", close);

  overlay.addEventListener("click", function (e) {
    if (e.target === overlay) close();
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !overlay.classList.contains("hidden")) close();
  });

  // ------------------------------------------------------------------
  // Data loading & rendering
  // ------------------------------------------------------------------
  function loadData() {
    contentEl.innerHTML = '<p class="newsletter-placeholder">Loading…</p>';
    fetch("data/newsletters.json")
      .then(function (r) { return r.json(); })
      .then(function (items) {
        data = items;
        loaded = true;
        buildSidebar();
        if (data.length > 0) selectWeek(0);
      })
      .catch(function () {
        contentEl.innerHTML = '<p class="newsletter-placeholder">Failed to load newsletters.</p>';
      });
  }

  function buildSidebar() {
    weekList.innerHTML = "";
    data.forEach(function (entry, idx) {
      var li = document.createElement("li");
      li.textContent = entry.label;
      li.setAttribute("data-idx", idx);
      if (idx === 0) li.classList.add("active");
      li.addEventListener("click", function () {
        selectWeek(idx);
      });
      weekList.appendChild(li);
    });
  }

  function selectWeek(idx) {
    // Update sidebar active state
    var items = weekList.querySelectorAll("li");
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle("active", i === idx);
    }
    // Update content
    contentEl.innerHTML = data[idx].html;
    contentEl.scrollTop = 0;
    // Open all links in a new tab
    var links = contentEl.querySelectorAll("a[href]");
    for (var j = 0; j < links.length; j++) {
      links[j].setAttribute("target", "_blank");
      links[j].setAttribute("rel", "noopener noreferrer");
    }
    // Paper title links: navigate to graph node instead of opening new tab
    var paperLinks = contentEl.querySelectorAll("a.nl-paper-link");
    for (var k = 0; k < paperLinks.length; k++) {
      paperLinks[k].removeAttribute("target");
      paperLinks[k].removeAttribute("rel");
      paperLinks[k].addEventListener("click", handlePaperClick);
    }
  }

  function handlePaperClick(e) {
    e.preventDefault();
    var paperId = this.getAttribute("data-paper-id");
    if (paperId && window.selectPaper) {
      close();
      window.selectPaper(paperId);
    }
  }
})();
