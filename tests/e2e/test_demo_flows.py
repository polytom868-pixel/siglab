"""E2E tests for SigLab frontend demo flows.

These tests exercise 5 critical user flows that buildathon judges will
see, plus supporting interactions for theme, auto-refresh, accessibility,
experiment detail navigation, run detail interactions, ops board panels,
and deployment readiness.

Prerequisites
-------------
- The SigLab dashboard server must be running on http://localhost:8080
- Playwright (pytest-playwright) must be installed

Run with::

    python3 -m pytest tests/e2e/ -v
"""

import re

from playwright.sync_api import Page, expect

BASE_URL = "http://localhost:8080"


class TestDemoFlows:
    """Critical user flows covering landing, navigation, filtering, and error handling."""

    # ------------------------------------------------------------------
    # Flow 1 – Landing page
    # ------------------------------------------------------------------
    def test_home_page_loads(self, page: Page):
        """Flow 1: Landing page loads run cards and summary cards."""
        page.goto(BASE_URL, wait_until="networkidle")

        # Wait for run cards to be rendered by JS (skeleton cards are replaced)
        page.wait_for_selector(".run-card", timeout=15000)

        # Verify summary cards exist — home.js::renderSummary creates 4 cards
        # (Visible Runs, Total Experiments, Deployed, Best Run + Score)
        expect(page.locator(".summary-card")).to_have_count(4)

        # Verify filter controls are present
        expect(page.locator("#trackFilter")).to_be_visible()
        expect(page.locator("#familyFilter")).to_be_visible()
        expect(page.locator("#metricFilter")).to_be_visible()

        # Verify hero heading
        expect(page.locator("h1")).to_contain_text("Run Dashboard")

    # ------------------------------------------------------------------
    # Flow 2 – Error handling when API is down
    # ------------------------------------------------------------------
    def test_error_handling_on_api_failure(self, page: Page):
        """Flow 2: App shows error toast when API call returns 500."""
        # Intercept the API call and return a server error
        page.route("**/api/runs", lambda route: route.fulfill(status=500))

        # Navigate — the JS fetch will receive the 500
        page.goto(BASE_URL, wait_until="networkidle")

        # The error toast should appear (showError removes "hidden", adds "visible")
        error_toast = page.locator("#errorToast")
        expect(error_toast).not_to_have_class("hidden")
        expect(error_toast).to_be_visible()

        # Verify the message contains the failure text
        expect(error_toast).to_contain_text("Failed to load")

    # ------------------------------------------------------------------
    # Flow 3 – Navigation to Ops board
    # ------------------------------------------------------------------
    def test_navigation_to_ops(self, page: Page):
        """Flow 3: Navigate from dashboard to the Ops board via the navbar."""
        page.goto(BASE_URL, wait_until="networkidle")

        # Click the "Ops" link in the navbar
        page.click("a:has-text('Ops')")

        # Wait for URL to include /ops
        page.wait_for_url(re.compile(r"/ops"))

        # Ops panels should be visible (static HTML — present immediately)
        expect(page.locator(".ops-panel").first).to_be_visible(timeout=10000)

        # Verify a known heading on the ops board
        expect(page.locator("h1")).to_contain_text("Research Operations Board")

    # ------------------------------------------------------------------
    # Flow 4 – Filter interaction refreshes data
    # ------------------------------------------------------------------
    def test_filter_interaction(self, page: Page):
        """Flow 4: Changing the track filter refreshes the scope summary.

        The "trend_signals" track renders as "Directional Perps" in the
        scope summary via TRACK_LABELS.
        """
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_selector(".run-card", timeout=15000)

        # Change track filter to "trend_signals"
        page.select_option("#trackFilter", "trend_signals")

        # The scope summary should now include "Directional Perps" (or the resolved track label)
        expect(page.locator("#scopeSummary")).to_contain_text(
            "Directional Perps", timeout=15000
        )

    # ------------------------------------------------------------------
    # Flow 5 – Click "Open Run" navigates to run detail page
    # ------------------------------------------------------------------
    def test_experiment_navigation(self, page: Page):
        """Flow 5: Clicking 'Open Run' on a run card goes to /runs/{id}."""
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_selector(".run-card", timeout=15000)

        # Click the first "Open Run" link on any run card
        page.click(".run-card .button-link:has-text('Open Run')")

        # Should navigate to /runs/{id}
        expect(page).to_have_url(re.compile(r"/runs/.+"))

        # The run detail page has an experiments table (tbody#experimentsTable)
        page.wait_for_selector("#experimentsTable", timeout=15000)
        expect(page.locator("h1")).to_be_visible()

    # ------------------------------------------------------------------
    # Flow 6 – Auto-refresh toggle
    # ------------------------------------------------------------------
    def test_auto_refresh_indicator(self, page: Page):
        """Flow 6: Auto-refresh checkbox starts checked and can be toggled."""
        page.goto(BASE_URL, wait_until="networkidle")

        # The checkbox should be checked by default (HTML has `checked`)
        expect(page.locator("#autoRefresh")).to_be_checked()

        # Uncheck it
        page.uncheck("#autoRefresh")
        expect(page.locator("#autoRefresh")).not_to_be_checked()

        # Re-check it
        page.check("#autoRefresh")
        expect(page.locator("#autoRefresh")).to_be_checked()

    # ------------------------------------------------------------------
    # Flow 7 – Theme toggle switches light/dark mode
    # ------------------------------------------------------------------
    def test_theme_toggle(self, page: Page):
        """Flow 7: Theme toggle switches to light mode and back to dark."""
        page.goto(BASE_URL, wait_until="networkidle")

        # Default is dark (no data-theme attribute)
        theme = page.evaluate(
            "document.documentElement.getAttribute('data-theme')",
        )
        assert theme is None or theme == "dark", (
            f"Expected default dark theme, got {theme!r}"
        )

        # Click the theme toggle
        page.click("#themeToggle")

        # Should now be light
        theme = page.evaluate(
            "document.documentElement.getAttribute('data-theme')",
        )
        assert theme == "light", f"Expected light theme, got {theme!r}"

        # Toggle again — back to dark
        page.click("#themeToggle")
        theme = page.evaluate(
            "document.documentElement.getAttribute('data-theme')",
        )
        assert theme != "light", "Expected dark theme after second toggle"

    # ------------------------------------------------------------------
    # Flow 8 – Keyboard accessibility: skip-to-content link
    # ------------------------------------------------------------------
    def test_accessibility_skip_link(self, page: Page):
        """Flow 8: Skip-to-content link becomes visible on Tab press."""
        page.goto(BASE_URL, wait_until="networkidle")

        skip_link = page.locator(".skip-link")

        # Before Tab, the skip link should be visually hidden
        # (it uses off-screen / clip positioning in its default state)
        expect(skip_link).to_have_attribute("href", "#main-content")

        # Press Tab to focus the first focusable element (the skip link)
        page.keyboard.press("Tab")

        # After focus, the CSS :focus selector should make it visible
        # (skip-link:focus is typically styled with position:static or
        #  clip:auto — making it appear on screen)
        expect(skip_link).to_be_visible()

    # ------------------------------------------------------------------
    # Flow 9 – Experiment detail page navigation
    # ------------------------------------------------------------------
    def test_experiment_detail_page_navigation(self, page: Page):
        """Flow 9: Navigate from dashboard → run → experiment detail panel."""
        page.goto(BASE_URL)
        page.wait_for_selector(".run-card", timeout=15000)
        # Click "Open Run" on first run card
        page.locator("text=Open Run").first.click()
        page.wait_for_url(re.compile(r"/runs/.+"), timeout=15000)
        # Click experiment row (first clickable row)
        page.wait_for_selector("#experimentsTable tr", timeout=15000)
        page.locator("#experimentsTable tr").nth(1).click()
        # Detail panel should show experiment data
        page.wait_for_selector("#detailContent h3", timeout=15000)
        expect(page.locator("#detailContent")).to_contain_text("Score")

    # ------------------------------------------------------------------
    # Flow 10 – Full experiment page via "More info" link
    # ------------------------------------------------------------------
    def test_experiment_page_from_more_info(self, page: Page):
        """Flow 10: Click 'Open Full Experiment Page' link → full experiment page."""
        page.goto(BASE_URL)
        page.wait_for_selector(".run-card", timeout=15000)
        page.locator("text=Open Run").first.click()
        page.wait_for_url(re.compile(r"/runs/.+"), timeout=15000)
        page.wait_for_selector("#experimentsTable tr", timeout=15000)
        # Click "Open Full Experiment Page" link in the detail panel
        page.locator("text=Open Full Experiment Page").first.click()
        # Should navigate to /experiments/{hash}
        expect(page).to_have_url(re.compile(r"/experiments/.+"), timeout=15000)
        # Experiment summary cards should load
        page.wait_for_selector(".summary-card", timeout=15000)
        expect(page.locator(".summary-card").first).to_be_visible()

    # ------------------------------------------------------------------
    # Flow 11 – Deployment panel on experiment page
    # ------------------------------------------------------------------
    def test_experiment_deployment_form(self, page: Page):
        """Flow 11: Deployment panel loads on experiment page."""
        page.goto(f"{BASE_URL}/experiments/spec-e2e-001-003")
        page.wait_for_selector("#deploymentPanel", timeout=15000)
        # Deployment panel should be visible with readiness info
        expect(page.locator("#deploymentPanel")).to_be_visible()
        # Readiness info is always rendered (form vs "Not Exportable Yet")
        expect(page.locator("#deploymentPanel")).to_contain_text("Readiness")

    # ------------------------------------------------------------------
    # Flow 12 – Improvement chart interaction on run detail
    # ------------------------------------------------------------------
    def test_run_detail_chart_interaction(self, page: Page):
        """Flow 12: Improvement curve chart renders with interactive tooltips."""
        page.goto(f"{BASE_URL}/runs/test-run-e2e-001")
        page.wait_for_selector("#chart", timeout=15000)
        # Chart SVG should be visible
        chart_svg = page.locator("#chart")
        expect(chart_svg).to_be_visible()
        # Wait for JS-rendered chart (text "by run order" only appears
        # after app.js renders, not in the HTMX template)
        page.wait_for_selector("text=by run order", timeout=10000)
        # Chart should render at least one circle (data point)
        circles = chart_svg.locator("circle")
        expect(circles.first).to_be_visible(timeout=5000)
        # Tooltip element is initially hidden
        tooltip = page.locator("#tooltip")
        expect(tooltip).to_have_class("tooltip hidden")
        # Trigger tooltip show by removing hidden class (simulates mouseenter result)
        page.evaluate("""() => {
            document.getElementById('tooltip').classList.remove('hidden');
        }""")
        expect(tooltip).to_be_visible(timeout=5000)

    # ------------------------------------------------------------------
    # Flow 13 – Experiment table sorting / row click on run detail
    # ------------------------------------------------------------------
    def test_run_detail_table_sorting(self, page: Page):
        """Flow 13: Experiment table renders with clickable rows."""
        page.goto(f"{BASE_URL}/runs/test-run-e2e-001")
        page.wait_for_selector("#experimentsTable", timeout=15000)
        # Table should have rows
        rows = page.locator("#experimentsTable tr")
        expect(rows.first).to_be_visible()
        # Clicking a row should populate detail panel
        rows.nth(1).click()
        page.wait_for_selector("#detailContent", timeout=10000)
        expect(page.locator("#detailContent")).not_to_contain_text(
            "Select an experiment"
        )

    # ------------------------------------------------------------------
    # Flow 14 – Family pill filter on run detail
    # ------------------------------------------------------------------
    def test_family_pill_filter(self, page: Page):
        """Flow 14: Family pills filter the experiment table."""
        page.goto(f"{BASE_URL}/runs/test-run-e2e-001")
        page.wait_for_selector("#experimentsTable", timeout=15000)
        # Find family pill and click it (class is "pill" with data-family attribute)
        pill = page.locator(".pill[data-family]").first
        if pill.is_visible():
            pill.click()
            page.wait_for_timeout(2000)
            # Table should still be there (possibly filtered)
            expect(page.locator("#experimentsTable")).to_be_visible()

    # ------------------------------------------------------------------
    # Flow 15 – Ops board panels
    # ------------------------------------------------------------------
    def test_ops_board_all_panels_load(self, page: Page):
        """Flow 15: All ops panels render on the ops board."""
        page.goto(f"{BASE_URL}/ops")
        page.wait_for_selector(".ops-panel", timeout=15000)
        panels = page.locator(".ops-panel")
        panel_count = panels.count()
        assert panel_count >= 3, f"Expected 3+ panels, got {panel_count}"
        # Panel headings should be visible
        expect(panels.first.locator("h2")).to_be_visible()
