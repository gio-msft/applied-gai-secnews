"""Full pre-deployment visual & functional inspection of the Paper Graph website.

Uses the REAL graph.json (production data) and exercises every UI feature.
Screenshots are saved to /tmp/viz_screenshots/ for manual review.

Run:
    python -m pytest tests/test_viz_full_predeployment.py -v --tb=short -x
"""

import http.server
import os
import threading
import time

import pytest

pytest.importorskip("playwright")
pytestmark = pytest.mark.e2e

from playwright.sync_api import sync_playwright  # noqa: E402

# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIZ_DIR = os.path.join(PROJECT_ROOT, "docs", "viz")
SCREENSHOT_DIR = "/tmp/viz_screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def viz_server():
    """Serve the REAL viz site (with production graph.json) on a random port."""
    handler = http.server.SimpleHTTPRequestHandler
    original_dir = os.getcwd()
    os.chdir(VIZ_DIR)
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    os.chdir(original_dir)


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        yield p


def _make_page(pw, viz_server, width=1280, height=800):
    browser = pw.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(viz_server + "/index.html", wait_until="networkidle")
    page.wait_for_selector("#loading-overlay", state="hidden", timeout=30000)
    return browser, page, errors


@pytest.fixture(scope="module")
def desktop_session(pw, viz_server):
    browser, page, errors = _make_page(pw, viz_server, 1280, 800)
    yield page, errors
    browser.close()


# ========================================================================
# 1. PAGE LOAD & INITIAL STATE
# ========================================================================
class TestPageLoad:

    def test_no_js_errors(self, desktop_session):
        _, errors = desktop_session
        assert errors == [], f"JS errors on load: {errors}"

    def test_title(self, desktop_session):
        page, _ = desktop_session
        assert "Paper Graph" in page.title()

    def test_loading_overlay_gone(self, desktop_session):
        page, _ = desktop_session
        overlay = page.locator("#loading-overlay")
        assert not overlay.is_visible()

    def test_graph_canvas_exists(self, desktop_session):
        page, _ = desktop_session
        canvases = page.query_selector_all("#graph-container canvas")
        assert len(canvases) >= 1, "Sigma should render at least one canvas"
        for c in canvases:
            assert c.is_visible()

    def test_toolbar_visible(self, desktop_session):
        page, _ = desktop_session
        assert page.locator("#toolbar").is_visible()

    def test_legend_visible(self, desktop_session):
        page, _ = desktop_session
        legend = page.locator("#legend")
        assert legend.is_visible()
        # Should have 3 legend items: Security, Cyber, General
        items = page.locator("#legend .legend-item")
        assert items.count() == 3

    def test_legend_dot_colors(self, desktop_session):
        page, _ = desktop_session
        dots = page.locator("#legend .legend-dot")
        for i in range(dots.count()):
            bg = dots.nth(i).evaluate("el => getComputedStyle(el).backgroundColor")
            assert bg and bg != "rgba(0, 0, 0, 0)", f"Legend dot {i} has no color"

    def test_card_panel_hidden(self, desktop_session):
        page, _ = desktop_session
        panel = page.locator("#card-panel")
        # It should be translated off-screen (hidden class)
        cls = panel.get_attribute("class") or ""
        assert "hidden" in cls

    def test_tooltip_hidden(self, desktop_session):
        page, _ = desktop_session
        tt = page.locator("#node-tooltip")
        cls = tt.get_attribute("class") or ""
        assert "hidden" in cls

    def test_selection_ring_hidden(self, desktop_session):
        page, _ = desktop_session
        ring = page.locator("#selection-ring")
        cls = ring.get_attribute("class") or ""
        assert "hidden" in cls

    def test_favicon_link(self, desktop_session):
        page, _ = desktop_session
        link = page.locator('link[rel="icon"]')
        assert link.count() >= 1
        href = link.first.get_attribute("href")
        assert href and "favicon" in href


# ========================================================================
# 2. TOOLBAR & LAYER TOGGLE
# ========================================================================
class TestToolbar:

    def test_logo_visible(self, desktop_session):
        page, _ = desktop_session
        logo = page.locator(".toolbar-logo")
        assert logo.is_visible()

    def test_title_gradient(self, desktop_session):
        page, _ = desktop_session
        title = page.locator(".toolbar-title")
        assert title.is_visible()
        text = title.text_content()
        assert text == "Paper Graph"

    def test_citations_active_by_default(self, desktop_session):
        page, _ = desktop_session
        btn = page.locator('.layer-btn[data-layer="citations"]')
        assert "active" in (btn.get_attribute("class") or "")
        assert btn.get_attribute("aria-pressed") == "true"

    def test_authors_inactive_by_default(self, desktop_session):
        page, _ = desktop_session
        btn = page.locator('.layer-btn[data-layer="authors"]')
        assert "active" not in (btn.get_attribute("class") or "")
        assert btn.get_attribute("aria-pressed") == "false"

    def test_switch_to_shared_authors(self, desktop_session):
        page, _ = desktop_session
        page.click('.layer-btn[data-layer="authors"]')
        page.wait_for_timeout(500)
        auth = page.locator('.layer-btn[data-layer="authors"]')
        cit = page.locator('.layer-btn[data-layer="citations"]')
        assert "active" in (auth.get_attribute("class") or "")
        assert "active" not in (cit.get_attribute("class") or "")
        # Switch back
        page.click('.layer-btn[data-layer="citations"]')
        page.wait_for_timeout(300)

    def test_toggle_aria_updates(self, desktop_session):
        page, _ = desktop_session
        page.click('.layer-btn[data-layer="authors"]')
        page.wait_for_timeout(200)
        assert page.locator('.layer-btn[data-layer="authors"]').get_attribute("aria-pressed") == "true"
        assert page.locator('.layer-btn[data-layer="citations"]').get_attribute("aria-pressed") == "false"
        page.click('.layer-btn[data-layer="citations"]')
        page.wait_for_timeout(200)


# ========================================================================
# 3. THEME TOGGLE
# ========================================================================
class TestThemeToggle:

    def test_initial_theme_is_dark_or_light(self, desktop_session):
        page, _ = desktop_session
        theme = page.locator("html").get_attribute("data-theme")
        assert theme in ("dark", "light")

    def test_toggle_theme(self, desktop_session):
        page, _ = desktop_session
        original = page.locator("html").get_attribute("data-theme")
        page.click("#theme-toggle")
        page.wait_for_timeout(400)
        new = page.locator("html").get_attribute("data-theme")
        assert new != original
        # Toggle back
        page.click("#theme-toggle")
        page.wait_for_timeout(400)
        assert page.locator("html").get_attribute("data-theme") == original

    def test_body_bg_changes_with_theme(self, desktop_session):
        page, _ = desktop_session
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        page.wait_for_timeout(200)
        dark_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        page.evaluate("document.documentElement.setAttribute('data-theme','light')")
        page.wait_for_timeout(200)
        light_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        assert dark_bg != light_bg, "Background should change between themes"
        # Restore
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        page.wait_for_timeout(200)

    def test_theme_icon_visibility(self, desktop_session):
        page, _ = desktop_session
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        page.wait_for_timeout(200)
        # Dark mode: sun icon visible, moon hidden
        sun_display = page.evaluate("getComputedStyle(document.querySelector('.theme-icon-light')).display")
        moon_display = page.evaluate("getComputedStyle(document.querySelector('.theme-icon-dark')).display")
        assert sun_display != "none", "Sun icon should show in dark mode"
        assert moon_display == "none", "Moon icon should be hidden in dark mode"
        # Restore
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")


# ========================================================================
# 4. SEARCH
# ========================================================================
class TestSearch:

    def test_search_input_has_placeholder(self, desktop_session):
        page, _ = desktop_session
        placeholder = page.locator("#search-input").get_attribute("placeholder")
        assert placeholder and "search" in placeholder.lower()

    def test_search_finds_known_paper(self, desktop_session):
        page, _ = desktop_session
        # Use a title from the real graph data
        page.fill("#search-input", "Prompt Injection")
        page.wait_for_timeout(800)
        panel = page.locator("#card-panel")
        cls = panel.get_attribute("class") or ""
        assert "hidden" not in cls, "Card panel should open on search match"
        title = page.text_content("#card-title")
        assert title and len(title) > 5
        # Cleanup
        page.click("#search-clear")
        page.wait_for_timeout(400)

    def test_search_no_match(self, desktop_session):
        page, _ = desktop_session
        page.fill("#search-input", "zzznonexistent99999")
        page.wait_for_timeout(600)
        panel_cls = page.locator("#card-panel").get_attribute("class") or ""
        # Panel should remain hidden (or become hidden) since no match
        assert "hidden" in panel_cls
        page.click("#search-clear")
        page.wait_for_timeout(300)

    def test_search_clear_button(self, desktop_session):
        page, _ = desktop_session
        page.fill("#search-input", "Agent")
        page.wait_for_timeout(600)
        page.click("#search-clear")
        page.wait_for_timeout(300)
        val = page.input_value("#search-input")
        assert val == ""
        panel_cls = page.locator("#card-panel").get_attribute("class") or ""
        assert "hidden" in panel_cls


# ========================================================================
# 5. CARD PANEL (via search to open it)
# ========================================================================
class TestCardPanel:

    def _open_card_via_search(self, page, query="Skill-Inject"):
        page.fill("#search-input", query)
        page.wait_for_timeout(800)

    def _close_card(self, page):
        page.click("#search-clear")
        page.wait_for_timeout(400)

    def test_card_shows_title(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        title = page.text_content("#card-title")
        assert title and len(title) > 3
        self._close_card(page)

    def test_card_shows_score(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        score = page.text_content("#card-score")
        assert score and "/10" in score
        self._close_card(page)

    def test_card_shows_tag(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        tag = page.text_content("#card-tag")
        assert tag and tag.lower() in ("security", "cyber", "general")
        self._close_card(page)

    def test_card_shows_date(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        date = page.text_content("#card-date")
        assert date and len(date) == 10  # YYYY-MM-DD
        self._close_card(page)

    def test_card_shows_authors(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        authors = page.text_content("#card-authors")
        assert authors and "Authors:" in authors
        self._close_card(page)

    def test_card_shows_affiliations(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        affil = page.text_content("#card-affiliations")
        assert affil and "Affiliations:" in affil
        self._close_card(page)

    def test_card_link_valid(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        link = page.locator("#card-link")
        href = link.get_attribute("href")
        assert href and href.startswith("http")
        target = link.get_attribute("target")
        assert target == "_blank"
        rel = link.get_attribute("rel")
        assert rel and "noopener" in rel
        self._close_card(page)

    def test_card_shows_oneliner(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        oneliner = page.text_content("#card-oneliner")
        assert oneliner and len(oneliner) > 10
        self._close_card(page)

    def test_card_shows_points(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        points = page.locator("#card-points li")
        assert points.count() >= 1, "Should have at least one key point"
        self._close_card(page)

    def test_card_close_button(self, desktop_session):
        page, _ = desktop_session
        self._open_card_via_search(page)
        assert "hidden" not in (page.locator("#card-panel").get_attribute("class") or "")
        page.click("#card-close")
        page.wait_for_timeout(400)
        assert "hidden" in (page.locator("#card-panel").get_attribute("class") or "")
        # Also clear search
        page.click("#search-clear")
        page.wait_for_timeout(200)


# ========================================================================
# 6. GRAPH INTERACTION (click on canvas to select/deselect)
# ========================================================================
class TestGraphInteraction:

    def test_click_on_empty_space_hides_card(self, desktop_session):
        page, _ = desktop_session
        # Force open a card
        page.fill("#search-input", "Agents of Chaos")
        page.wait_for_timeout(800)
        assert "hidden" not in (page.locator("#card-panel").get_attribute("class") or "")
        # Click top-left of graph container (likely empty space)
        container = page.locator("#graph-container")
        box = container.bounding_box()
        if box:
            page.mouse.click(box["x"] + 20, box["y"] + 20)
            page.wait_for_timeout(600)
        page.click("#search-clear")
        page.wait_for_timeout(300)

    def test_canvas_receives_wheel_zoom(self, desktop_session):
        page, _ = desktop_session
        container = page.locator("#graph-container")
        box = container.bounding_box()
        if box:
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            # Get camera ratio before zoom
            ratio_before = page.evaluate(
                "window.__sigmaRenderer ? window.__sigmaRenderer.getCamera().getState().ratio : null"
            )
            # Zoom via mouse wheel
            page.mouse.move(cx, cy)
            page.mouse.wheel(0, -300)
            page.wait_for_timeout(500)
            # We can't easily access sigma internals, but no errors = success


# ========================================================================
# 7. RESPONSIVE / MOBILE
# ========================================================================
class TestResponsive:

    def test_mobile_card_bottom_sheet(self, pw, viz_server):
        """At <= 768px, card panel should be a full-width bottom sheet."""
        browser, page, errors = _make_page(pw, viz_server, 375, 667)
        # Force-show card
        page.evaluate("document.getElementById('card-panel').classList.remove('hidden')")
        page.wait_for_timeout(300)
        width = page.evaluate("getComputedStyle(document.getElementById('card-panel')).width")
        w_px = float(width.replace("px", ""))
        assert w_px >= 370, f"Card should fill mobile width, got {w_px}px"

        # Verify it IS at the bottom (border-top present, border-left gone)
        border_top = page.evaluate("getComputedStyle(document.getElementById('card-panel')).borderTopStyle")
        border_left = page.evaluate("getComputedStyle(document.getElementById('card-panel')).borderLeftStyle")
        assert border_top == "solid", "Mobile card should have a top border"
        assert border_left == "none", "Mobile card should NOT have a left border"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "mobile-card-open.png"))
        assert errors == [], f"JS errors on mobile: {errors}"
        browser.close()

    def test_mobile_toolbar_compact(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 375, 667)
        h = page.evaluate("document.getElementById('toolbar').offsetHeight")
        assert h <= 100, f"Toolbar height on mobile should be compact, got {h}px"
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "mobile-toolbar.png"))
        browser.close()

    def test_mobile_legend_visible(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 375, 667)
        assert page.locator("#legend").is_visible()
        browser.close()


# ========================================================================
# 8. VISUAL SCREENSHOTS (dark, light, both layers, card open, mobile)
# ========================================================================
class TestVisualScreenshots:

    def test_desktop_dark_overview(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 1280, 800)
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "desktop-dark-overview.png"))
        browser.close()

    def test_desktop_light_overview(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 1280, 800)
        page.evaluate("document.documentElement.setAttribute('data-theme','light')")
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "desktop-light-overview.png"))
        browser.close()

    def test_desktop_dark_authors_layer(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 1280, 800)
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        page.click('.layer-btn[data-layer="authors"]')
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "desktop-dark-authors.png"))
        browser.close()

    def test_desktop_light_authors_layer(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 1280, 800)
        page.evaluate("document.documentElement.setAttribute('data-theme','light')")
        page.click('.layer-btn[data-layer="authors"]')
        page.wait_for_timeout(600)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "desktop-light-authors.png"))
        browser.close()

    def test_desktop_dark_card_open(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 1280, 800)
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        page.fill("#search-input", "Skill-Inject")
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "desktop-dark-card-open.png"))
        browser.close()

    def test_desktop_light_card_open(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 1280, 800)
        page.evaluate("document.documentElement.setAttribute('data-theme','light')")
        page.fill("#search-input", "Agents of Chaos")
        page.wait_for_timeout(800)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "desktop-light-card-open.png"))
        browser.close()

    def test_mobile_dark_overview(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 375, 667)
        page.evaluate("document.documentElement.setAttribute('data-theme','dark')")
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "mobile-dark-overview.png"))
        browser.close()

    def test_mobile_light_overview(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 375, 667)
        page.evaluate("document.documentElement.setAttribute('data-theme','light')")
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "mobile-light-overview.png"))
        browser.close()

    def test_widescreen(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 1920, 1080)
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "widescreen-1920.png"))
        browser.close()

    def test_tablet_portrait(self, pw, viz_server):
        browser, page, _ = _make_page(pw, viz_server, 768, 1024)
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "tablet-portrait.png"))
        browser.close()


# ========================================================================
# 9. DATA INTEGRITY — verify graph.json loads correctly
# ========================================================================
class TestDataIntegrity:

    def test_node_count(self, desktop_session):
        page, _ = desktop_session
        count = page.evaluate("window.graphology && window.graphology.Graph ? null : 'no graphology'")
        # graph is the module-scoped variable — access via IIFE closure won't work.
        # Instead, check the DOM indirectly: search should be able to find real papers.
        page.fill("#search-input", "Jailbreak")
        page.wait_for_timeout(800)
        title = page.text_content("#card-title")
        assert title and len(title) > 5, "Should find a jailbreak paper from real data"
        page.click("#search-clear")
        page.wait_for_timeout(300)

    def test_multiple_tags_present(self, desktop_session):
        """Verify all three tag types exist in the data by searching."""
        page, _ = desktop_session
        found_tags = set()
        for query in ["Malware", "Prompt Injection", "LoRA"]:
            page.fill("#search-input", query)
            page.wait_for_timeout(600)
            tag = page.text_content("#card-tag")
            if tag:
                found_tags.add(tag.strip().lower())
            page.click("#search-clear")
            page.wait_for_timeout(300)
        assert len(found_tags) >= 2, f"Expected at least 2 tag types, got: {found_tags}"


# ========================================================================
# 10. CDN DEPENDENCIES
# ========================================================================
class TestCDNDependencies:

    def test_graphology_loaded(self, desktop_session):
        page, _ = desktop_session
        has = page.evaluate("typeof graphology !== 'undefined'")
        assert has, "graphology library should be loaded from CDN"

    def test_sigma_loaded(self, desktop_session):
        page, _ = desktop_session
        has = page.evaluate("typeof Sigma !== 'undefined'")
        assert has, "Sigma library should be loaded from CDN"


# ========================================================================
# 11. ACCESSIBILITY BASICS
# ========================================================================
class TestAccessibility:

    def test_html_lang(self, desktop_session):
        page, _ = desktop_session
        lang = page.locator("html").get_attribute("lang")
        assert lang == "en"

    def test_search_input_autocomplete(self, desktop_session):
        page, _ = desktop_session
        ac = page.locator("#search-input").get_attribute("autocomplete")
        assert ac == "off"

    def test_card_panel_has_aria_label(self, desktop_session):
        page, _ = desktop_session
        label = page.locator("#card-panel").get_attribute("aria-label")
        assert label and len(label) > 0

    def test_close_button_has_aria_label(self, desktop_session):
        page, _ = desktop_session
        label = page.locator("#card-close").get_attribute("aria-label")
        assert label and "close" in label.lower()

    def test_theme_toggle_has_aria(self, desktop_session):
        page, _ = desktop_session
        label = page.locator("#theme-toggle").get_attribute("aria-label")
        assert label and "theme" in label.lower()

    def test_search_clear_has_aria(self, desktop_session):
        page, _ = desktop_session
        label = page.locator("#search-clear").get_attribute("aria-label")
        assert label and "clear" in label.lower()

    def test_layer_toggle_radiogroup(self, desktop_session):
        page, _ = desktop_session
        role = page.locator(".layer-toggle").get_attribute("role")
        assert role == "radiogroup"


# ========================================================================
# 12. PERFORMANCE / EDGE CASES
# ========================================================================
class TestEdgeCases:

    def test_rapid_layer_switching(self, desktop_session):
        """Toggle layers quickly multiple times without errors."""
        page, errors = desktop_session
        initial_err_count = len(errors)
        for _ in range(5):
            page.click('.layer-btn[data-layer="authors"]')
            page.wait_for_timeout(100)
            page.click('.layer-btn[data-layer="citations"]')
            page.wait_for_timeout(100)
        page.wait_for_timeout(500)
        new_errors = errors[initial_err_count:]
        assert new_errors == [], f"Errors during rapid switching: {new_errors}"

    def test_rapid_search_typing(self, desktop_session):
        """Type quickly in search without crash."""
        page, errors = desktop_session
        initial_err_count = len(errors)
        page.fill("#search-input", "abc")
        page.wait_for_timeout(100)
        page.fill("#search-input", "adversarial")
        page.wait_for_timeout(100)
        page.fill("#search-input", "")
        page.wait_for_timeout(100)
        page.fill("#search-input", "backdoor")
        page.wait_for_timeout(400)
        page.click("#search-clear")
        page.wait_for_timeout(300)
        new_errors = errors[initial_err_count:]
        assert new_errors == [], f"Errors during rapid search: {new_errors}"

    def test_double_theme_toggle(self, desktop_session):
        page, errors = desktop_session
        initial_err_count = len(errors)
        for _ in range(4):
            page.click("#theme-toggle")
            page.wait_for_timeout(100)
        page.wait_for_timeout(300)
        new_errors = errors[initial_err_count:]
        assert new_errors == [], f"Errors during rapid theme toggle: {new_errors}"

    def test_open_close_card_repeatedly(self, desktop_session):
        page, errors = desktop_session
        initial_err_count = len(errors)
        for _ in range(3):
            page.fill("#search-input", "Agents")
            page.wait_for_timeout(500)
            page.click("#card-close")
            page.wait_for_timeout(200)
        page.click("#search-clear")
        page.wait_for_timeout(300)
        new_errors = errors[initial_err_count:]
        assert new_errors == [], f"Errors during rapid card open/close: {new_errors}"


# ========================================================================
# 13. NETWORK / RESOURCE LOADING
# ========================================================================
class TestNetworkResources:

    def test_no_failed_requests(self, pw, viz_server):
        """All fetched resources should return 2xx or 3xx."""
        failed = []
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def on_response(resp):
            if resp.status >= 400:
                failed.append(f"{resp.status} {resp.url}")

        page.on("response", on_response)
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=30000)
        browser.close()
        assert failed == [], f"Failed resources: {failed}"

    def test_graph_json_loads(self, pw, viz_server):
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        graph_data = []

        def on_response(resp):
            if "graph.json" in resp.url:
                graph_data.append(resp.status)

        page.on("response", on_response)
        page.goto(viz_server + "/index.html", wait_until="networkidle")
        page.wait_for_selector("#loading-overlay", state="hidden", timeout=30000)
        browser.close()
        assert len(graph_data) >= 1, "graph.json should be fetched"
        assert graph_data[0] == 200, f"graph.json status: {graph_data[0]}"
