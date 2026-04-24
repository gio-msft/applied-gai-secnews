/* ==========================================================================
   Paper Graph Visualization — app.js
   Sigma.js v3 + Graphology — vanilla JS, no build tools
   ========================================================================== */

(function () {
  "use strict";

  // --- Constants -----------------------------------------------------------
  const TAG_COLORS = {
    security: { light: "#4f6df5", dark: "#6b8aff" },
    cyber:    { light: "#e67e22", dark: "#f5a623" },
    general:  { light: "#95a5a6", dark: "#7f8c8d" },
  };

  const EDGE_COLORS = {
    citations: { light: "rgba(70,70,160,0.5)",   dark: "rgba(140,160,255,0.35)" },
    authors:   { light: "rgba(160,70,70,0.5)",    dark: "rgba(255,160,140,0.45)" },
    semantic:  { light: "rgba(30,130,65,0.55)",     dark: "rgba(100,220,140,0.4)"  },
  };

  const MIN_NODE_SIZE = 1;
  const MAX_NODE_SIZE = 6;
  const SCALE_FACTOR = 600; // spread pre-computed positions

  // --- State ---------------------------------------------------------------
  let graphData = null;
  let graph = null;
  let renderer = null;
  let activeLayer = "citations";
  let highlightedNode = null;
  var originalPositions = {};  // paper_id → {x, y}
  var semanticPositions = {};  // paper_id → {x, y}
  var animatingLayout = false;
  var clusterLookup = {};  // cluster_id → {label, color}
  var clusterNodeSets = {};  // cluster_id → Set of node IDs
  var filteredCluster = null;  // null or cluster_id
  var placedLabelPositions = {};  // region.id → {x, y, hw, hh} — last drawn positions
  let selectedNode = null;
  let draggedNode = null;
  let isDragging = false;
  var nodeClickHandled = false;  // flag to prevent container click from overriding clickNode
  var activeTags = new Set(["security", "cyber", "general"]);
  var currentView = "graph"; // "graph" | "split" | "list"
  var hoveredRegion = null;  // cluster_id when mouse is inside a topic hull
  var tableSortCol = "score";
  var tableSortDir = "desc";
  var tableFocusedRow = -1;
  var dateFilterMin = null;  // "YYYY-MM-DD" or null (no filter)
  var dateFilterMax = null;
  var allDates = [];         // sorted list of unique date strings from data

  // --- DOM refs ------------------------------------------------------------
  const container = document.getElementById("graph-container");
  const graphPane = document.getElementById("graph-pane");
  const mainContainer = document.getElementById("main-container");
  const loadingOverlay = document.getElementById("loading-overlay");
  const cardPanel = document.getElementById("card-panel");
  const searchInput = document.getElementById("search-input");
  const tableBody = document.getElementById("paper-table-body");
  const tfStart = document.getElementById("tf-start");
  const tfEnd = document.getElementById("tf-end");
  const tfRangeMin = document.getElementById("tf-range-min");
  const tfRangeMax = document.getElementById("tf-range-max");
  const tfReset = document.getElementById("tf-reset");

  // --- Theme ---------------------------------------------------------------
  function currentTheme() {
    return document.documentElement.getAttribute("data-theme") || "dark";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    if (renderer && graph) refreshEdgeColors();
    if (renderer) renderer.refresh();
  }

  document.getElementById("theme-toggle").addEventListener("click", function () {
    applyTheme(currentTheme() === "dark" ? "light" : "dark");
  });

  // --- Help modal --------------------------------------------------------
  var helpOverlay = document.getElementById("help-overlay");
  document.getElementById("help-toggle").addEventListener("click", function () {
    helpOverlay.classList.toggle("hidden");
    helpOverlay.setAttribute("aria-hidden", !helpOverlay.classList.contains("hidden"));
  });
  document.getElementById("help-close").addEventListener("click", function () {
    helpOverlay.classList.add("hidden");
    helpOverlay.setAttribute("aria-hidden", "true");
  });
  helpOverlay.addEventListener("click", function (e) {
    if (e.target === helpOverlay) {
      helpOverlay.classList.add("hidden");
      helpOverlay.setAttribute("aria-hidden", "true");
    }
  });

  // Respect system preference on first load
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
    applyTheme("light");
  }

  // --- Sync main-container top with toolbar height -------------------------
  (function () {
    var toolbar = document.getElementById("toolbar");
    function syncTop() {
      mainContainer.style.top = toolbar.offsetHeight + "px";
    }
    syncTop();
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(syncTop).observe(toolbar);
    }
  })();

  // --- Legend tag filter ----------------------------------------------------
  document.querySelectorAll("#legend .legend-item").forEach(function (item) {
    item.addEventListener("click", function () {
      var tag = item.getAttribute("data-tag");
      if (!tag) return;
      if (activeTags.has(tag)) {
        // Don't allow deselecting the last active tag
        if (activeTags.size <= 1) return;
        activeTags.delete(tag);
        item.classList.remove("active");
      } else {
        activeTags.add(tag);
        item.classList.add("active");
      }
      if (renderer) renderer.refresh();
      refreshTableVisibility();
    });
  });

  // --- Data loading --------------------------------------------------------
  fetch("data/graph.json")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      graphData = data;
      // Expose loaded graph + helpers for secondary views (e.g., Trends overlay)
      window.__graphData = data;
      window.__currentTheme = currentTheme;
      // Build cluster lookup and node sets
      (graphData.topic_regions || []).forEach(function (r) {
        clusterLookup[r.id] = { label: r.label, color: r.color };
        clusterNodeSets[r.id] = new Set(r.papers || []);
      });
      // Pre-compute dual position sets
      graphData.nodes.forEach(function (n) {
        originalPositions[n.id] = {
          x: (n.x || 0) * SCALE_FACTOR,
          y: (n.y || 0) * SCALE_FACTOR,
        };
        semanticPositions[n.id] = {
          x: (n.semantic_x != null ? n.semantic_x : n.x || 0) * SCALE_FACTOR,
          y: (n.semantic_y != null ? n.semantic_y : n.y || 0) * SCALE_FACTOR,
        };
      });
      initGraph();
      buildTable();
      initTimeframeFilter();
      loadingOverlay.style.display = "none";
    })
    .catch(function (err) {
      loadingOverlay.querySelector("p").textContent = "Failed to load graph data.";
      console.error(err);
    });

  // --- Graph init ----------------------------------------------------------
  function initGraph() {
    graph = new graphology.Graph({ type: "undirected", multi: false });

    // Add nodes
    graphData.nodes.forEach(function (n) {
      var score = n.interest_score || 5;
      var size = MIN_NODE_SIZE + ((score - 1) / 9) * (MAX_NODE_SIZE - MIN_NODE_SIZE);
      var tagColor = TAG_COLORS[n.tag] || TAG_COLORS.general;
      var theme = currentTheme();
      var nodeAlpha = (n.tag === "general") ? 0.9 : 0.65;

      graph.addNode(n.id, {
        x: (n.x || 0) * SCALE_FACTOR,
        y: (n.y || 0) * SCALE_FACTOR,
        size: size,
        color: hexToRgba(tagColor[theme], nodeAlpha),
        label: "",
        tag: n.tag,
        // Store original data for the card
        _data: n,
      });
    });

    // Add edges for the active layer
    addEdges(activeLayer);

    // Instantiate renderer
    renderer = new Sigma(graph, container, {
      renderLabels: false,
      enableEdgeEvents: false,
      defaultEdgeColor: EDGE_COLORS[activeLayer][currentTheme()],
      defaultEdgeType: "line",
      minCameraRatio: 0.02,
      maxCameraRatio: 20,
      // Suppress all canvas-rendered labels and hover boxes
      labelRenderer: function () {},
      hoverRenderer: function () {},
      defaultDrawNodeHover: function () {},
    });

    // --- Drag support with spring physics for neighbors --------------------
    var SPRING_STRENGTH = 0.1;
    var DAMPING = 0.72;
    var REST_LENGTH = 30;     // ideal spring length — no pull below this
    var REPULSION = 500;      // repulsion constant to prevent overlap
    var MIN_SEP = 8;          // minimum separation (sum of radii floor)
    var neighborVelocities = {}; // nodeId → {vx, vy}
    var physicsRAF = null;

    function runPhysicsStep() {
      if (!isDragging || !draggedNode) return;

      var dragPos = {
        x: graph.getNodeAttribute(draggedNode, "x"),
        y: graph.getNodeAttribute(draggedNode, "y"),
      };
      var dragSize = graph.getNodeAttribute(draggedNode, "size") || 3;

      var neighbors = graph.neighbors(draggedNode);
      var moved = false;

      // Collect neighbor positions for repulsion pass
      var positions = {};
      neighbors.forEach(function (nid) {
        positions[nid] = {
          x: graph.getNodeAttribute(nid, "x"),
          y: graph.getNodeAttribute(nid, "y"),
          size: graph.getNodeAttribute(nid, "size") || 3,
        };
      });

      neighbors.forEach(function (nid) {
        var p = positions[nid];
        var dx = dragPos.x - p.x;
        var dy = dragPos.y - p.y;
        var dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 0.1) dist = 0.1;

        // --- Spring: distance-normalized (constant force regardless of distance)
        var stretch = dist - REST_LENGTH;
        var fx = 0, fy = 0;
        if (stretch > 0) {
          // Pull toward dragged node, proportional to log of overshoot
          var pull = SPRING_STRENGTH * Math.log(1 + stretch);
          fx = (dx / dist) * pull;
          fy = (dy / dist) * pull;
        }

        // --- Repulsion from dragged node to prevent overlap
        var minDist = (dragSize + p.size) * 1.5;
        if (minDist < MIN_SEP) minDist = MIN_SEP;
        if (dist < minDist) {
          var repForce = REPULSION / (dist * dist);
          fx -= (dx / dist) * repForce;
          fy -= (dy / dist) * repForce;
        }

        // --- Repulsion between neighbors to prevent mutual overlap
        neighbors.forEach(function (other) {
          if (other === nid) return;
          var op = positions[other];
          var odx = op.x - p.x;
          var ody = op.y - p.y;
          var odist = Math.sqrt(odx * odx + ody * ody);
          if (odist < 0.1) odist = 0.1;
          var oMinDist = (p.size + op.size) * 1.5;
          if (oMinDist < MIN_SEP) oMinDist = MIN_SEP;
          if (odist < oMinDist) {
            var oRepForce = REPULSION / (odist * odist);
            fx -= (odx / odist) * oRepForce;
            fy -= (ody / odist) * oRepForce;
          }
        });

        if (!neighborVelocities[nid]) neighborVelocities[nid] = { vx: 0, vy: 0 };
        var vel = neighborVelocities[nid];
        vel.vx = (vel.vx + fx) * DAMPING;
        vel.vy = (vel.vy + fy) * DAMPING;

        if (Math.abs(vel.vx) > 0.01 || Math.abs(vel.vy) > 0.01) {
          graph.setNodeAttribute(nid, "x", p.x + vel.vx);
          graph.setNodeAttribute(nid, "y", p.y + vel.vy);
          moved = true;
        }
      });

      if (moved) {
        physicsRAF = requestAnimationFrame(runPhysicsStep);
      } else {
        physicsRAF = null;
      }
    }

    renderer.on("downNode", function (event) {
      isDragging = true;
      draggedNode = event.node;
      neighborVelocities = {};
      graph.setNodeAttribute(draggedNode, "highlighted", true);
      renderer.getCamera().disable();
    });

    renderer.getMouseCaptor().on("mousemovebody", function (event) {
      if (!isDragging || !draggedNode) return;
      var pos = renderer.viewportToGraph(event);
      graph.setNodeAttribute(draggedNode, "x", pos.x);
      graph.setNodeAttribute(draggedNode, "y", pos.y);

      // Kick off physics for neighbors
      if (!physicsRAF) {
        physicsRAF = requestAnimationFrame(runPhysicsStep);
      }

      event.preventSigmaDefault();
      event.original.preventDefault();
      event.original.stopPropagation();
    });

    renderer.getMouseCaptor().on("mouseup", function () {
      if (draggedNode) {
        graph.removeNodeAttribute(draggedNode, "highlighted");
      }
      isDragging = false;
      draggedNode = null;
      neighborVelocities = {};
      if (physicsRAF) {
        cancelAnimationFrame(physicsRAF);
        physicsRAF = null;
      }
      renderer.getCamera().enable();
    });

    // --- Event: click node --------------------------------------------------
    renderer.on("clickNode", function (event) {
      nodeClickHandled = true;
      var nodeId = event.node;
      selectedNode = nodeId;
      var attrs = graph.getNodeAttributes(nodeId);
      showCard(attrs._data);
      highlightNode(nodeId);
      // Sync: select table row on graph click
      tableSelectRow(nodeId);
    });

    // --- Event: click stage (deselect) --------------------------------------
    renderer.on("clickStage", function () {
      selectedNode = null;
      hideCard();
      clearHighlight();
      tableSelectRow(null);
    });

    // --- Event: hover -------------------------------------------------------
    var tooltip = document.getElementById("node-tooltip");

    renderer.on("enterNode", function (event) {
      highlightedNode = event.node;
      var data = graph.getNodeAttribute(event.node, "_data");
      tooltip.textContent = (data.emoji || "") + " " + (data.title || event.node);
      tooltip.classList.remove("hidden");
      renderer.refresh();
      // Sync: highlight table row on graph hover
      tableHoverRow(event.node);
    });

    renderer.getMouseCaptor().on("mousemovebody", function (event) {
      if (highlightedNode && !isDragging) {
        tooltip.style.left = event.original.clientX + 12 + "px";
        tooltip.style.top = event.original.clientY + 12 + "px";
      }
    });

    renderer.on("leaveNode", function () {
      highlightedNode = null;
      tooltip.classList.add("hidden");
      renderer.refresh();
      // Sync: clear table row hover
      tableHoverRow(null);
    });

    // Node reducer for hover / selection / cluster filter dimming
    renderer.setSetting("nodeReducer", function (node, data) {
      var res = Object.assign({}, data);
      var dimColor = currentTheme() === "dark" ? "#1a1a2e" : "#d0d0d8";

      // --- Tag filter: hide nodes whose tag is not active ---
      var nodeTag = (data._data && data._data.tag) || "general";
      if (!activeTags.has(nodeTag)) {
        res.hidden = true;
        return res;
      }

      // --- Date filter: hide nodes outside the selected date range ---
      if (dateFilterMin || dateFilterMax) {
        var pubDate = (data._data && data._data.published || "").slice(0, 10);
        if (dateFilterMin && pubDate < dateFilterMin) { res.hidden = true; return res; }
        if (dateFilterMax && pubDate > dateFilterMax) { res.hidden = true; return res; }
      }

      // --- Cluster filter: hide nodes not in the filtered cluster ---
      if (filteredCluster != null) {
        var inCluster = clusterNodeSets[filteredCluster] && clusterNodeSets[filteredCluster].has(node);
        if (!inCluster && node !== selectedNode) {
          res.hidden = true;
          return res;
        }
      }

      // Highlight the selected node — keep original color, overlay does the ring
      if (node === selectedNode) {
        res.zIndex = 10;
        return res;
      }

      if (highlightedNode && highlightedNode !== node) {
        if (!graph.hasEdge(highlightedNode, node) && !graph.hasEdge(node, highlightedNode)) {
          res.color = dimColor;
          res.label = "";
        }
      }
      if (selectedNode && selectedNode !== node) {
        if (!graph.hasEdge(selectedNode, node) && !graph.hasEdge(node, selectedNode)) {
          res.color = dimColor;
          res.label = "";
        }
      }

      // --- Region hover: dim nodes outside the hovered topic region ---
      if (hoveredRegion != null && !highlightedNode && !selectedNode) {
        var inHovered = clusterNodeSets[hoveredRegion] && clusterNodeSets[hoveredRegion].has(node);
        if (!inHovered) {
          res.color = dimColor;
          res.label = "";
        }
      }
      return res;
    });

    renderer.setSetting("edgeReducer", function (edge, data) {
      var res = Object.assign({}, data);

      // --- Tag filter: hide edges connected to hidden nodes ---
      var sTag = (graph.getNodeAttribute(graph.source(edge), "_data") || {}).tag || "general";
      var tTag = (graph.getNodeAttribute(graph.target(edge), "_data") || {}).tag || "general";
      if (!activeTags.has(sTag) || !activeTags.has(tTag)) {
        res.hidden = true;
        return res;
      }

      // --- Cluster filter: hide edges not connecting cluster nodes ---
      if (filteredCluster != null) {
        var cSet = clusterNodeSets[filteredCluster];
        var s = graph.source(edge);
        var t = graph.target(edge);
        if (!cSet || !cSet.has(s) || !cSet.has(t)) {
          res.hidden = true;
          return res;
        }
      }

      // In semantic mode, hide ALL edges unless a node is hovered/selected
      if (activeLayer === "semantic") {
        if (!highlightedNode && !selectedNode) {
          // Region hover: show edges within hovered region, hide the rest
          if (hoveredRegion != null) {
            var hrSet = clusterNodeSets[hoveredRegion];
            var hs = graph.source(edge);
            var ht = graph.target(edge);
            if (!hrSet || !hrSet.has(hs) || !hrSet.has(ht)) {
              res.hidden = true;
            }
            return res;
          }
          res.hidden = true;
          return res;
        }
      }
      if (highlightedNode) {
        var src = graph.source(edge);
        var tgt = graph.target(edge);
        if (src !== highlightedNode && tgt !== highlightedNode) {
          res.hidden = true;
        }
      }
      if (selectedNode) {
        var src2 = graph.source(edge);
        var tgt2 = graph.target(edge);
        if (src2 !== selectedNode && tgt2 !== selectedNode) {
          res.hidden = true;
        }
      }
      return res;
    });

    // Keep selection ring in sync with camera movement
    renderer.on("afterRender", function () {
      if (selectedNode) updateSelectionRing();
      drawHulls();
    });

    // Expose internals for e2e tests
    window._sigmaRenderer = renderer;
    window._graph = graph;
    Object.defineProperty(window, '_selectedNode', { get: function () { return selectedNode; } });
    Object.defineProperty(window, '_filteredCluster', { get: function () { return filteredCluster; } });
  }

  // --- Paper table ---------------------------------------------------------
  function buildTable() {
    if (!graphData || !tableBody) return;
    tableBody.innerHTML = "";
    graphData.nodes.forEach(function (n) {
      var tr = document.createElement("tr");
      tr.setAttribute("data-node-id", n.id);
      tr.setAttribute("data-score", n.interest_score || 0);
      tr.setAttribute("data-tag", n.tag || "general");
      tr.setAttribute("data-date", (n.published || "").slice(0, 10));
      tr.setAttribute("data-title", (n.title || "").toLowerCase());

      var tdTitle = document.createElement("td");
      tdTitle.className = "pt-cell-title";
      tdTitle.textContent = (n.emoji || "") + " " + (n.title || n.id);
      tdTitle.title = n.title || n.id;
      tr.appendChild(tdTitle);

      var tdScore = document.createElement("td");
      tdScore.className = "pt-cell-score";
      tdScore.textContent = n.interest_score || "?";
      tr.appendChild(tdScore);

      var tdTag = document.createElement("td");
      tdTag.className = "pt-cell-tag";
      tdTag.textContent = n.tag || "general";
      tdTag.style.color = "var(--badge-" + (n.tag || "general") + "-text)";
      tr.appendChild(tdTag);

      var topicLabel = "";
      var topicClusterId = (n.cluster != null && n.cluster !== -1) ? n.cluster : null;
      if (topicClusterId != null && clusterLookup[topicClusterId]) {
        topicLabel = clusterLookup[topicClusterId].label;
      }
      tr.setAttribute("data-topic", topicLabel.toLowerCase());
      tr.setAttribute("data-cluster", topicClusterId != null ? topicClusterId : "");
      var tdTopic = document.createElement("td");
      tdTopic.className = "pt-cell-topic";
      tdTopic.textContent = topicLabel || "—";
      if (topicClusterId != null && clusterLookup[topicClusterId]) {
        var theme = currentTheme();
        var tColor = theme === "dark" ? clusterLookup[topicClusterId].color.dark : clusterLookup[topicClusterId].color.light;
        tdTopic.style.color = tColor;
        tdTopic.title = "Filter: " + topicLabel;
        (function (cid) {
          tdTopic.addEventListener("click", function (e) {
            e.stopPropagation();
            filterByCluster(cid);
          });
        })(topicClusterId);
      } else {
        tdTopic.style.color = "var(--text-muted)";
      }
      tr.appendChild(tdTopic);

      var tdDate = document.createElement("td");
      tdDate.className = "pt-cell-date";
      tdDate.textContent = (n.published || "").slice(0, 10);
      tr.appendChild(tdDate);

      // Row interactions
      tr.addEventListener("click", function () {
        var nodeId = n.id;
        selectedNode = nodeId;
        if (graph && graph.hasNode(nodeId)) {
          var attrs = graph.getNodeAttributes(nodeId);
          showCard(attrs._data);
          highlightNode(nodeId);
          if (renderer && currentView !== "list") {
            var pos = renderer.getNodeDisplayData(nodeId);
            if (pos) renderer.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.3 }, { duration: 400 });
          }
        }
        tableSelectRow(nodeId);
      });

      tr.addEventListener("mouseenter", function () {
        if (graph && graph.hasNode(n.id) && renderer && currentView !== "list") {
          highlightedNode = n.id;
          renderer.refresh();
        }
        tr.classList.add("pt-hovered");
      });

      tr.addEventListener("mouseleave", function () {
        if (renderer && currentView !== "list") {
          highlightedNode = null;
          renderer.refresh();
        }
        tr.classList.remove("pt-hovered");
      });

      tableBody.appendChild(tr);
    });

    sortTable("score", "desc");
  }

  function sortTable(col, dir) {
    tableSortCol = col;
    tableSortDir = dir;
    var rows = Array.from(tableBody.querySelectorAll("tr"));
    rows.sort(function (a, b) {
      var av, bv;
      if (col === "score") {
        av = parseFloat(a.getAttribute("data-score")) || 0;
        bv = parseFloat(b.getAttribute("data-score")) || 0;
      } else if (col === "date") {
        av = a.getAttribute("data-date") || "";
        bv = b.getAttribute("data-date") || "";
      } else if (col === "tag") {
        av = a.getAttribute("data-tag") || "";
        bv = b.getAttribute("data-tag") || "";
      } else if (col === "topic") {
        av = a.getAttribute("data-topic") || "";
        bv = b.getAttribute("data-topic") || "";
      } else {
        av = a.getAttribute("data-title") || "";
        bv = b.getAttribute("data-title") || "";
      }
      var cmp;
      if (typeof av === "number" && typeof bv === "number") {
        cmp = av - bv;
      } else {
        cmp = String(av).localeCompare(String(bv));
      }
      var primary = dir === "desc" ? -cmp : cmp;
      if (primary !== 0) return primary;
      // Secondary sort: score desc then date desc (newest first)
      if (col !== "score") {
        var sa = parseFloat(a.getAttribute("data-score")) || 0;
        var sb = parseFloat(b.getAttribute("data-score")) || 0;
        if (sa !== sb) return sb - sa;
      }
      if (col !== "date") {
        var da = a.getAttribute("data-date") || "";
        var db = b.getAttribute("data-date") || "";
        if (da !== db) return da > db ? -1 : 1;
      }
      return 0;
    });
    rows.forEach(function (r) { tableBody.appendChild(r); });
    updateSortArrows();
  }

  function updateSortArrows() {
    document.querySelectorAll("#paper-table th").forEach(function (th) {
      var arrow = th.querySelector(".sort-arrow");
      if (!arrow) return;
      if (th.getAttribute("data-sort") === tableSortCol) {
        arrow.textContent = tableSortDir === "desc" ? "\u25BC" : "\u25B2";
      } else {
        arrow.textContent = "";
      }
    });
  }

  // Header click for sorting
  document.querySelectorAll("#paper-table th[data-sort]").forEach(function (th) {
    th.addEventListener("click", function () {
      var col = th.getAttribute("data-sort");
      var dir = (col === tableSortCol && tableSortDir === "desc") ? "asc" : "desc";
      sortTable(col, dir);
    });
  });

  function tableSelectRow(nodeId) {
    tableBody.querySelectorAll("tr.pt-selected").forEach(function (r) { r.classList.remove("pt-selected"); });
    if (!nodeId) return;
    var row = tableBody.querySelector('tr[data-node-id="' + nodeId + '"]');
    if (row) {
      row.classList.add("pt-selected");
      row.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }

  function tableHoverRow(nodeId) {
    tableBody.querySelectorAll("tr.pt-hovered").forEach(function (r) { r.classList.remove("pt-hovered"); });
    if (!nodeId) return;
    var row = tableBody.querySelector('tr[data-node-id="' + nodeId + '"]');
    if (row) row.classList.add("pt-hovered");
  }

  function refreshTableVisibility() {
    if (!tableBody) return;
    var query = searchInput.value.trim().toLowerCase();
    tableBody.querySelectorAll("tr").forEach(function (row) {
      var tag = row.getAttribute("data-tag");
      var nodeId = row.getAttribute("data-node-id");
      var hidden = false;

      // Tag filter
      if (!activeTags.has(tag)) hidden = true;

      // Date filter
      if (!hidden && (dateFilterMin || dateFilterMax)) {
        var rowDate = row.getAttribute("data-date") || "";
        if (dateFilterMin && rowDate < dateFilterMin) hidden = true;
        if (dateFilterMax && rowDate > dateFilterMax) hidden = true;
      }

      // Cluster filter
      if (!hidden && filteredCluster != null) {
        var inCluster = clusterNodeSets[filteredCluster] && clusterNodeSets[filteredCluster].has(nodeId);
        if (!inCluster) hidden = true;
      }

      // Search filter
      if (!hidden && query) {
        var title = row.getAttribute("data-title") || "";
        if (title.indexOf(query) === -1) hidden = true;
      }

      if (hidden) row.classList.add("pt-hidden");
      else row.classList.remove("pt-hidden");
    });
  }

  // --- View toggle ----------------------------------------------------------
  function switchView(mode) {
    if (mode === currentView) return;
    currentView = mode;
    mainContainer.className = "view-" + mode;

    // Update button states
    document.querySelectorAll(".view-btn").forEach(function (b) {
      b.classList.remove("active");
      b.setAttribute("aria-pressed", "false");
    });
    var activeBtn = document.querySelector('.view-btn[data-view="' + mode + '"]');
    if (activeBtn) {
      activeBtn.classList.add("active");
      activeBtn.setAttribute("aria-pressed", "true");
    }

    // Resize Sigma after layout transition
    setTimeout(function () {
      if (renderer && mode !== "list") {
        renderer.resize();
        renderer.refresh();
        drawHulls();
      }
    }, 350);

    // Refresh table visibility when entering split/list
    if (mode !== "graph") refreshTableVisibility();
  }

  document.querySelectorAll(".view-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      switchView(btn.getAttribute("data-view"));
    });
  });

  // --- Keyboard support for table ------------------------------------------
  document.getElementById("paper-table-container").addEventListener("keydown", function (e) {
    var visibleRows = Array.from(tableBody.querySelectorAll("tr:not(.pt-hidden)"));
    if (!visibleRows.length) return;

    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (e.key === "ArrowDown") tableFocusedRow = Math.min(tableFocusedRow + 1, visibleRows.length - 1);
      else tableFocusedRow = Math.max(tableFocusedRow - 1, 0);

      var row = visibleRows[tableFocusedRow];
      if (row) {
        tableBody.querySelectorAll("tr.pt-hovered").forEach(function (r) { r.classList.remove("pt-hovered"); });
        row.classList.add("pt-hovered");
        row.scrollIntoView({ behavior: "smooth", block: "nearest" });
        var nodeId = row.getAttribute("data-node-id");
        if (graph && graph.hasNode(nodeId) && renderer && currentView !== "list") {
          highlightedNode = nodeId;
          renderer.refresh();
        }
      }
    } else if (e.key === "Enter") {
      e.preventDefault();
      var row = visibleRows[tableFocusedRow];
      if (row) row.click();
    } else if (e.key === "Escape") {
      e.preventDefault();
      selectedNode = null;
      hideCard();
      clearHighlight();
      tableBody.querySelectorAll("tr.pt-selected, tr.pt-hovered").forEach(function (r) {
        r.classList.remove("pt-selected");
        r.classList.remove("pt-hovered");
      });
      tableFocusedRow = -1;
    }
  });

  // Make table container focusable for keyboard events
  document.getElementById("paper-table-container").setAttribute("tabindex", "0");

  // --- Edge management -----------------------------------------------------
  function addEdges(layer) {
    var edges;
    if (layer === "citations") edges = graphData.citation_edges;
    else if (layer === "authors") edges = graphData.author_edges;
    else if (layer === "semantic") edges = graphData.similarity_edges || [];
    else edges = [];
    var theme = currentTheme();
    var color = EDGE_COLORS[layer][theme];

    edges.forEach(function (e, i) {
      // Skip edges whose endpoints don't exist in the graph
      if (!graph.hasNode(e.source) || !graph.hasNode(e.target)) return;
      // Avoid duplicate edges
      if (graph.hasEdge(e.source, e.target)) return;

      var size;
      if (layer === "authors") size = Math.min((e.weight || 1) * 0.8, 5);
      else if (layer === "semantic") size = Math.min((e.weight || 0.5) * 2, 3);
      else size = 0.5;
      graph.addEdge(e.source, e.target, {
        color: color,
        size: size,
        type: "line",
      });
    });
  }

  function clearEdges() {
    graph.clearEdges();
  }

  function switchLayer(layer) {
    if (layer === activeLayer) return;
    var prevLayer = activeLayer;
    activeLayer = layer;
    clearEdges();
    addEdges(layer);
    renderer.setSetting("defaultEdgeType", "line");

    // Animate positions when switching to/from semantic mode
    var targetPositions;
    if (layer === "semantic") {
      targetPositions = semanticPositions;
    } else if (prevLayer === "semantic") {
      targetPositions = originalPositions;
    }
    if (targetPositions) {
      animatePositions(targetPositions, 600);
    } else {
      renderer.refresh();
    }

    // Hull overlay
    drawHulls();
  }

  // --- Position animation --------------------------------------------------
  function animatePositions(target, duration) {
    if (animatingLayout) return;
    animatingLayout = true;
    var start = {};
    graph.forEachNode(function (node) {
      start[node] = {
        x: graph.getNodeAttribute(node, "x"),
        y: graph.getNodeAttribute(node, "y"),
      };
    });
    var t0 = performance.now();
    function step(now) {
      var progress = Math.min((now - t0) / duration, 1);
      // ease-in-out
      var ease = progress < 0.5
        ? 2 * progress * progress
        : 1 - Math.pow(-2 * progress + 2, 2) / 2;

      graph.forEachNode(function (node) {
        var s = start[node];
        var t = target[node];
        if (!s || !t) return;
        graph.setNodeAttribute(node, "x", s.x + (t.x - s.x) * ease);
        graph.setNodeAttribute(node, "y", s.y + (t.y - s.y) * ease);
      });

      if (progress < 1) {
        requestAnimationFrame(step);
      } else {
        animatingLayout = false;
      }
    }
    requestAnimationFrame(step);
  }

  // --- Hull rendering (topic region overlays) ------------------------------
  var hullCanvas = document.getElementById("hull-canvas");
  var hullCtx = hullCanvas ? hullCanvas.getContext("2d") : null;
  var labelCanvas = document.getElementById("label-canvas");
  var labelCtx = labelCanvas ? labelCanvas.getContext("2d") : null;

  // --- Dynamic convex-hull helpers ----------------------------------------
  var HULL_PADDING = 0.04 * SCALE_FACTOR; // match build_viz.py radius
  var HULL_CIRCLE_PTS = 12; // sample points per node circle

  function _cross2d(O, A, B) {
    return (A.x - O.x) * (B.y - O.y) - (A.y - O.y) * (B.x - O.x);
  }

  /** Andrew's monotone-chain convex hull. */
  function _convexHull(points) {
    if (points.length <= 2) return points.slice();
    var sorted = points.slice().sort(function (a, b) {
      return a.x - b.x || a.y - b.y;
    });
    var lower = [];
    for (var i = 0; i < sorted.length; i++) {
      while (lower.length >= 2 &&
             _cross2d(lower[lower.length - 2], lower[lower.length - 1], sorted[i]) <= 0)
        lower.pop();
      lower.push(sorted[i]);
    }
    var upper = [];
    for (var i = sorted.length - 1; i >= 0; i--) {
      while (upper.length >= 2 &&
             _cross2d(upper[upper.length - 2], upper[upper.length - 1], sorted[i]) <= 0)
        upper.pop();
      upper.push(sorted[i]);
    }
    lower.pop();
    upper.pop();
    return lower.concat(upper);
  }

  /**
   * Compute a smooth rounded hull for a cluster from live node positions.
   * Places HULL_CIRCLE_PTS sample points on a circle of HULL_PADDING around
   * each node, then takes the convex hull — similar to Shapely's
   * Point.buffer() + unary_union().
   */
  function _computeLiveHull(paperIds) {
    var expanded = [];
    paperIds.forEach(function (id) {
      if (!graph.hasNode(id)) return;
      var x = graph.getNodeAttribute(id, "x");
      var y = graph.getNodeAttribute(id, "y");
      for (var i = 0; i < HULL_CIRCLE_PTS; i++) {
        var angle = (2 * Math.PI * i) / HULL_CIRCLE_PTS;
        expanded.push({
          x: x + Math.cos(angle) * HULL_PADDING,
          y: y + Math.sin(angle) * HULL_PADDING,
        });
      }
    });
    if (expanded.length < 3) return null;
    return _convexHull(expanded);
  }

  /** Filter paper IDs to only those currently visible (not hidden by tag/date). */
  function _visiblePapers(paperIds) {
    return paperIds.filter(function (id) {
      if (!graph.hasNode(id)) return false;
      var d = graph.getNodeAttribute(id, "_data");
      // Tag filter
      var tag = (d && d.tag) || "general";
      if (!activeTags.has(tag)) return false;
      // Date filter
      if (dateFilterMin || dateFilterMax) {
        var pub = (d && d.published || "").slice(0, 10);
        if (dateFilterMin && pub < dateFilterMin) return false;
        if (dateFilterMax && pub > dateFilterMax) return false;
      }
      return true;
    });
  }

  /** Centroid of cluster nodes from their current graph positions. */
  function _liveRegionCentroid(paperIds) {
    var cx = 0, cy = 0, n = 0;
    paperIds.forEach(function (id) {
      if (!graph.hasNode(id)) return;
      cx += graph.getNodeAttribute(id, "x");
      cy += graph.getNodeAttribute(id, "y");
      n++;
    });
    return n ? { x: cx / n, y: cy / n } : null;
  }

  function drawHulls() {
    if (!hullCtx || !renderer) return;
    // Resize canvas to match graph pane (not full viewport)
    var rect = graphPane.getBoundingClientRect();
    hullCanvas.width = rect.width * (window.devicePixelRatio || 1);
    hullCanvas.height = rect.height * (window.devicePixelRatio || 1);
    hullCanvas.style.width = rect.width + "px";
    hullCanvas.style.height = rect.height + "px";
    hullCtx.setTransform(window.devicePixelRatio || 1, 0, 0,
                         window.devicePixelRatio || 1, 0, 0);
    hullCtx.clearRect(0, 0, rect.width, rect.height);

    // Also resize the label overlay canvas
    if (labelCtx) {
      labelCanvas.width = rect.width * (window.devicePixelRatio || 1);
      labelCanvas.height = rect.height * (window.devicePixelRatio || 1);
      labelCanvas.style.width = rect.width + "px";
      labelCanvas.style.height = rect.height + "px";
      labelCtx.setTransform(window.devicePixelRatio || 1, 0, 0,
                           window.devicePixelRatio || 1, 0, 0);
      labelCtx.clearRect(0, 0, rect.width, rect.height);
    }

    if (activeLayer !== "semantic") return;

    var regions = graphData.topic_regions || [];
    var theme = currentTheme();

    regions.forEach(function (region) {
      var papers = _visiblePapers(region.papers || []);
      var hull = _computeLiveHull(papers);
      if (!hull || hull.length < 3) return;
      var color = theme === "dark" ? region.color.dark : region.color.light;

      // Transform hull points to viewport
      var viewPts = hull.map(function (p) {
        return renderer.graphToViewport(p);
      });

      // Draw smooth closed curve (quadratic bezier through edge midpoints)
      var n = viewPts.length;
      hullCtx.beginPath();
      var mx0 = (viewPts[n - 1].x + viewPts[0].x) / 2;
      var my0 = (viewPts[n - 1].y + viewPts[0].y) / 2;
      hullCtx.moveTo(mx0, my0);
      for (var i = 0; i < n; i++) {
        var next = (i + 1) % n;
        var mx = (viewPts[i].x + viewPts[next].x) / 2;
        var my = (viewPts[i].y + viewPts[next].y) / 2;
        hullCtx.quadraticCurveTo(viewPts[i].x, viewPts[i].y, mx, my);
      }
      hullCtx.closePath();
      // Dim other regions when hovering or filtering a topic hull
      var isDimmed = (hoveredRegion != null && region.id !== hoveredRegion) ||
                     (filteredCluster != null && region.id !== filteredCluster);
      hullCtx.fillStyle = hexToRgba(color, isDimmed ? 0.014 : 0.05);
      hullCtx.fill();
      hullCtx.strokeStyle = hexToRgba(color, isDimmed ? 0.058 : 0.058);
      hullCtx.lineWidth = 1.5;
      hullCtx.stroke();
    });

    // Draw topic labels on the overlay canvas (in front of nodes)
    if (labelCtx) {
      var placedLabels = []; // array of {x, y, hw, hh} for overlap detection
      placedLabelPositions = {}; // reset for this frame

      function _overlapsAny(lx, ly, hw, hh) {
        for (var p = 0; p < placedLabels.length; p++) {
          var pl = placedLabels[p];
          if (Math.abs(lx - pl.x) < hw + pl.hw + 4 &&
              Math.abs(ly - pl.y) < hh + pl.hh + 1) {
            return true;
          }
        }
        return false;
      }

      regions.forEach(function (region) {
        var visPapers = _visiblePapers(region.papers || []);
        if (visPapers.length === 0) return;
        var centroid = _liveRegionCentroid(visPapers);
        if (!centroid) return;
        var color = theme === "dark" ? region.color.dark : region.color.light;
        var cView = renderer.graphToViewport(centroid);
        var isDimmed = (hoveredRegion != null && region.id !== hoveredRegion) ||
                       (filteredCluster != null && region.id !== filteredCluster);
        labelCtx.font = "bold 11px system-ui, -apple-system, sans-serif";
        var tw = labelCtx.measureText(region.label).width;
        var hw = tw / 2 + 4; // half-width with padding
        var hh = 8;          // half-height

        // Try original position, then small offsets to resolve overlaps
        var lx = cView.x, ly = cView.y;
        var stepV = hh * 2 + 2;
        if (_overlapsAny(lx, ly, hw, hh)) {
          // Try up, down, then diagonal — max 3 rings
          var found = false;
          for (var ring = 1; ring <= 3 && !found; ring++) {
            var candidates = [
              {dx: 0, dy: -ring * stepV},
              {dx: 0, dy:  ring * stepV},
              {dx: -ring * 20, dy: -ring * stepV},
              {dx:  ring * 20, dy: -ring * stepV},
            ];
            for (var c = 0; c < candidates.length; c++) {
              var cx = cView.x + candidates[c].dx;
              var cy = cView.y + candidates[c].dy;
              if (!_overlapsAny(cx, cy, hw, hh)) {
                lx = cx; ly = cy; found = true; break;
              }
            }
          }
        }

        placedLabels.push({ x: lx, y: ly, hw: hw, hh: hh });
        placedLabelPositions[region.id] = { x: lx, y: ly, hw: hw, hh: hh };
        labelCtx.textAlign = "center";
        labelCtx.textBaseline = "middle";
        labelCtx.fillStyle = hexToRgba(color, isDimmed ? 0.15 : 0.7);
        labelCtx.fillText(region.label, lx, ly);
      });
    }
  }

  // --- Cluster filter ------------------------------------------------------
  function filterByCluster(clusterId) {
    if (filteredCluster === clusterId) {
      clearClusterFilter();
      return;
    }
    filteredCluster = clusterId;
    selectedNode = null;
    hideCard();
    clearHighlight();
    refreshTableVisibility();

    var region = clusterLookup[clusterId];
    var banner = document.getElementById("cluster-filter-banner");
    if (banner && region) {
      var theme = currentTheme();
      var cColor = theme === "dark" ? region.color.dark : region.color.light;
      document.getElementById("cluster-filter-label").textContent = region.label;
      banner.style.borderColor = cColor;
      document.getElementById("cluster-filter-label").style.color = cColor;
      banner.classList.remove("hidden");
    }
    if (renderer && currentView !== "list") renderer.refresh();
  }

  function clearClusterFilter() {
    filteredCluster = null;
    var banner = document.getElementById("cluster-filter-banner");
    if (banner) banner.classList.add("hidden");
    refreshTableVisibility();
    if (renderer && currentView !== "list") renderer.refresh();
  }

  // Expose for programmatic access (e.g., tests)
  window.filterByCluster = filterByCluster;
  window.clearClusterFilter = clearClusterFilter;

  // Banner close button
  document.getElementById("cluster-filter-clear").addEventListener("click", clearClusterFilter);

  // --- Canvas label click detection (on graph container, above hull canvas) -
  container.addEventListener("click", function (event) {
    if (nodeClickHandled) {
      nodeClickHandled = false;
      return;
    }
    if (activeLayer !== "semantic" || !renderer) return;
    var rect = container.getBoundingClientRect();
    var mx = event.clientX - rect.left;
    var my = event.clientY - rect.top;

    var regions = graphData ? (graphData.topic_regions || []) : [];
    for (var i = 0; i < regions.length; i++) {
      var region = regions[i];
      var visPapers = _visiblePapers(region.papers || []);
      if (visPapers.length === 0) continue;
      // Use the actual drawn label position if available
      var pl = placedLabelPositions[region.id];
      if (!pl) continue;
      if (Math.abs(mx - pl.x) < pl.hw && Math.abs(my - pl.y) < pl.hh) {
        filterByCluster(region.id);
        event.stopPropagation();
        return;
      }
    }
  });

  // --- Point-in-polygon (ray-casting) for hull hit-testing ---------------
  function _pointInPolygon(px, py, poly) {
    var inside = false;
    for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      var xi = poly[i].x, yi = poly[i].y;
      var xj = poly[j].x, yj = poly[j].y;
      if ((yi > py) !== (yj > py) &&
          px < (xj - xi) * (py - yi) / (yj - yi) + xi) {
        inside = !inside;
      }
    }
    return inside;
  }

  // Change cursor on hover over labels + detect region hover for dimming
  container.addEventListener("mousemove", function (event) {
    if (activeLayer !== "semantic" || !renderer) return;
    var rect = container.getBoundingClientRect();
    var mx = event.clientX - rect.left;
    var my = event.clientY - rect.top;
    var hit = false;

    var regions = graphData ? (graphData.topic_regions || []) : [];

    // Label hit-test for pointer cursor
    for (var i = 0; i < regions.length; i++) {
      var region = regions[i];
      var visPapers = _visiblePapers(region.papers || []);
      if (visPapers.length === 0) continue;
      var pl = placedLabelPositions[region.id];
      if (!pl) continue;
      if (Math.abs(mx - pl.x) < pl.hw && Math.abs(my - pl.y) < pl.hh) {
        hit = true;
        break;
      }
    }
    container.style.cursor = hit ? "pointer" : "";

    // Region hull hover — disabled when a cluster filter is active
    if (filteredCluster != null) {
      if (hoveredRegion != null) {
        hoveredRegion = null;
        if (renderer) { renderer.refresh(); drawHulls(); }
      }
      return;
    }
    var newHovered = null;
    for (var i = 0; i < regions.length; i++) {
      var region = regions[i];
      var visPapers = _visiblePapers(region.papers || []);
      if (visPapers.length === 0) continue;
      var hull = _computeLiveHull(visPapers);
      if (!hull || hull.length < 3) continue;
      var viewPts = hull.map(function (p) { return renderer.graphToViewport(p); });
      if (_pointInPolygon(mx, my, viewPts)) {
        newHovered = region.id;
        break;
      }
    }
    if (newHovered !== hoveredRegion) {
      hoveredRegion = newHovered;
      if (renderer) { renderer.refresh(); drawHulls(); }
    }
  });

  function hexToRgba(hex, alpha) {
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  function refreshEdgeColors() {
    var theme = currentTheme();
    var color = EDGE_COLORS[activeLayer][theme];
    graph.forEachEdge(function (edge) {
      graph.setEdgeAttribute(edge, "color", color);
    });
    // Also update node colors
    graph.forEachNode(function (node) {
      var data = graph.getNodeAttribute(node, "_data");
      var tagColor = TAG_COLORS[data.tag] || TAG_COLORS.general;
      var nodeAlpha = (data.tag === "general") ? 0.9 : 0.7;
      graph.setNodeAttribute(node, "color", hexToRgba(tagColor[theme], nodeAlpha));
    });
    renderer.setSetting("labelColor", { color: theme === "dark" ? "#e0e0e0" : "#333" });
  }

  // --- Layer toggle buttons ------------------------------------------------
  document.querySelectorAll(".layer-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".layer-btn").forEach(function (b) {
        b.classList.remove("active");
        b.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-pressed", "true");
      switchLayer(btn.getAttribute("data-layer"));
    });
  });

  // --- Card panel ----------------------------------------------------------
  function showCard(data) {
    document.getElementById("card-title").textContent = (data.emoji || "") + " " + (data.title || data.id);

    document.getElementById("card-score").textContent = (data.interest_score || "?") + "/10";
    var tagEl = document.getElementById("card-tag");
    tagEl.textContent = data.tag || "general";
    tagEl.className = "badge tag-badge tag-" + (data.tag || "general");

    document.getElementById("card-date").textContent = (data.published || "").slice(0, 10);

    // Cluster (semantic topic) badge
    var clusterEl = document.getElementById("card-cluster");
    var region = clusterLookup[data.cluster];
    if (region) {
      var cColor = currentTheme() === "dark" ? region.color.dark : region.color.light;
      clusterEl.textContent = region.label;
      clusterEl.style.background = hexToRgba(cColor, 0.15);
      clusterEl.style.color = cColor;
      clusterEl.style.display = "inline-block";
      clusterEl.style.cursor = "pointer";
      clusterEl.onclick = function () { filterByCluster(data.cluster); };
    } else {
      clusterEl.style.display = "none";
      clusterEl.onclick = null;
    }

    document.getElementById("card-authors").innerHTML = "<strong>Authors:</strong> " + ((data.authors || []).join(", ") || "Unknown");
    document.getElementById("card-affiliations").innerHTML = "<strong>Affiliations:</strong> " + ((data.affiliations || []).join(", ") || "—");

    var link = document.getElementById("card-link");
    if (data.url) {
      link.href = data.url;
      link.style.display = "inline-block";
    } else {
      link.style.display = "none";
    }

    document.getElementById("card-oneliner").textContent = data.one_liner || "";

    var pointsEl = document.getElementById("card-points");
    pointsEl.innerHTML = "";
    (data.points || []).forEach(function (pt) {
      var li = document.createElement("li");
      li.textContent = pt;
      pointsEl.appendChild(li);
    });

    var projEl = document.getElementById("card-projects");
    projEl.innerHTML = "";
    (data.projects || []).forEach(function (p) {
      var span = document.createElement("span");
      span.className = "badge project-badge";
      span.textContent = p;
      projEl.appendChild(span);
    });

    // --- Linked nodes sections (always both, from raw graphData) ---
    function buildLinkedSection(containerId, heading, neighborIds) {
      var el = document.getElementById(containerId);
      el.innerHTML = "";
      if (!neighborIds || neighborIds.length === 0) return;
      var h = document.createElement("h3");
      h.className = "card-linked-heading";
      h.textContent = heading + " (" + neighborIds.length + ")";
      el.appendChild(h);
      var list = document.createElement("ul");
      list.className = "card-linked-list";
      neighborIds.forEach(function (nid) {
        if (!graph.hasNode(nid)) return;
        var nData = graph.getNodeAttribute(nid, "_data");
        var li = document.createElement("li");
        var link = document.createElement("a");
        link.href = "#";
        link.className = "card-linked-item";
        link.textContent = (nData.emoji || "") + " " + (nData.title || nid);
        link.addEventListener("click", function (e) {
          e.preventDefault();
          showCard(nData);
          highlightNode(nid);
          var pos = renderer.getNodeDisplayData(nid);
          renderer.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.3 }, { duration: 400 });
        });
        li.appendChild(link);
        list.appendChild(li);
      });
      el.appendChild(list);
    }

    // Collect citation neighbors from graphData edges
    var citationNeighbors = [];
    (graphData.citation_edges || []).forEach(function (e) {
      if (e.source === data.id) citationNeighbors.push(e.target);
      if (e.target === data.id) citationNeighbors.push(e.source);
    });
    buildLinkedSection("card-linked-citations", "Cited / Cited By", citationNeighbors);

    // Collect author-overlap neighbors from graphData edges
    var authorNeighbors = [];
    (graphData.author_edges || []).forEach(function (e) {
      if (e.source === data.id) authorNeighbors.push(e.target);
      if (e.target === data.id) authorNeighbors.push(e.source);
    });
    buildLinkedSection("card-linked-authors", "Shared Authors", authorNeighbors);

    // Collect similarity neighbors from graphData edges (always shown, all modes)
    var similarNeighbors = [];
    (graphData.similarity_edges || []).forEach(function (e) {
      if (e.source === data.id) similarNeighbors.push({ id: e.target, w: e.weight });
      if (e.target === data.id) similarNeighbors.push({ id: e.source, w: e.weight });
    });
    similarNeighbors.sort(function (a, b) { return b.w - a.w; });
    similarNeighbors = similarNeighbors.slice(0, 5);

    // Build similar section with scores
    var simEl = document.getElementById("card-linked-similar");
    simEl.innerHTML = "";
    if (similarNeighbors.length > 0) {
      var h = document.createElement("h3");
      h.className = "card-linked-heading";
      h.textContent = "Similar Papers (" + similarNeighbors.length + ")";
      simEl.appendChild(h);
      var list = document.createElement("ul");
      list.className = "card-linked-list";
      similarNeighbors.forEach(function (item) {
        if (!graph.hasNode(item.id)) return;
        var nData = graph.getNodeAttribute(item.id, "_data");
        var li = document.createElement("li");
        var link = document.createElement("a");
        link.href = "#";
        link.className = "card-linked-item";
        link.textContent = (nData.emoji || "") + " " + (nData.title || item.id);
        link.addEventListener("click", function (e) {
          e.preventDefault();
          showCard(nData);
          highlightNode(item.id);
          var pos = renderer.getNodeDisplayData(item.id);
          renderer.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.3 }, { duration: 400 });
        });
        li.appendChild(link);
        var score = document.createElement("span");
        score.className = "card-similarity-score";
        score.textContent = " (similarity: " + item.w.toFixed(2) + ")";
        li.appendChild(score);
        list.appendChild(li);
      });
      simEl.appendChild(list);
    }

    cardPanel.classList.remove("hidden");
  }

  function hideCard() {
    cardPanel.classList.add("hidden");
  }

  document.getElementById("card-close").addEventListener("click", function () {
    selectedNode = null;
    hideCard();
    clearHighlight();
  });

  // --- Selection ring overlay ----------------------------------------------
  var selectionRing = document.getElementById("selection-ring");

  function updateSelectionRing() {
    if (!selectedNode || !renderer || !graph.hasNode(selectedNode)) {
      selectionRing.classList.add("hidden");
      return;
    }
    var nodeData = graph.getNodeAttributes(selectedNode);
    if (!nodeData) {
      selectionRing.classList.add("hidden");
      return;
    }
    // Convert graph coordinates to viewport pixel coordinates
    var viewportPos = renderer.graphToViewport({ x: nodeData.x, y: nodeData.y });
    var displayData = renderer.getNodeDisplayData(selectedNode);
    var nodeSize = displayData ? displayData.size : 5;
    // Scale node size by camera zoom so the ring always wraps the visible circle
    var cameraRatio = renderer.getCamera().getState().ratio;
    var screenNodeSize = nodeSize / cameraRatio;
    var ringSize = Math.max(screenNodeSize * 2 + 6, nodeSize * 2 + 6);
    var rect = graphPane.getBoundingClientRect();
    selectionRing.style.width = ringSize + "px";
    selectionRing.style.height = ringSize + "px";
    selectionRing.style.left = (rect.left + viewportPos.x - ringSize / 2) + "px";
    selectionRing.style.top = (rect.top + viewportPos.y - ringSize / 2) + "px";
    selectionRing.classList.remove("hidden");
  }

  // --- Highlight / focus ---------------------------------------------------
  function highlightNode(nodeId) {
    selectedNode = nodeId;
    if (currentView !== "list") renderer.refresh();
    updateSelectionRing();
  }

  function clearHighlight() {
    selectedNode = null;
    highlightedNode = null;
    selectionRing.classList.add("hidden");
    if (currentView !== "list") renderer.refresh();
  }

  // --- Search --------------------------------------------------------------
  searchInput.addEventListener("input", function () {
    var query = searchInput.value.trim().toLowerCase();
    if (!query) {
      clearHighlight();
      refreshTableVisibility();
      return;
    }

    // Filter table rows
    refreshTableVisibility();

    // Find first matching node
    var match = null;
    graph.forEachNode(function (node) {
      if (match) return;
      var data = graph.getNodeAttribute(node, "_data");
      if (data.title && data.title.toLowerCase().indexOf(query) !== -1) {
        match = node;
      }
    });

    if (match) {
      var attrs = graph.getNodeAttributes(match);
      showCard(attrs._data);

      // Zoom to the node
      if (renderer && currentView !== "list") {
        var pos = renderer.getNodeDisplayData(match);
        var camera = renderer.getCamera();
        camera.animate({ x: pos.x, y: pos.y, ratio: 0.3 }, { duration: 400 });
      }
      highlightNode(match);
      tableSelectRow(match);
    }
  });

  document.getElementById("search-clear").addEventListener("click", function () {
    searchInput.value = "";
    selectedNode = null;
    hideCard();
    clearHighlight();
    refreshTableVisibility();
    tableSelectRow(null);
  });

  // Expose a programmatic way to set the search query (used by the Trends overlay)
  window.setSearch = function (term) {
    searchInput.value = term == null ? "" : String(term);
    searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    try { searchInput.focus(); } catch (e) {}
  };

  // --- Timeframe filter ----------------------------------------------------
  function initTimeframeFilter() {
    // Collect all unique dates and sort them
    var dateSet = {};
    graphData.nodes.forEach(function (n) {
      var d = (n.published || "").slice(0, 10);
      if (d) dateSet[d] = true;
    });
    allDates = Object.keys(dateSet).sort();
    if (allDates.length === 0) return;

    var minDate = allDates[0];
    var maxDate = allDates[allDates.length - 1];

    // Configure date inputs
    tfStart.min = minDate;
    tfStart.max = maxDate;
    tfStart.value = minDate;
    tfEnd.min = minDate;
    tfEnd.max = maxDate;
    tfEnd.value = maxDate;

    // Configure range sliders
    tfRangeMin.min = 0;
    tfRangeMin.max = allDates.length - 1;
    tfRangeMin.value = 0;
    tfRangeMax.min = 0;
    tfRangeMax.max = allDates.length - 1;
    tfRangeMax.value = allDates.length - 1;

    // Date picker → update sliders + apply filter
    tfStart.addEventListener("change", function () {
      var idx = _closestDateIndex(tfStart.value, true);
      tfRangeMin.value = idx;
      _applyDateFilter();
    });
    tfEnd.addEventListener("change", function () {
      var idx = _closestDateIndex(tfEnd.value, false);
      tfRangeMax.value = idx;
      _applyDateFilter();
    });

    // Slider → update date pickers + apply filter
    tfRangeMin.addEventListener("input", function () {
      var mi = parseInt(tfRangeMin.value);
      var ma = parseInt(tfRangeMax.value);
      if (mi > ma) { tfRangeMin.value = ma; mi = ma; }
      tfStart.value = allDates[mi];
      _applyDateFilter();
    });
    tfRangeMax.addEventListener("input", function () {
      var mi = parseInt(tfRangeMin.value);
      var ma = parseInt(tfRangeMax.value);
      if (ma < mi) { tfRangeMax.value = mi; ma = mi; }
      tfEnd.value = allDates[ma];
      _applyDateFilter();
    });

    // Reset button
    tfReset.addEventListener("click", function () {
      tfRangeMin.value = 0;
      tfRangeMax.value = allDates.length - 1;
      tfStart.value = minDate;
      tfEnd.value = maxDate;
      dateFilterMin = null;
      dateFilterMax = null;
      refreshTableVisibility();
      if (renderer && currentView !== "list") renderer.refresh();
    });
  }

  /** Find the index of the closest date in allDates. */
  function _closestDateIndex(dateStr, roundDown) {
    for (var i = 0; i < allDates.length; i++) {
      if (allDates[i] >= dateStr) return roundDown ? Math.max(0, i) : i;
    }
    return allDates.length - 1;
  }

  function _applyDateFilter() {
    var mi = parseInt(tfRangeMin.value);
    var ma = parseInt(tfRangeMax.value);
    var isFullRange = (mi === 0 && ma === allDates.length - 1);
    dateFilterMin = isFullRange ? null : allDates[mi];
    dateFilterMax = isFullRange ? null : allDates[ma];
    refreshTableVisibility();
    if (renderer && currentView !== "list") renderer.refresh();
  }

  // Expose for testing
  window.setDateFilter = function (minDate, maxDate) {
    if (!allDates.length) return;
    if (minDate) {
      dateFilterMin = minDate;
      tfStart.value = minDate;
      tfRangeMin.value = _closestDateIndex(minDate, true);
    }
    if (maxDate) {
      dateFilterMax = maxDate;
      tfEnd.value = maxDate;
      tfRangeMax.value = _closestDateIndex(maxDate, false);
    }
    refreshTableVisibility();
    if (renderer && currentView !== "list") renderer.refresh();
  };
  window.clearDateFilter = function () {
    tfReset.click();
  };

  // --- Card panel resize ---------------------------------------------------
  (function () {
    var handle = document.getElementById("card-resize-handle");
    var panel = document.getElementById("card-panel");
    var isResizing = false;

    handle.addEventListener("mousedown", function (e) {
      isResizing = true;
      handle.classList.add("active");
      panel.classList.add("resizing");
      e.preventDefault();
    });

    document.addEventListener("mousemove", function (e) {
      if (!isResizing) return;
      var newWidth = window.innerWidth - e.clientX;
      var min = 280;
      var max = window.innerWidth * 0.8;
      newWidth = Math.max(min, Math.min(max, newWidth));
      panel.style.width = newWidth + "px";
    });

    document.addEventListener("mouseup", function () {
      if (isResizing) {
        isResizing = false;
        handle.classList.remove("active");
        panel.classList.remove("resizing");
      }
    });
  })();
})();
