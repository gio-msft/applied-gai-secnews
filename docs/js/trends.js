/* ==========================================================================
   Trends overlay — weekly topic volume over time
   Depends on: d3 v7 (CDN), window.__graphData, window.filterByCluster
   ========================================================================== */
(function () {
  "use strict";

  // --- Config --------------------------------------------------------------
  var WEEK_MS = 7 * 24 * 3600 * 1000;
  var MIN_CLUSTER_TOTAL = 5;        // drop clusters with fewer papers than this
  var MAX_STREAMS = 20;             // cap top-N clusters; rest roll into "Other"
  var RISING_WINDOW_WEEKS = 4;
  var TOP_N_LISTS = 5;
  var TOP_N_KEYWORDS = 10;          // keyword lines shown
  var KEYWORD_MIN_LEN = 3;
  var OTHER_COLOR = { light: "#b0b5c0", dark: "#4a4f60" };
  var TAG_KEYS = ["security", "cyber", "general"];
  // Tableau10-ish palette, readable on both themes
  var KEYWORD_PALETTE = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  ];
  // Standard English stopwords + research-paper fluff
  var STOPWORDS = (function () {
    var list = (
      "a an and are as at be but by for from has have he her his i if in into is it its of on or " +
      "our so than that the their them then there these they this those to too us was we were " +
      "what when where which while who whom why will with within without would you your yours " +
      // paper fluff
      "paper llm study studies novel method methods methodology approach approaches using used use " +
      "via vs versus towards toward analysis analyses evaluation evaluating evaluate framework " +
      "frameworks based learn learning learned deep neural network networks model models modeling " +
      "language languages large small new improve improved improving improvement improvements " +
      "data dataset datasets benchmark benchmarks benchmarking system systems systematic results " +
      "result experiments experimental experiment case cases survey review overview introduction " +
      "towards toward efficient effective robust scalable general generic generative generation " +
      "task tasks performance performances high higher low lower fast faster slow paper's " +
      "we our their its ours s t can may might could should would will would've ve re ll d m" +
      "llm llms ai artificial "
    ).split(/\s+/);
    var set = {};
    list.forEach(function (w) { if (w) set[w] = true; });
    return set;
  })();

  // --- DOM refs ------------------------------------------------------------
  var overlay = document.getElementById("trends-overlay");
  var toggleBtn = document.getElementById("trends-toggle");
  var closeBtn = document.getElementById("trends-close");
  var streamEl = document.getElementById("trends-streamgraph");
  var keywordsEl = document.getElementById("trends-keywords");
  var keywordsLegendEl = document.getElementById("trends-kw-legend");
  var tagShareEl = document.getElementById("trends-tagshare");
  var risingEl = document.getElementById("trends-rising");
  var coolingEl = document.getElementById("trends-cooling");
  var captionEl = document.getElementById("trends-caption");
  var tooltip = document.getElementById("trends-tooltip");

  if (!overlay || !toggleBtn) return;

  // --- State ---------------------------------------------------------------
  var rendered = false;
  var resizeObserver = null;
  var resizeTimer = null;
  var currentSeries = null;   // cached aggregation (bins, clusterSeries, tagSeries)

  // --- Helpers -------------------------------------------------------------
  function theme() {
    return (window.__currentTheme && window.__currentTheme()) ||
           document.documentElement.getAttribute("data-theme") || "dark";
  }

  function pickColor(color) {
    if (!color) return "#888";
    return color[theme()] || color.dark || color.light || "#888";
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function parseDate(s) {
    if (!s) return null;
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  // Floor a date to the start of its ISO week (Monday 00:00 UTC)
  function weekStart(d) {
    var t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    var day = t.getUTCDay(); // 0=Sun..6=Sat
    var diff = (day === 0 ? -6 : 1 - day);
    t.setUTCDate(t.getUTCDate() + diff);
    return t;
  }

  // --- Aggregation (pure) --------------------------------------------------
  function computeWeeklyBins(nodes) {
    var dates = [];
    nodes.forEach(function (n) {
      var d = parseDate(n.published);
      if (d) dates.push(d);
    });
    if (!dates.length) return [];
    var min = weekStart(new Date(Math.min.apply(null, dates)));
    var max = weekStart(new Date(Math.max.apply(null, dates)));
    var bins = [];
    for (var t = min.getTime(); t <= max.getTime(); t += WEEK_MS) {
      bins.push(new Date(t));
    }
    return bins;
  }

  function aggregateByClusterWeek(nodes, bins, topicRegions) {
    if (!bins.length) return [];
    var binIndex = {};
    bins.forEach(function (b, i) { binIndex[b.getTime()] = i; });

    var clusterMeta = {};
    (topicRegions || []).forEach(function (r) {
      clusterMeta[r.id] = { label: r.label, color: r.color };
    });

    // Build per-cluster count arrays
    var perCluster = {};
    nodes.forEach(function (n) {
      if (n.cluster == null || n.cluster === -1) return;
      var d = parseDate(n.published);
      if (!d) return;
      var key = weekStart(d).getTime();
      var idx = binIndex[key];
      if (idx == null) return;
      if (!perCluster[n.cluster]) {
        perCluster[n.cluster] = new Array(bins.length).fill(0);
      }
      perCluster[n.cluster][idx] += 1;
    });

    // Materialize + compute totals
    var series = Object.keys(perCluster).map(function (cid) {
      var counts = perCluster[cid];
      var total = counts.reduce(function (a, b) { return a + b; }, 0);
      var meta = clusterMeta[cid] || {
        label: "Topic " + cid,
        color: { light: "#888", dark: "#888" },
      };
      return {
        clusterId: isNaN(+cid) ? cid : +cid,
        label: meta.label,
        color: meta.color,
        counts: counts,
        total: total,
      };
    }).filter(function (s) { return s.total >= MIN_CLUSTER_TOTAL; });

    series.sort(function (a, b) { return b.total - a.total; });

    // Cap at MAX_STREAMS, fold the rest into a muted "Other" stream
    if (series.length > MAX_STREAMS) {
      var kept = series.slice(0, MAX_STREAMS);
      var tail = series.slice(MAX_STREAMS);
      var otherCounts = new Array(bins.length).fill(0);
      var otherTotal = 0;
      tail.forEach(function (s) {
        for (var i = 0; i < bins.length; i++) otherCounts[i] += s.counts[i];
        otherTotal += s.total;
      });
      kept.push({
        clusterId: "__other__",
        label: "Other (" + tail.length + " topics)",
        color: OTHER_COLOR,
        counts: otherCounts,
        total: otherTotal,
      });
      series = kept;
    }
    return series;
  }

  function aggregateByTagWeek(nodes, bins) {
    var binIndex = {};
    bins.forEach(function (b, i) { binIndex[b.getTime()] = i; });
    var out = {};
    TAG_KEYS.forEach(function (t) { out[t] = new Array(bins.length).fill(0); });
    nodes.forEach(function (n) {
      var tag = TAG_KEYS.indexOf(n.tag) >= 0 ? n.tag : "general";
      var d = parseDate(n.published);
      if (!d) return;
      var idx = binIndex[weekStart(d).getTime()];
      if (idx == null) return;
      out[tag][idx] += 1;
    });
    return out;
  }

  // --- Keyword aggregation (title tokens) ---------------------------------
  function tokenize(title) {
    if (!title) return [];
    return String(title)
      .toLowerCase()
      // Replace non-letters/digits with spaces, keep hyphens as splitters too
      .replace(/[^a-z0-9]+/g, " ")
      .split(/\s+/)
      .filter(function (t) {
        if (!t) return false;
        if (t.length < KEYWORD_MIN_LEN) return false;
        if (/^\d+$/.test(t)) return false;
        if (STOPWORDS[t]) return false;
        return true;
      });
  }

  function extractBigrams(tokens) {
    var out = [];
    for (var i = 0; i < tokens.length - 1; i++) {
      var a = tokens[i], b = tokens[i + 1];
      if (!a || !b) continue;
      out.push(a + " " + b);
    }
    return out;
  }

  function aggregateKeywordsByWeek(nodes, bins, opts) {
    opts = opts || {};
    var topN = opts.topN || TOP_N_KEYWORDS;
    var includeBigrams = opts.includeBigrams !== false;
    if (!bins.length) return [];
    var binIndex = {};
    bins.forEach(function (b, i) { binIndex[b.getTime()] = i; });

    var termTotals = {};
    var termWeekly = {};
    nodes.forEach(function (n) {
      var d = parseDate(n.published);
      if (!d) return;
      var idx = binIndex[weekStart(d).getTime()];
      if (idx == null) return;
      var toks = tokenize(n.title);
      var unique = {};
      // Unigrams: count each term at most once per paper to avoid duplicates
      toks.forEach(function (t) { unique[t] = true; });
      if (includeBigrams) {
        extractBigrams(toks).forEach(function (b) { unique[b] = true; });
      }
      Object.keys(unique).forEach(function (t) {
        if (!termTotals[t]) {
          termTotals[t] = 0;
          termWeekly[t] = new Array(bins.length).fill(0);
        }
        termTotals[t] += 1;
        termWeekly[t][idx] += 1;
      });
    });

    // Pick top-N by total, with bigrams preferred over subsumed unigrams when ties
    var terms = Object.keys(termTotals)
      .filter(function (t) { return termTotals[t] >= 2; })
      .sort(function (a, b) { return termTotals[b] - termTotals[a]; });

    // Suppress unigrams fully covered by a higher-ranking bigram
    var kept = [];
    var coveredUnigrams = {};
    terms.forEach(function (t) {
      if (kept.length >= topN) return;
      var parts = t.split(" ");
      if (parts.length === 2) {
        kept.push(t);
        parts.forEach(function (p) { coveredUnigrams[p] = true; });
      } else {
        if (coveredUnigrams[t]) return;
        kept.push(t);
      }
    });

    return kept.map(function (t, i) {
      return {
        term: t,
        total: termTotals[t],
        counts: termWeekly[t],
        color: KEYWORD_PALETTE[i % KEYWORD_PALETTE.length],
      };
    });
  }

  function computeRisingCooling(clusterSeries, windowWeeks) {
    var W = windowWeeks || RISING_WINDOW_WEEKS;
    var ranked = clusterSeries
      .filter(function (s) { return s.clusterId !== "__other__"; })
      .map(function (s) {
        var n = s.counts.length;
        var recent = 0, prior = 0;
        for (var i = Math.max(0, n - W); i < n; i++) recent += s.counts[i];
        for (var j = Math.max(0, n - 2 * W); j < n - W; j++) prior += s.counts[j];
        return {
          clusterId: s.clusterId,
          label: s.label,
          color: s.color,
          recent: recent,
          prior: prior,
          delta: recent - prior,
        };
      });

    var rising = ranked
      .filter(function (r) { return r.recent >= 2 && r.delta > 0; })
      .sort(function (a, b) { return b.delta - a.delta; })
      .slice(0, TOP_N_LISTS);

    var cooling = ranked
      .filter(function (r) { return r.prior >= 2 && r.delta < 0; })
      .sort(function (a, b) { return a.delta - b.delta; })
      .slice(0, TOP_N_LISTS);

    return { rising: rising, cooling: cooling };
  }

  // --- Rendering -----------------------------------------------------------
  function renderStreamgraph(container, clusterSeries, bins) {
    container.innerHTML = "";
    if (!clusterSeries.length || !bins.length) {
      container.innerHTML = '<div class="trends-empty">Not enough data to render streamgraph.</div>';
      return;
    }

    var rect = container.getBoundingClientRect();
    var width = Math.max(320, rect.width);
    var height = Math.max(280, rect.height || 340);
    var margin = { top: 10, right: 10, bottom: 28, left: 10 };

    var svg = d3.select(container).append("svg")
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("preserveAspectRatio", "none");

    // Build stack data: each key = clusterId, values per bin
    var keys = clusterSeries.map(function (s) { return String(s.clusterId); });
    var seriesById = {};
    clusterSeries.forEach(function (s) { seriesById[String(s.clusterId)] = s; });

    var tableData = bins.map(function (b, i) {
      var row = { __date: b };
      clusterSeries.forEach(function (s) { row[String(s.clusterId)] = s.counts[i]; });
      return row;
    });

    var stack = d3.stack()
      .keys(keys)
      .offset(d3.stackOffsetWiggle)
      .order(d3.stackOrderInsideOut);
    var stackedData = stack(tableData);

    var x = d3.scaleTime()
      .domain(d3.extent(bins))
      .range([margin.left, width - margin.right]);

    var yExtent = [
      d3.min(stackedData, function (layer) { return d3.min(layer, function (p) { return p[0]; }); }),
      d3.max(stackedData, function (layer) { return d3.max(layer, function (p) { return p[1]; }); }),
    ];
    var y = d3.scaleLinear()
      .domain(yExtent)
      .range([height - margin.bottom, margin.top]);

    var area = d3.area()
      .x(function (d) { return x(d.data.__date); })
      .y0(function (d) { return y(d[0]); })
      .y1(function (d) { return y(d[1]); })
      .curve(d3.curveBasis);

    var g = svg.append("g");
    g.selectAll("path")
      .data(stackedData)
      .join("path")
      .attr("class", "trends-stream-path")
      .attr("d", area)
      .attr("fill", function (d) { return pickColor(seriesById[d.key].color); })
      .attr("data-cluster", function (d) { return d.key; })
      .on("mousemove", function (event, d) {
        var bisect = d3.bisector(function (b) { return b; }).left;
        var mx = d3.pointer(event, svg.node())[0];
        var dt = x.invert(mx);
        var i = Math.min(bins.length - 1, Math.max(0, bisect(bins, dt) - 1));
        var s = seriesById[d.key];
        showTooltip(event,
          '<strong>' + escapeHtml(s.label) + '</strong><br/>' +
          '<span class="trends-tooltip-meta">Week of ' + formatDate(bins[i]) + '</span><br/>' +
          s.counts[i] + ' paper' + (s.counts[i] === 1 ? '' : 's')
        );
        container.classList.add("has-hover");
        g.selectAll(".trends-stream-path").classed("is-hover", function (dd) { return dd.key === d.key; });
      })
      .on("mouseleave", function () {
        hideTooltip();
        container.classList.remove("has-hover");
        g.selectAll(".trends-stream-path").classed("is-hover", false);
      })
      .on("click", function (event, d) {
        var s = seriesById[d.key];
        if (s.clusterId === "__other__") return;
        activateClusterFilter(s.clusterId);
      });

    // X axis
    svg.append("g")
      .attr("class", "trends-axis")
      .attr("transform", "translate(0," + (height - margin.bottom) + ")")
      .call(d3.axisBottom(x).ticks(Math.min(10, Math.max(3, Math.floor(width / 110)))).tickSizeOuter(0));
  }

  function renderRisingCoolingList(container, items, kind) {
    container.innerHTML = "";
    if (!items.length) {
      container.innerHTML = '<li class="trends-empty" style="padding:8px">No ' + kind + ' topics detected.</li>';
      return;
    }
    var maxAbsDelta = d3.max(items, function (d) { return Math.abs(d.delta); }) || 1;
    items.forEach(function (it) {
      var li = document.createElement("li");
      li.className = "trends-list-item";
      li.setAttribute("role", "button");
      li.setAttribute("tabindex", "0");
      li.dataset.cluster = String(it.clusterId);
      var color = pickColor(it.color);
      li.innerHTML =
        '<span class="trends-list-dot" style="background:' + color + '"></span>' +
        '<span class="trends-list-label">' + escapeHtml(it.label) +
          '<span class="trends-list-sub">' + (it.delta > 0 ? "+" : "") + it.delta +
          ' (' + it.prior + '→' + it.recent + ')</span>' +
        '</span>' +
        '<span class="trends-list-bar">' +
          '<span class="trends-list-bar-fill ' + (kind === "rising" ? "rise" : "cool") + '" ' +
          'style="width:' + Math.round((Math.abs(it.delta) / maxAbsDelta) * 100) + '%"></span>' +
        '</span>';
      li.addEventListener("click", function () { activateClusterFilter(it.clusterId); });
      li.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          activateClusterFilter(it.clusterId);
        }
      });
      container.appendChild(li);
    });
  }

  function renderKeywordLines(container, legendContainer, keywordSeries, bins) {
    container.innerHTML = "";
    if (legendContainer) legendContainer.innerHTML = "";
    if (!keywordSeries.length || !bins.length) {
      container.innerHTML = '<div class="trends-empty">Not enough titles to extract keyword trends.</div>';
      return;
    }

    var rect = container.getBoundingClientRect();
    var width = Math.max(320, rect.width);
    var height = Math.max(260, rect.height || 300);
    var margin = { top: 8, right: 10, bottom: 28, left: 28 };

    var svg = d3.select(container).append("svg")
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("preserveAspectRatio", "none");

    var x = d3.scaleTime()
      .domain(d3.extent(bins))
      .range([margin.left, width - margin.right]);

    var yMax = d3.max(keywordSeries, function (s) { return d3.max(s.counts); }) || 1;
    var y = d3.scaleLinear()
      .domain([0, yMax * 1.05])
      .range([height - margin.bottom, margin.top]);

    var line = d3.line()
      .x(function (_, i) { return x(bins[i]); })
      .y(function (d) { return y(d); })
      .curve(d3.curveMonotoneX);

    // Axes
    svg.append("g")
      .attr("class", "trends-axis")
      .attr("transform", "translate(0," + (height - margin.bottom) + ")")
      .call(d3.axisBottom(x).ticks(Math.min(8, Math.max(3, Math.floor(width / 120)))).tickSizeOuter(0));

    svg.append("g")
      .attr("class", "trends-axis")
      .attr("transform", "translate(" + margin.left + ",0)")
      .call(d3.axisLeft(y).ticks(4).tickSizeOuter(0));

    var hiddenTerms = {};
    var linesG = svg.append("g");

    function applyVisibility() {
      linesG.selectAll(".trends-kw-line")
        .classed("is-dim", function (d) { return !!hiddenTerms[d.term]; });
      if (legendContainer) {
        legendContainer.querySelectorAll(".trends-kw-chip").forEach(function (chip) {
          chip.classList.toggle("is-dim", !!hiddenTerms[chip.dataset.term]);
        });
      }
    }

    linesG.selectAll("path")
      .data(keywordSeries, function (d) { return d.term; })
      .join("path")
      .attr("class", "trends-kw-line")
      .attr("stroke", function (d) { return d.color; })
      .attr("d", function (d) { return line(d.counts); })
      .attr("data-term", function (d) { return d.term; })
      .on("mousemove", function (event, d) {
        var mx = d3.pointer(event, svg.node())[0];
        var dt = x.invert(mx);
        var i = d3.bisectLeft(bins, dt);
        if (i >= bins.length) i = bins.length - 1;
        if (i > 0 && (dt - bins[i - 1] < bins[i] - dt)) i = i - 1;
        showTooltip(event,
          '<strong>' + escapeHtml(d.term) + '</strong><br/>' +
          '<span class="trends-tooltip-meta">Week of ' + formatDate(bins[i]) + '</span><br/>' +
          d.counts[i] + ' title' + (d.counts[i] === 1 ? '' : 's')
        );
        linesG.selectAll(".trends-kw-line").classed("is-hover", function (dd) { return dd.term === d.term; });
      })
      .on("mouseleave", function () {
        hideTooltip();
        linesG.selectAll(".trends-kw-line").classed("is-hover", false);
      })
      .on("click", function (event, d) {
        activateKeywordSearch(d.term);
      });

    // Legend
    if (legendContainer) {
      keywordSeries.forEach(function (s) {
        var chip = document.createElement("span");
        chip.className = "trends-kw-chip";
        chip.dataset.term = s.term;
        chip.innerHTML =
          '<span class="trends-kw-swatch" style="background:' + s.color + '"></span>' +
          escapeHtml(s.term) +
          ' <span style="color:var(--text-muted);font-size:10px">(' + s.total + ')</span>';
        // Click: search. Shift+click or right-click: toggle visibility.
        chip.addEventListener("click", function (e) {
          if (e.shiftKey || e.metaKey || e.ctrlKey) {
            hiddenTerms[s.term] = !hiddenTerms[s.term];
            applyVisibility();
            return;
          }
          activateKeywordSearch(s.term);
        });
        chip.addEventListener("contextmenu", function (e) {
          e.preventDefault();
          hiddenTerms[s.term] = !hiddenTerms[s.term];
          applyVisibility();
        });
        chip.title = "Click to search · Shift-click to toggle line";
        legendContainer.appendChild(chip);
      });
    }
  }

  function activateKeywordSearch(term) {
    if (typeof window.setSearch === "function") {
      window.setSearch(term);
    } else {
      var input = document.getElementById("search-input");
      if (input) {
        input.value = term;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }
    close();
  }

  function renderTagShare(container, tagSeries, bins) {
    container.innerHTML = "";
    if (!bins.length) {
      container.innerHTML = '<div class="trends-empty">No data.</div>';
      return;
    }
    var rect = container.getBoundingClientRect();
    var width = Math.max(320, rect.width);
    var height = 56;
    var margin = { top: 4, right: 10, bottom: 20, left: 10 };

    var svg = d3.select(container).append("svg")
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("preserveAspectRatio", "none");

    var stackData = bins.map(function (b, i) {
      var row = { __date: b };
      TAG_KEYS.forEach(function (t) { row[t] = tagSeries[t][i]; });
      return row;
    });

    var stack = d3.stack().keys(TAG_KEYS).offset(d3.stackOffsetExpand); // normalized share
    var stacked = stack(stackData);

    var x = d3.scaleTime().domain(d3.extent(bins)).range([margin.left, width - margin.right]);
    var y = d3.scaleLinear().domain([0, 1]).range([height - margin.bottom, margin.top]);

    var area = d3.area()
      .x(function (d) { return x(d.data.__date); })
      .y0(function (d) { return y(d[0]); })
      .y1(function (d) { return y(d[1]); })
      .curve(d3.curveStepAfter);

    var tagColor = {
      security: cssVar("--color-security") || "#4f6df5",
      cyber: cssVar("--color-cyber") || "#e67e22",
      general: cssVar("--color-general") || "#95a5a6",
    };

    svg.append("g").selectAll("path")
      .data(stacked)
      .join("path")
      .attr("d", area)
      .attr("fill", function (d) { return tagColor[d.key]; })
      .attr("opacity", 0.85);

    svg.append("g")
      .attr("class", "trends-axis")
      .attr("transform", "translate(0," + (height - margin.bottom) + ")")
      .call(d3.axisBottom(x).ticks(Math.min(8, Math.max(3, Math.floor(width / 130)))).tickSizeOuter(0));

    // Invisible hover rect per bin for tooltip
    var bw = (width - margin.left - margin.right) / bins.length;
    svg.append("g").selectAll("rect")
      .data(bins)
      .join("rect")
      .attr("x", function (d, i) { return margin.left + i * bw; })
      .attr("y", margin.top)
      .attr("width", bw)
      .attr("height", height - margin.top - margin.bottom)
      .attr("fill", "transparent")
      .on("mousemove", function (event, d) {
        var i = bins.indexOf(d);
        var total = TAG_KEYS.reduce(function (a, t) { return a + tagSeries[t][i]; }, 0);
        var html = '<strong>Week of ' + formatDate(d) + '</strong><br/>';
        TAG_KEYS.forEach(function (t) {
          var v = tagSeries[t][i];
          var pct = total ? Math.round((v / total) * 100) : 0;
          html += '<span class="trends-tooltip-meta">' + t + ': ' + v + ' (' + pct + '%)</span><br/>';
        });
        showTooltip(event, html);
      })
      .on("mouseleave", hideTooltip);
  }

  // --- Cluster filter bridge ----------------------------------------------
  function activateClusterFilter(clusterId) {
    if (typeof window.filterByCluster === "function") {
      // If already filtered on this cluster, filterByCluster will toggle it off;
      // force-apply by clearing first when ids don't match.
      window.filterByCluster(clusterId);
    }
    close();
  }

  // --- Tooltip -------------------------------------------------------------
  function showTooltip(event, html) {
    if (!tooltip) return;
    tooltip.innerHTML = html;
    tooltip.classList.remove("hidden");
    var x = (event.clientX || 0) + 12;
    var y = (event.clientY || 0) + 12;
    tooltip.style.left = x + "px";
    tooltip.style.top = y + "px";
  }
  function hideTooltip() {
    if (tooltip) tooltip.classList.add("hidden");
  }

  // --- Utils ---------------------------------------------------------------
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var monthFmt = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function formatDate(d) {
    return monthFmt[d.getUTCMonth()] + " " + d.getUTCDate() + ", " + d.getUTCFullYear();
  }

  // --- Orchestration -------------------------------------------------------
  function buildSeries() {
    var data = window.__graphData;
    if (!data || !Array.isArray(data.nodes)) return null;
    var bins = computeWeeklyBins(data.nodes);
    var clusterSeries = aggregateByClusterWeek(data.nodes, bins, data.topic_regions);
    var tagSeries = aggregateByTagWeek(data.nodes, bins);
    var keywordSeries = aggregateKeywordsByWeek(data.nodes, bins, { topN: TOP_N_KEYWORDS });
    var rc = computeRisingCooling(clusterSeries, RISING_WINDOW_WEEKS);
    return {
      bins: bins,
      clusterSeries: clusterSeries,
      tagSeries: tagSeries,
      keywordSeries: keywordSeries,
      rising: rc.rising,
      cooling: rc.cooling,
      totalPapers: data.nodes.length,
    };
  }

  function renderAll() {
    currentSeries = buildSeries();
    if (!currentSeries || !currentSeries.bins.length) {
      streamEl.innerHTML = '<div class="trends-empty">No dated papers available.</div>';
      if (keywordsEl) keywordsEl.innerHTML = "";
      if (keywordsLegendEl) keywordsLegendEl.innerHTML = "";
      risingEl.innerHTML = "";
      coolingEl.innerHTML = "";
      tagShareEl.innerHTML = "";
      captionEl.textContent = "";
      return;
    }
    renderStreamgraph(streamEl, currentSeries.clusterSeries, currentSeries.bins);
    if (keywordsEl) {
      renderKeywordLines(keywordsEl, keywordsLegendEl, currentSeries.keywordSeries, currentSeries.bins);
    }
    renderRisingCoolingList(risingEl, currentSeries.rising, "rising");
    renderRisingCoolingList(coolingEl, currentSeries.cooling, "cooling");
    renderTagShare(tagShareEl, currentSeries.tagSeries, currentSeries.bins);

    var first = currentSeries.bins[0];
    var last = currentSeries.bins[currentSeries.bins.length - 1];
    captionEl.textContent =
      currentSeries.totalPapers + " papers · " +
      currentSeries.bins.length + " weeks · " +
      formatDate(first) + " → " + formatDate(last);
  }

  function scheduleResize() {
    if (!rendered) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(renderAll, 150);
  }

  function open() {
    overlay.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    renderAll();
    rendered = true;
    if (!resizeObserver && typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(scheduleResize);
      resizeObserver.observe(overlay.querySelector(".trends-modal"));
    }
  }

  function close() {
    overlay.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
    hideTooltip();
  }

  // --- Wiring --------------------------------------------------------------
  toggleBtn.addEventListener("click", function () {
    if (overlay.classList.contains("hidden")) open(); else close();
  });
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", function (e) {
    if (e.target === overlay) close();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !overlay.classList.contains("hidden")) close();
  });

  // --- Public API (for tests) ----------------------------------------------
  window.Trends = {
    open: open,
    close: close,
    _computeWeeklyBins: computeWeeklyBins,
    _aggregateByClusterWeek: aggregateByClusterWeek,
    _aggregateByTagWeek: aggregateByTagWeek,
    _aggregateKeywordsByWeek: aggregateKeywordsByWeek,
    _tokenize: tokenize,
    _computeRisingCooling: computeRisingCooling,
  };
})();
