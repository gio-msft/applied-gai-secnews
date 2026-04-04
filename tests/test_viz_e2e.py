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
