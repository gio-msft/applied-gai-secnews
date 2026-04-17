"""Playwright end-to-end tests for the Trends overlay.

Shares the viz-server pattern from test_viz_e2e.py but with a dataset large
enough for the streamgraph to render non-trivially.
"""

import http.server
import json
import os
import shutil
import threading
from datetime import datetime, timedelta

import pytest

pytest.importorskip("playwright")

pytestmark = pytest.mark.e2e

from playwright.sync_api import sync_playwright  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIZ_DIR = os.path.join(PROJECT_ROOT, "docs")


def _build_sample_graph():
    """Generate a small but time-distributed dataset across two clusters."""
    nodes = []
    base = datetime(2025, 10, 1)
    # Cluster 0: "LLM Security" — mostly recent papers (rising)
    for i in range(20):
        day = base + timedelta(days=i * 7)  # weekly
        recent_boost = 3 if i >= 15 else 1
        for k in range(recent_boost):
            pid = f"cl0_{i}_{k}"
            nodes.append({
                "id": pid,
                "title": f"Prompt Injection Attacks on LLM Agents {i}.{k}",
                "authors": ["Alice"],
                "affiliations": ["MIT"],
                "url": f"http://arxiv.org/abs/{pid}",
                "published": day.strftime("%Y-%m-%dT00:00:00Z"),
                "emoji": "🛡️",
                "tag": "security",
                "one_liner": "A study.",
                "points": ["a", "b"],
                "interest_score": 7,
                "projects": [],
                "relevant": True,
                "x": 0.0, "y": 0.0,
                "semantic_x": 0.0, "semantic_y": 0.0,
                "cluster": 0,
            })
    # Cluster 1: "Cyber Threats" — mostly older (cooling)
    for i in range(20):
        day = base + timedelta(days=i * 7)
        prior_boost = 3 if i < 5 else 1
        for k in range(prior_boost):
            pid = f"cl1_{i}_{k}"
            nodes.append({
                "id": pid,
                "title": f"Malware Family Detection via Static Analysis {i}.{k}",
                "authors": ["Bob"],
                "affiliations": ["CrowdStrike"],
                "url": f"http://arxiv.org/abs/{pid}",
                "published": day.strftime("%Y-%m-%dT00:00:00Z"),
                "emoji": "🦠",
                "tag": "cyber",
                "one_liner": "A study.",
                "points": ["a", "b"],
                "interest_score": 5,
                "projects": [],
                "relevant": True,
                "x": 0.0, "y": 0.0,
                "semantic_x": 0.0, "semantic_y": 0.0,
                "cluster": 1,
            })
    return {
        "nodes": nodes,
        "citation_edges": [],
        "author_edges": [],
        "similarity_edges": [],
        "topic_regions": [
            {"id": 0, "label": "LLM Security",
             "color": {"light": "#4f6df5", "dark": "#6b8aff"},
             "papers": [n["id"] for n in nodes if n["cluster"] == 0],
             "hull": [], "centroid": [0, 0]},
            {"id": 1, "label": "Cyber Threats",
             "color": {"light": "#e67e22", "dark": "#f5a623"},
             "papers": [n["id"] for n in nodes if n["cluster"] == 1],
             "hull": [], "centroid": [0, 0]},
        ],
    }


@pytest.fixture(scope="module")
def viz_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("viz_trends")
    dest = str(tmp / "viz")
    shutil.copytree(VIZ_DIR, dest)
    data_dir = os.path.join(dest, "data")
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, "graph.json"), "w") as f:
        json.dump(_build_sample_graph(), f)

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
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="module")
def page(pw_instance, viz_server):
    browser = pw_instance.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    p = context.new_page()
    p.goto(viz_server + "/index.html", wait_until="networkidle")
    p.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
    yield p
    browser.close()


class TestTrendsOverlay:

    def test_toggle_button_present(self, page):
        btn = page.query_selector("#trends-toggle")
        assert btn is not None
        assert btn.is_visible()

    def test_overlay_hidden_by_default(self, page):
        overlay = page.query_selector("#trends-overlay")
        assert "hidden" in overlay.get_attribute("class")

    def test_open_renders_streamgraph(self, page):
        page.click("#trends-toggle")
        page.wait_for_selector("#trends-overlay:not(.hidden)", timeout=3000)
        # Streamgraph SVG appears with at least one path
        page.wait_for_selector("#trends-streamgraph svg path.trends-stream-path", timeout=3000)
        paths = page.query_selector_all("#trends-streamgraph svg path.trends-stream-path")
        assert len(paths) >= 2, f"expected ≥2 stream paths, got {len(paths)}"

        # Tag-share renders
        page.wait_for_selector("#trends-tagshare svg", timeout=2000)

        # Rising list rendered
        rising_items = page.query_selector_all("#trends-rising .trends-list-item")
        assert len(rising_items) >= 1

    def test_keyword_chart_renders(self, page):
        # Ensure open
        overlay = page.query_selector("#trends-overlay")
        if "hidden" in overlay.get_attribute("class"):
            page.click("#trends-toggle")
            page.wait_for_selector("#trends-overlay:not(.hidden)", timeout=3000)
        page.wait_for_selector("#trends-keywords svg path.trends-kw-line", timeout=3000)
        lines = page.query_selector_all("#trends-keywords svg path.trends-kw-line")
        assert len(lines) >= 3, f"expected ≥3 keyword lines, got {len(lines)}"
        chips = page.query_selector_all("#trends-kw-legend .trends-kw-chip")
        assert len(chips) == len(lines), "legend chip count should match line count"

    def test_clicking_keyword_chip_sets_search(self, page):
        # Ensure overlay is closed, search is clear
        page.evaluate(
            "document.getElementById('search-clear').click();"
        )
        page.click("#trends-toggle")
        page.wait_for_selector("#trends-overlay:not(.hidden)", timeout=3000)
        page.wait_for_selector("#trends-kw-legend .trends-kw-chip", timeout=3000)
        first_chip_term = page.eval_on_selector(
            "#trends-kw-legend .trends-kw-chip", "el => el.dataset.term"
        )
        page.click("#trends-kw-legend .trends-kw-chip:first-child")
        # Overlay closes
        page.wait_for_function(
            "document.getElementById('trends-overlay').classList.contains('hidden')",
            timeout=2000,
        )
        value = page.input_value("#search-input")
        assert value.strip() == first_chip_term, (
            f"search input should be set to '{first_chip_term}', got '{value}'"
        )
        # Cleanup
        page.evaluate("document.getElementById('search-clear').click();")

    def test_escape_closes_overlay(self, page):
        # Ensure open first
        overlay = page.query_selector("#trends-overlay")
        if "hidden" in overlay.get_attribute("class"):
            page.click("#trends-toggle")
            page.wait_for_selector("#trends-overlay:not(.hidden)", timeout=3000)
        page.keyboard.press("Escape")
        page.wait_for_function(
            "document.getElementById('trends-overlay').classList.contains('hidden')",
            timeout=2000,
        )

    def test_clicking_rising_item_applies_cluster_filter(self, page):
        # Make sure cluster filter is cleared before we start
        page.evaluate("window.clearClusterFilter && window.clearClusterFilter()")
        page.click("#trends-toggle")
        page.wait_for_selector("#trends-overlay:not(.hidden)", timeout=3000)
        page.wait_for_selector("#trends-rising .trends-list-item", timeout=3000)
        page.click("#trends-rising .trends-list-item:first-child")
        # Overlay should close
        page.wait_for_function(
            "document.getElementById('trends-overlay').classList.contains('hidden')",
            timeout=2000,
        )
        # Cluster filter banner becomes visible
        page.wait_for_selector("#cluster-filter-banner:not(.hidden)", timeout=2000)
        label = page.inner_text("#cluster-filter-label")
        assert label.strip() != ""
        # Cleanup
        page.click("#cluster-filter-clear")

    def test_no_console_errors_on_overlay(self, pw_instance, viz_server):
        errors = []
        browser = pw_instance.chromium.launch(headless=True)
        p = browser.new_page()
        p.on("pageerror", lambda err: errors.append(str(err)))
        p.goto(viz_server + "/index.html", wait_until="networkidle")
        p.wait_for_selector("#loading-overlay", state="hidden", timeout=15000)
        p.click("#trends-toggle")
        p.wait_for_selector("#trends-overlay:not(.hidden)", timeout=3000)
        p.wait_for_selector("#trends-streamgraph svg path.trends-stream-path", timeout=3000)
        browser.close()
        assert errors == [], f"Console errors: {errors}"
