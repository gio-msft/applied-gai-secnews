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

  // Respect system preference on first load
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
    applyTheme("light");
  }

  // --- Data loading --------------------------------------------------------
  fetch("data/graph.json")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      graphData = data;
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
        color: tagColor[theme],
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

    // Node reducer for hover / selection dimming
    renderer.setSetting("nodeReducer", function (node, data) {
      var res = Object.assign({}, data);

      // Highlight the selected node — keep original color, overlay does the ring
      if (node === selectedNode) {
        res.zIndex = 10;
        return res;
      }

      if (highlightedNode && highlightedNode !== node) {
        if (!graph.hasEdge(highlightedNode, node) && !graph.hasEdge(node, highlightedNode)) {
          res.color = currentTheme() === "dark" ? "#1a1a2e" : "#d0d0d8";
          res.label = "";
        }
      }
      if (selectedNode && selectedNode !== node) {
        if (!graph.hasEdge(selectedNode, node) && !graph.hasEdge(node, selectedNode)) {
          res.color = currentTheme() === "dark" ? "#1a1a2e" : "#d0d0d8";
          res.label = "";
        }
      }
      return res;
    });

    renderer.setSetting("edgeReducer", function (edge, data) {
      var res = Object.assign({}, data);
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
    });
  }

  // --- Edge management -----------------------------------------------------
  function addEdges(layer) {
    var edges = layer === "citations" ? graphData.citation_edges : graphData.author_edges;
    var theme = currentTheme();
    var color = EDGE_COLORS[layer][theme];

    edges.forEach(function (e, i) {
      // Skip edges whose endpoints don't exist in the graph
      if (!graph.hasNode(e.source) || !graph.hasNode(e.target)) return;
      // Avoid duplicate edges
      if (graph.hasEdge(e.source, e.target)) return;

      var size = layer === "authors" ? Math.min((e.weight || 1) * 0.8, 5) : 0.5;
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
    activeLayer = layer;
    clearEdges();
    addEdges(layer);
    renderer.setSetting("defaultEdgeType", "line");
    renderer.refresh();
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
      graph.setNodeAttribute(node, "color", tagColor[theme]);
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
