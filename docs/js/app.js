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
    semantic:  { light: "rgba(40,160,80,0.5)",     dark: "rgba(100,220,140,0.4)"  },
  };

  const MIN_NODE_SIZE = 1.5;
  const MAX_NODE_SIZE = 8;
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
  let selectedNode = null;
  let draggedNode = null;
  let isDragging = false;

  // --- DOM refs ------------------------------------------------------------
  const container = document.getElementById("graph-container");
  const loadingOverlay = document.getElementById("loading-overlay");
  const cardPanel = document.getElementById("card-panel");
  const searchInput = document.getElementById("search-input");

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

  // --- Data loading --------------------------------------------------------
  fetch("data/graph.json")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      graphData = data;
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

      graph.addNode(n.id, {
        x: (n.x || 0) * SCALE_FACTOR,
        y: (n.y || 0) * SCALE_FACTOR,
        size: size,
        color: hexToRgba(tagColor[theme], 0.8),
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
      var nodeId = event.node;
      selectedNode = nodeId;
      var attrs = graph.getNodeAttributes(nodeId);
      showCard(attrs._data);
      highlightNode(nodeId);
    });

    // --- Event: click stage (deselect) --------------------------------------
    renderer.on("clickStage", function () {
      selectedNode = null;
      hideCard();
      clearHighlight();
    });

    // --- Event: hover -------------------------------------------------------
    var tooltip = document.getElementById("node-tooltip");

    renderer.on("enterNode", function (event) {
      highlightedNode = event.node;
      var data = graph.getNodeAttribute(event.node, "_data");
      tooltip.textContent = (data.emoji || "") + " " + (data.title || event.node);
      tooltip.classList.remove("hidden");
      renderer.refresh();
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
    });

    // Node reducer for hover / selection / cluster filter dimming
    renderer.setSetting("nodeReducer", function (node, data) {
      var res = Object.assign({}, data);
      var dimColor = currentTheme() === "dark" ? "#1a1a2e" : "#d0d0d8";

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
      return res;
    });

    renderer.setSetting("edgeReducer", function (edge, data) {
      var res = Object.assign({}, data);

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
  }

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

  /**
   * Draw a smooth closed curve through an array of {x,y} points
   * using Catmull-Rom → cubic Bézier conversion.
   */
  function drawSmoothClosed(ctx, pts) {
    var n = pts.length;
    if (n < 3) return;
    var tension = 0.35; // 0 = sharp, 1 = very round
    ctx.beginPath();

    for (var i = 0; i < n; i++) {
      var p0 = pts[(i - 1 + n) % n];
      var p1 = pts[i];
      var p2 = pts[(i + 1) % n];
      var p3 = pts[(i + 2) % n];

      var cp1x = p1.x + (p2.x - p0.x) * tension;
      var cp1y = p1.y + (p2.y - p0.y) * tension;
      var cp2x = p2.x - (p3.x - p1.x) * tension;
      var cp2y = p2.y - (p3.y - p1.y) * tension;

      if (i === 0) ctx.moveTo(p1.x, p1.y);
      ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
    }
    ctx.closePath();
  }

  function drawHulls() {
    if (!hullCtx || !renderer) return;
    // Resize canvas to match viewport
    var rect = container.getBoundingClientRect();
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
      var rings = region.rings || (region.hull && region.hull.length >= 3 ? [region.hull] : []);
      if (!rings.length) return;
      var color = theme === "dark" ? region.color.dark : region.color.light;

      rings.forEach(function (ring) {
        if (ring.length < 3) return;

        // Transform ring points from graph coords to viewport
        var viewPts = ring.map(function (pt) {
          return renderer.graphToViewport({
            x: pt[0] * SCALE_FACTOR,
            y: pt[1] * SCALE_FACTOR,
          });
        });

        // Draw the pre-smoothed shape directly (Shapely buffer is already smooth)
        hullCtx.beginPath();
        hullCtx.moveTo(viewPts[0].x, viewPts[0].y);
        for (var i = 1; i < viewPts.length; i++) {
          hullCtx.lineTo(viewPts[i].x, viewPts[i].y);
        }
        hullCtx.closePath();
        hullCtx.fillStyle = hexToRgba(color, 0.07);
        hullCtx.fill();
        hullCtx.strokeStyle = hexToRgba(color, 0.25);
        hullCtx.lineWidth = 1.5;
        hullCtx.stroke();
      });

    });

    // Draw topic labels on the overlay canvas (in front of nodes)
    if (labelCtx) {
      regions.forEach(function (region) {
        if (!region.centroid) return;
        var color = theme === "dark" ? region.color.dark : region.color.light;
        var cView = renderer.graphToViewport({
          x: region.centroid[0] * SCALE_FACTOR,
          y: region.centroid[1] * SCALE_FACTOR,
        });
        labelCtx.font = "bold 13px system-ui, -apple-system, sans-serif";
        labelCtx.textAlign = "center";
        labelCtx.textBaseline = "middle";
        labelCtx.fillStyle = hexToRgba(color, 0.7);
        labelCtx.fillText(region.label, cView.x, cView.y);
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
    if (renderer) renderer.refresh();
  }

  function clearClusterFilter() {
    filteredCluster = null;
    var banner = document.getElementById("cluster-filter-banner");
    if (banner) banner.classList.add("hidden");
    if (renderer) renderer.refresh();
  }

  // Expose for programmatic access (e.g., tests)
  window.filterByCluster = filterByCluster;
  window.clearClusterFilter = clearClusterFilter;

  // Banner close button
  document.getElementById("cluster-filter-clear").addEventListener("click", clearClusterFilter);

  // --- Canvas label click detection (on graph container, above hull canvas) -
  container.addEventListener("click", function (event) {
    if (activeLayer !== "semantic" || !renderer) return;
    var rect = container.getBoundingClientRect();
    var mx = event.clientX - rect.left;
    var my = event.clientY - rect.top;

    var regions = graphData ? (graphData.topic_regions || []) : [];
    for (var i = 0; i < regions.length; i++) {
      var region = regions[i];
      if (!region.centroid) continue;
      var cView = renderer.graphToViewport({
        x: region.centroid[0] * SCALE_FACTOR,
        y: region.centroid[1] * SCALE_FACTOR,
      });
      // Hit test: within bounding box of the label text
      var labelW = region.label.length * 7; // rough width estimate
      var labelH = 18;
      if (Math.abs(mx - cView.x) < labelW / 2 && Math.abs(my - cView.y) < labelH / 2) {
        filterByCluster(region.id);
        event.stopPropagation();
        return;
      }
    }
  });

  // Change cursor on hover over labels
  container.addEventListener("mousemove", function (event) {
    if (activeLayer !== "semantic" || !renderer) return;
    var rect = container.getBoundingClientRect();
    var mx = event.clientX - rect.left;
    var my = event.clientY - rect.top;
    var hit = false;

    var regions = graphData ? (graphData.topic_regions || []) : [];
    for (var i = 0; i < regions.length; i++) {
      var region = regions[i];
      if (!region.centroid) continue;
      var cView = renderer.graphToViewport({
        x: region.centroid[0] * SCALE_FACTOR,
        y: region.centroid[1] * SCALE_FACTOR,
      });
      var labelW = region.label.length * 7;
      var labelH = 18;
      if (Math.abs(mx - cView.x) < labelW / 2 && Math.abs(my - cView.y) < labelH / 2) {
        hit = true;
        break;
      }
    }
    container.style.cursor = hit ? "pointer" : "";
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
      graph.setNodeAttribute(node, "color", hexToRgba(tagColor[theme], 0.8));
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
    var graphContainer = document.getElementById("graph-container");
    var rect = graphContainer.getBoundingClientRect();
    selectionRing.style.width = ringSize + "px";
    selectionRing.style.height = ringSize + "px";
    selectionRing.style.left = (rect.left + viewportPos.x - ringSize / 2) + "px";
    selectionRing.style.top = (rect.top + viewportPos.y - ringSize / 2) + "px";
    selectionRing.classList.remove("hidden");
  }

  // --- Highlight / focus ---------------------------------------------------
  function highlightNode(nodeId) {
    selectedNode = nodeId;
    renderer.refresh();
    updateSelectionRing();
  }

  function clearHighlight() {
    selectedNode = null;
    highlightedNode = null;
    selectionRing.classList.add("hidden");
    renderer.refresh();
  }

  // --- Search --------------------------------------------------------------
  searchInput.addEventListener("input", function () {
    var query = searchInput.value.trim().toLowerCase();
    if (!query) {
      clearHighlight();
      return;
    }

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
      var pos = renderer.getNodeDisplayData(match);
      var camera = renderer.getCamera();
      camera.animate({ x: pos.x, y: pos.y, ratio: 0.3 }, { duration: 400 });
      highlightNode(match);
    }
  });

  document.getElementById("search-clear").addEventListener("click", function () {
    searchInput.value = "";
    selectedNode = null;
    hideCard();
    clearHighlight();
  });

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
