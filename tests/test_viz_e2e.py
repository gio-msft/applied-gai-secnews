"""Playwright end-to-end tests for the interactive paper graph visualization.

Requires:
    pip install pytest-playwright
    playwright install chromium

Tests spin up a local HTTP server for docs/ and exercise the graph UI.
"""

import http.server
import json
import os
import shutil
import threading

import pytest

# Guard: skip entire module if playwright is not installed
pytest.importorskip("playwright")

# Mark all tests in this module as e2e (excluded from default pytest run)
pytestmark = pytest.mark.e2e

from playwright.sync_api import sync_playwright, expect  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIZ_DIR = os.path.join(PROJECT_ROOT, "docs")

# ---------------------------------------------------------------------------
# Sample graph data — small enough for fast tests
# ---------------------------------------------------------------------------
SAMPLE_GRAPH = {
    "nodes": [
        {
            "id": "2601.00001v1",
            "title": "Adversarial Prompt Injection in LLM Agents",
            "authors": ["Alice Smith", "Bob Jones"],
            "affiliations": ["MIT", "Stanford"],
            "url": "http://arxiv.org/pdf/2601.00001v1.pdf",
            "published": "2026-01-15T10:00:00Z",
            "emoji": "🛡️",
            "tag": "security",
            "one_liner": "A novel attack vector for prompt injection in tool-using agents.",
            "points": [
                "Achieves 92% success rate on AgentBench",
                "Bypasses 3 of 4 commercial guardrails",
                "Proposes a defense reducing attack success to 12%",
            ],
            "interest_score": 9,
            "projects": ["agentic-prompt-injection-grpo"],
            "relevant": True,
            "x": -0.5,
            "y": 0.3,
            "semantic_x": -0.4,
            "semantic_y": 0.2,
            "cluster": 0,
        },
        {
            "id": "2601.00002v1",
            "title": "Backdoor Detection via Attention Analysis",
            "authors": ["Bob Jones", "Charlie Lee"],
            "affiliations": ["Stanford", "Google DeepMind"],
            "url": "http://arxiv.org/pdf/2601.00002v1.pdf",
            "published": "2026-01-16T12:00:00Z",
            "emoji": "🔍",
            "tag": "security",
            "one_liner": "Detects sleeper-agent backdoors by inspecting attention head patterns.",
            "points": [
                "99.1% recall on known backdoor benchmarks",
                "Works across model sizes 7B–70B",
                "Open-source tooling released",
            ],
            "interest_score": 8,
            "projects": ["backdoor-detection"],
            "relevant": True,
            "x": 0.5,
            "y": -0.2,
            "semantic_x": 0.6,
            "semantic_y": -0.1,
            "cluster": 0,
        },
        {
            "id": "2601.00003v1",
            "title": "AI-Powered Malware Classification",
            "authors": ["Dave Wilson"],
            "affiliations": ["CrowdStrike"],
            "url": "http://arxiv.org/pdf/2601.00003v1.pdf",
            "published": "2026-01-17T08:00:00Z",
            "emoji": "🦠",
            "tag": "cyber",
            "one_liner": "LLM-powered malware family classifier with 97% accuracy.",
            "points": [
                "Outperforms VirusTotal on zero-day samples",
                "Handles obfuscated PE binaries",
                "Sub-second inference on commodity GPU",
            ],
            "interest_score": 6,
            "projects": [],
            "relevant": True,
            "x": 0.0,
            "y": -0.6,
            "semantic_x": -0.1,
            "semantic_y": -0.5,
            "cluster": 1,
        },
    ],
    "citation_edges": [
        {"source": "2601.00001v1", "target": "2601.00002v1"},
    ],
    "author_edges": [
        {
            "source": "2601.00001v1",
            "target": "2601.00002v1",
            "weight": 1,
            "shared_authors": ["Bob Jones"],
        },
    ],
    "similarity_edges": [
        {"source": "2601.00001v1", "target": "2601.00002v1", "weight": 0.85},
        {"source": "2601.00001v1", "target": "2601.00003v1", "weight": 0.62},
    ],
    "topic_regions": [
        {
            "id": 0,
            "label": "LLM Security",
            "color": {"light": "#4f6df5", "dark": "#6b8aff"},
            "papers": ["2601.00001v1", "2601.00002v1"],
            "hull": [[-0.55, 0.35], [0.55, 0.35], [0.55, -0.25], [-0.55, -0.25]],
            "centroid": [0.0, 0.05],
        },
        {
            "id": 1,
            "label": "Cyber Threat Analysis",
            "color": {"light": "#e67e22", "dark": "#f5a623"},
            "papers": ["2601.00003v1"],
            "hull": [],
            "centroid": [-0.1, -0.5],
        },
    ],
}

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def viz_server(tmp_path_factory):
    """Copy viz site to a temp dir, inject sample graph.json, and serve it."""
    tmp = tmp_path_factory.mktemp("viz")
    dest = str(tmp / "viz")
    shutil.copytree(VIZ_DIR, dest)

    # Write sample graph data
    data_dir = os.path.join(dest, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "graph.json"), "w") as f:
        json.dump(SAMPLE_GRAPH, f)

    # Start a simple HTTP server
    handler = http.server.SimpleHTTPRequestHandler
    os.chdir(dest)
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    yield f"http://127.0.0.1:{port}"

    server.shutdown()


@pytest.fixture(scope="module")
def pw_instance():
    """Module-scoped Playwright instance shared by all tests."""
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="module")
def browser_page(pw_instance, viz_server):
    """Launch a browser and navigate to the viz page."""
    browser = pw_instance.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.goto(viz_server + "/index.html", wait_until="networkidle")
    # Wait for loading overlay to disappear
    page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
    yield page
    browser.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPageLoad:

    def test_no_console_errors(self, pw_instance, viz_server):
        """Page loads without JavaScript errors."""
        errors = []
        browser = pw_instance.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("pageerror", lambda err: errors.append(str(err)))
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
        browser.close()
        assert errors == [], f"Console errors: {errors}"

    def test_graph_canvas_visible(self, browser_page):
        """The graph container has a canvas element (Sigma renders via canvas/WebGL)."""
        canvas = browser_page.query_selector("#graph-container canvas")
        assert canvas is not None, "No canvas found in #graph-container"
        assert canvas.is_visible()


class TestLayerToggle:

    def test_citations_active_by_default(self, browser_page):
        btn = browser_page.query_selector('.layer-btn[data-layer="citations"]')
        assert "active" in btn.get_attribute("class")

    def test_switch_to_authors(self, browser_page):
        browser_page.click('.layer-btn[data-layer="authors"]')
        btn_authors = browser_page.query_selector('.layer-btn[data-layer="authors"]')
        btn_citations = browser_page.query_selector('.layer-btn[data-layer="citations"]')
        assert "active" in btn_authors.get_attribute("class")
        assert "active" not in btn_citations.get_attribute("class")
        # Switch back
        browser_page.click('.layer-btn[data-layer="citations"]')


class TestCardPanel:

    def test_card_hidden_initially(self, browser_page):
        panel = browser_page.query_selector("#card-panel")
        assert "hidden" in panel.get_attribute("class")

    def test_card_close_button(self, browser_page):
        """If card is open, clicking close hides it."""
        panel = browser_page.query_selector("#card-panel")
        # Force-show for test
        browser_page.evaluate("document.getElementById('card-panel').classList.remove('hidden')")
        assert "hidden" not in panel.get_attribute("class")
        browser_page.click("#card-close")
        assert "hidden" in panel.get_attribute("class")


class TestSearch:

    def test_search_input_exists(self, browser_page):
        el = browser_page.query_selector("#search-input")
        assert el is not None

    def test_search_triggers_card(self, browser_page):
        """Typing a known title fragment should open the card panel."""
        browser_page.fill("#search-input", "Adversarial Prompt")
        browser_page.wait_for_timeout(600)
        panel = browser_page.query_selector("#card-panel")
        class_attr = panel.get_attribute("class") or ""
        # Card should be shown (hidden class removed)
        assert "hidden" not in class_attr
        title = browser_page.text_content("#card-title")
        assert "Adversarial Prompt Injection" in title
        # Clean up
        browser_page.click("#search-clear")
        browser_page.wait_for_timeout(300)


class TestThemeToggle:

    def test_default_theme(self, browser_page):
        theme = browser_page.get_attribute("html", "data-theme")
        assert theme in ("dark", "light")

    def test_toggle_changes_theme(self, browser_page):
        original = browser_page.get_attribute("html", "data-theme")
        browser_page.click("#theme-toggle")
        new_theme = browser_page.get_attribute("html", "data-theme")
        assert new_theme != original
        # Toggle back
        browser_page.click("#theme-toggle")
        restored = browser_page.get_attribute("html", "data-theme")
        assert restored == original


class TestResponsive:

    def test_mobile_viewport_card_bottom_sheet(self, pw_instance, viz_server):
        """At mobile viewport, card panel should be positioned as a bottom sheet."""
        browser = pw_instance.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 375, "height": 667})
        page = context.new_page()
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)

        # Show card panel for inspection
        page.evaluate("document.getElementById('card-panel').classList.remove('hidden')")
        # Check computed style: at mobile, the panel should have width 100%
        width = page.evaluate(
            "getComputedStyle(document.getElementById('card-panel')).width"
        )
        # 375px viewport → panel should fill all 375px
        assert int(width.replace("px", "")) >= 370
        browser.close()


class TestScreenshots:

    def test_capture_dark_mode(self, pw_instance, viz_server, tmp_path):
        browser = pw_instance.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
        page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
        page.wait_for_timeout(500)
        path = str(tmp_path / "dark-mode.png")
        page.screenshot(path=path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 1000
        browser.close()

    def test_capture_light_mode(self, pw_instance, viz_server, tmp_path):
        browser = pw_instance.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
        page.evaluate("document.documentElement.setAttribute('data-theme', 'light')")
        page.wait_for_timeout(500)
        path = str(tmp_path / "light-mode.png")
        page.screenshot(path=path)
        assert os.path.exists(path)
        assert os.path.getsize(path) > 1000
        browser.close()


class TestSplitView:
    """Tests for the Split Graph/List Mode feature."""

    def test_view_toggle_buttons_exist(self, browser_page):
        """View toggle buttons for graph, split, and list modes should exist."""
        for mode in ("graph", "split", "list"):
            btn = browser_page.query_selector(f'.view-btn[data-view="{mode}"]')
            assert btn is not None, f"Missing view button for '{mode}'"

    def test_graph_mode_default(self, browser_page):
        """Graph mode should be active by default."""
        btn = browser_page.query_selector('.view-btn[data-view="graph"]')
        assert "active" in btn.get_attribute("class")
        container = browser_page.query_selector("#main-container")
        assert "view-graph" in container.get_attribute("class")

    def test_table_hidden_in_graph_mode(self, browser_page):
        """Paper table should not be visible in graph-only mode."""
        table_container = browser_page.query_selector("#paper-table-container")
        assert not table_container.is_visible()

    def test_switch_to_split_view(self, browser_page):
        """Clicking split button should show both graph and table side by side."""
        browser_page.click('.view-btn[data-view="split"]')
        browser_page.wait_for_timeout(400)

        # Split button should be active
        btn = browser_page.query_selector('.view-btn[data-view="split"]')
        assert "active" in btn.get_attribute("class")

        # Both panes should be visible
        graph_pane = browser_page.query_selector("#graph-pane")
        table_container = browser_page.query_selector("#paper-table-container")
        assert graph_pane.is_visible()
        assert table_container.is_visible()

        # Container should have split class
        container = browser_page.query_selector("#main-container")
        assert "view-split" in container.get_attribute("class")

    def test_table_has_rows(self, browser_page):
        """Table should have rows matching the number of sample papers (3)."""
        # Should still be in split mode from previous test
        rows = browser_page.query_selector_all("#paper-table-body tr")
        assert len(rows) == 3, f"Expected 3 table rows, got {len(rows)}"

    def test_table_columns(self, browser_page):
        """Each row should have 5 cells: title, score, tag, topic, date."""
        row = browser_page.query_selector("#paper-table-body tr")
        cells = row.query_selector_all("td")
        assert len(cells) == 5, f"Expected 5 cells per row, got {len(cells)}"

    def test_table_default_sort_by_score(self, browser_page):
        """Default sort should be by score descending."""
        rows = browser_page.query_selector_all("#paper-table-body tr")
        scores = []
        for row in rows:
            score = row.get_attribute("data-score")
            scores.append(int(score))
        assert scores == sorted(scores, reverse=True), \
            f"Scores not in descending order: {scores}"

    def test_table_sort_by_title(self, browser_page):
        """Clicking title header should sort alphabetically."""
        # First click gives descending (toggle from non-title col)
        browser_page.click('#paper-table th[data-sort="title"]')
        browser_page.wait_for_timeout(100)
        rows = browser_page.query_selector_all("#paper-table-body tr")
        titles = [row.get_attribute("data-title") for row in rows]
        assert titles == sorted(titles, reverse=True), \
            f"Titles not sorted descending: {titles}"

        # Click again for ascending
        browser_page.click('#paper-table th[data-sort="title"]')
        browser_page.wait_for_timeout(100)
        rows = browser_page.query_selector_all("#paper-table-body tr")
        titles = [row.get_attribute("data-title") for row in rows]
        assert titles == sorted(titles), f"Titles not sorted ascending: {titles}"

    def test_table_row_click_opens_card(self, browser_page):
        """Clicking a table row should open the card panel."""
        browser_page.click("#card-close", force=True) if browser_page.query_selector(
            "#card-panel:not(.hidden)") else None
        browser_page.wait_for_timeout(200)

        # Click first table row
        first_row = browser_page.query_selector("#paper-table-body tr")
        first_row.click()
        browser_page.wait_for_timeout(600)

        # Card panel should be visible
        panel = browser_page.query_selector("#card-panel")
        class_attr = panel.get_attribute("class") or ""
        assert "hidden" not in class_attr, "Card panel should be visible after row click"

        # Selected row should have the pt-selected class
        assert "pt-selected" in first_row.get_attribute("class")

    def test_table_row_hover_highlights(self, browser_page):
        """Hovering a table row should add the pt-hovered class."""
        # Close card panel if open (it can overlay the table)
        browser_page.click("#card-close", force=True)
        browser_page.wait_for_timeout(300)
        second_row = browser_page.query_selector_all("#paper-table-body tr")[1]
        second_row.hover()
        browser_page.wait_for_timeout(200)
        assert "pt-hovered" in (second_row.get_attribute("class") or "")

    def test_switch_to_list_view(self, browser_page):
        """Clicking list button should hide graph and show full-width table."""
        browser_page.click('.view-btn[data-view="list"]')
        browser_page.wait_for_timeout(400)

        btn = browser_page.query_selector('.view-btn[data-view="list"]')
        assert "active" in btn.get_attribute("class")

        graph_pane = browser_page.query_selector("#graph-pane")
        table_container = browser_page.query_selector("#paper-table-container")
        assert not graph_pane.is_visible()
        assert table_container.is_visible()

    def test_switch_back_to_graph(self, browser_page):
        """Switching back to graph mode should hide table."""
        browser_page.click('.view-btn[data-view="graph"]')
        browser_page.wait_for_timeout(400)

        graph_pane = browser_page.query_selector("#graph-pane")
        table_container = browser_page.query_selector("#paper-table-container")
        assert graph_pane.is_visible()
        assert not table_container.is_visible()

    def test_tag_filter_hides_table_rows(self, browser_page):
        """Deactivating a tag should hide matching table rows in split mode."""
        # Switch to split mode
        browser_page.click('.view-btn[data-view="split"]')
        browser_page.wait_for_timeout(400)

        # Deactivate 'cyber' tag (only 1 paper: AI-Powered Malware)
        browser_page.click('.legend-item[data-tag="cyber"]')
        browser_page.wait_for_timeout(200)

        # Check that the cyber paper row is hidden
        rows = browser_page.query_selector_all("#paper-table-body tr")
        visible_count = 0
        for row in rows:
            if "pt-hidden" not in (row.get_attribute("class") or ""):
                visible_count += 1
        assert visible_count == 2, f"Expected 2 visible rows, got {visible_count}"

        # Re-activate it
        browser_page.click('.legend-item[data-tag="cyber"]')
        browser_page.wait_for_timeout(200)

    def test_search_filters_table(self, browser_page):
        """Searching should filter table rows to matching papers."""
        browser_page.fill("#search-input", "Backdoor")
        browser_page.wait_for_timeout(600)

        rows = browser_page.query_selector_all("#paper-table-body tr")
        visible_count = 0
        for row in rows:
            if "pt-hidden" not in (row.get_attribute("class") or ""):
                visible_count += 1
        assert visible_count == 1, f"Expected 1 visible row for 'Backdoor', got {visible_count}"

        # Clean up
        browser_page.click("#search-clear")
        browser_page.wait_for_timeout(300)


class TestSplitViewScreenshots:
    """Capture screenshots of all three view modes for visual inspection."""

    def test_capture_split_view(self, pw_instance, viz_server, tmp_path):
        browser = pw_instance.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)

        # Graph mode screenshot
        page.wait_for_timeout(500)
        page.screenshot(path=str(tmp_path / "view-graph.png"))

        # Split mode screenshot
        page.click('.view-btn[data-view="split"]')
        page.wait_for_timeout(500)
        page.screenshot(path=str(tmp_path / "view-split.png"))

        # List mode screenshot
        page.click('.view-btn[data-view="list"]')
        page.wait_for_timeout(500)
        page.screenshot(path=str(tmp_path / "view-list.png"))

        # Verify all screenshots exist and have content
        for name in ("view-graph.png", "view-split.png", "view-list.png"):
            path = str(tmp_path / name)
            assert os.path.exists(path), f"Screenshot {name} not found"
            assert os.path.getsize(path) > 1000, f"Screenshot {name} too small"

        # Copy screenshots to project screenshots dir for review
        screenshots_dir = os.path.join(PROJECT_ROOT, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        for name in ("view-graph.png", "view-split.png", "view-list.png"):
            shutil.copy2(str(tmp_path / name), os.path.join(screenshots_dir, name))

        browser.close()


class TestClusterFilterInListView:
    """Regression: clearing cluster filter in list view should show all rows."""

    def test_clear_cluster_filter_resets_table_in_list_view(self, pw_instance, viz_server, tmp_path):
        """Select a topic in graph → switch to list → clear filter → all rows visible."""
        browser = pw_instance.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)

        # 1. Switch to semantic layer
        page.click('.layer-btn[data-layer="semantic"]')
        page.wait_for_timeout(800)

        # 2. Filter by cluster 0 ("LLM Security") via JS (label click needs coords)
        page.evaluate("window.filterByCluster(0)")
        page.wait_for_timeout(300)

        # Banner should be visible
        banner = page.query_selector("#cluster-filter-banner")
        assert "hidden" not in (banner.get_attribute("class") or ""), \
            "Banner should be visible after filtering"

        # 3. Switch to list view
        page.click('.view-btn[data-view="list"]')
        page.wait_for_timeout(500)

        # Screenshot: list view with cluster filter active
        page.screenshot(path=str(tmp_path / "list-filtered.png"))

        # Only cluster-0 papers should be visible (2 out of 3)
        visible_before = page.evaluate("""
            Array.from(document.querySelectorAll('#paper-table-body tr'))
                .filter(r => !r.classList.contains('pt-hidden')).length
        """)
        assert visible_before == 2, \
            f"Expected 2 visible rows with cluster filter, got {visible_before}"

        # 4. Clear the cluster filter via the banner close button
        page.click("#cluster-filter-clear")
        page.wait_for_timeout(300)

        # Screenshot: list view after clearing filter
        page.screenshot(path=str(tmp_path / "list-unfiltered.png"))

        # ALL 3 rows should now be visible
        visible_after = page.evaluate("""
            Array.from(document.querySelectorAll('#paper-table-body tr'))
                .filter(r => !r.classList.contains('pt-hidden')).length
        """)
        assert visible_after == 3, \
            f"Expected 3 visible rows after clearing filter, got {visible_after}"

        # Banner should be hidden
        assert "hidden" in (banner.get_attribute("class") or ""), \
            "Banner should be hidden after clearing filter"

        # No JS errors
        assert errors == [], f"JS errors: {errors}"

        # Save screenshots for review
        screenshots_dir = os.path.join(PROJECT_ROOT, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        for name in ("list-filtered.png", "list-unfiltered.png"):
            shutil.copy2(str(tmp_path / name), os.path.join(screenshots_dir, name))

        browser.close()

    def test_toggle_cluster_filter_in_table_topic_column(self, pw_instance, viz_server, tmp_path):
        """Click topic cell to filter, click again to clear — all rows should return."""
        browser = pw_instance.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)

        # Switch to semantic layer + list view
        page.click('.layer-btn[data-layer="semantic"]')
        page.wait_for_timeout(800)
        page.click('.view-btn[data-view="list"]')
        page.wait_for_timeout(500)

        # Total rows before filtering
        total = page.evaluate("""
            Array.from(document.querySelectorAll('#paper-table-body tr'))
                .filter(r => !r.classList.contains('pt-hidden')).length
        """)
        assert total == 3

        # Click the topic cell of the first visible row that has a cluster
        page.evaluate("window.filterByCluster(0)")
        page.wait_for_timeout(300)

        filtered = page.evaluate("""
            Array.from(document.querySelectorAll('#paper-table-body tr'))
                .filter(r => !r.classList.contains('pt-hidden')).length
        """)
        assert filtered == 2, f"Expected 2 filtered rows, got {filtered}"

        # Toggle off by calling filterByCluster with the same ID (toggle behavior)
        page.evaluate("window.filterByCluster(0)")
        page.wait_for_timeout(300)

        page.screenshot(path=str(tmp_path / "list-toggle-unfiltered.png"))

        restored = page.evaluate("""
            Array.from(document.querySelectorAll('#paper-table-body tr'))
                .filter(r => !r.classList.contains('pt-hidden')).length
        """)
        assert restored == 3, \
            f"Expected 3 rows after toggling filter off, got {restored}"

        assert errors == [], f"JS errors: {errors}"

        screenshots_dir = os.path.join(PROJECT_ROOT, "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        shutil.copy2(str(tmp_path / "list-toggle-unfiltered.png"),
                      os.path.join(screenshots_dir, "list-toggle-unfiltered.png"))

        browser.close()


class TestNodeClickNearLabel:
    """Regression: clicking a node near a cluster label should open the card,
    not activate the cluster filter (GitHub issue: zoomed-in semantic view)."""

    # Custom graph with a node sitting right at the cluster centroid so the
    # label hit-box overlaps the node.
    OVERLAP_GRAPH = {
        "nodes": [
            {
                "id": "overlap-node",
                "title": "Paper Right On Centroid",
                "authors": ["Test Author"],
                "affiliations": ["TestU"],
                "url": "http://arxiv.org/pdf/overlap.pdf",
                "published": "2026-01-15T10:00:00Z",
                "emoji": "📌",
                "tag": "security",
                "one_liner": "This node sits at the cluster centroid.",
                "points": ["Point A", "Point B"],
                "interest_score": 9,
                "projects": [],
                "relevant": True,
                "x": 0.0,
                "y": 0.0,
                "semantic_x": 0.0,   # exactly at centroid
                "semantic_y": -0.3,
                "cluster": 0,
            },
            {
                "id": "far-node",
                "title": "Far Away Paper",
                "authors": ["Other Author"],
                "affiliations": ["OtherU"],
                "url": "http://arxiv.org/pdf/far.pdf",
                "published": "2026-01-16T10:00:00Z",
                "emoji": "🔭",
                "tag": "security",
                "one_liner": "This node is far from the centroid.",
                "points": ["Point C"],
                "interest_score": 5,
                "projects": [],
                "relevant": True,
                "x": 0.8,
                "y": 0.8,
                "semantic_x": 0.3,
                "semantic_y": -0.3,
                "cluster": 0,
            },
        ],
        "citation_edges": [],
        "author_edges": [],
        "similarity_edges": [
            {"source": "overlap-node", "target": "far-node", "weight": 0.7},
        ],
        "topic_regions": [
            {
                "id": 0,
                "label": "Security of Tool-Using LLM Agents",
                "color": {"light": "#4f6df5", "dark": "#6b8aff"},
                "papers": ["overlap-node", "far-node"],
                "hull": [[-0.3, -0.6], [0.6, -0.6], [0.6, 0.0], [-0.3, 0.0]],
                "centroid": [0.0, -0.3],
            },
        ],
    }

    def test_click_node_at_centroid_opens_card_not_filter(self, pw_instance, tmp_path):
        """Click a node that overlaps the cluster label → card should open,
        cluster filter should NOT activate."""
        import functools

        # Spin up a dedicated server with our overlap graph
        dest = str(tmp_path / "viz")
        shutil.copytree(VIZ_DIR, dest)
        data_dir = os.path.join(dest, "data")
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "graph.json"), "w") as f:
            json.dump(self.OVERLAP_GRAPH, f)

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=dest)
        server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        try:
            browser = pw_instance.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="networkidle")
            page.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)

            # Switch to semantic layer
            page.click('.layer-btn[data-layer="semantic"]')
            page.wait_for_timeout(800)

            # Get the viewport coordinates of the "overlap-node" which sits
            # right at the cluster centroid (overlapping the label hit-box).
            coords = page.evaluate("""() => {
                const pos = window._sigmaRenderer.graphToViewport(
                    window._graph.getNodeAttributes('overlap-node')
                );
                const container = document.getElementById('graph-container');
                const rect = container.getBoundingClientRect();
                return { x: rect.left + pos.x, y: rect.top + pos.y };
            }""")

            # Click at the node's position using Playwright's real mouse
            page.mouse.click(coords["x"], coords["y"])
            page.wait_for_timeout(400)

            page.screenshot(path=str(tmp_path / "node-click-at-centroid.png"))

            # Check state
            result = page.evaluate("""() => {
                const card = document.getElementById('card-panel');
                const banner = document.getElementById('cluster-filter-banner');
                return {
                    cardVisible: !card.classList.contains('hidden'),
                    bannerVisible: !banner.classList.contains('hidden'),
                    selectedNode: window._selectedNode,
                    filteredCluster: window._filteredCluster,
                };
            }""")

            page.screenshot(path=str(tmp_path / "node-click-at-centroid.png"))

            assert result["cardVisible"], \
                "Card panel should be visible after clicking a node"
            assert not result["bannerVisible"], \
                "Cluster filter banner should NOT activate when clicking a node"

            assert errors == [], f"JS errors: {errors}"

        finally:
            server.shutdown()
            browser.close()


class TestTimeframeFilter:
    """Tests for the date-range timeframe filter."""

    def test_timeframe_filter_visible(self, browser_page):
        """Timeframe filter widget should be visible."""
        tf = browser_page.query_selector("#timeframe-filter")
        assert tf is not None
        assert tf.is_visible()

    def test_date_inputs_populated(self, browser_page):
        """Date inputs should be populated from graph data."""
        start_val = browser_page.input_value("#tf-start")
        end_val = browser_page.input_value("#tf-end")
        assert start_val, "Start date should be set"
        assert end_val, "End date should be set"
        assert start_val <= end_val

    def test_date_filter_hides_table_rows(self, browser_page):
        """Setting a narrow date range should hide papers outside the range."""
        # Switch to split view to see the table
        browser_page.click('.view-btn[data-view="split"]')
        browser_page.wait_for_timeout(400)

        # Sample data has dates: 2026-01-15, 2026-01-16, 2026-01-17
        # Filter to only the first date
        browser_page.evaluate("window.setDateFilter('2026-01-15', '2026-01-15')")
        browser_page.wait_for_timeout(300)

        visible = browser_page.evaluate("""
            Array.from(document.querySelectorAll('#paper-table-body tr'))
                .filter(r => !r.classList.contains('pt-hidden')).length
        """)
        assert visible == 1, f"Expected 1 visible row, got {visible}"

    def test_date_filter_reset_shows_all(self, browser_page):
        """Resetting the date filter should show all rows."""
        browser_page.evaluate("window.clearDateFilter()")
        browser_page.wait_for_timeout(300)

        visible = browser_page.evaluate("""
            Array.from(document.querySelectorAll('#paper-table-body tr'))
                .filter(r => !r.classList.contains('pt-hidden')).length
        """)
        assert visible == 3, f"Expected 3 visible rows after reset, got {visible}"

        # Switch back to graph view for subsequent tests
        browser_page.click('.view-btn[data-view="graph"]')
        browser_page.wait_for_timeout(300)
