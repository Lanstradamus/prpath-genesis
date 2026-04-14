"""
App Screen Renderer — Screenshots HTML templates with Playwright.

Takes a template name + data params, loads the HTML template via a local
HTTP server, and captures a 1080x1350 PNG screenshot.

Usage:
    renderer = AppScreenRenderer()
    renderer.start()
    png_path = renderer.screenshot("strength_score", {"score": 67, "exercise": "Squat"}, "output.png")
    renderer.stop()
"""

import subprocess
import time
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

# Try playwright, fall back gracefully
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("WARNING: playwright not installed. Run: pip install playwright && playwright install chromium")


TEMPLATE_DIR = Path(__file__).parent / "templates" / "html"
WIDTH = 1080
HEIGHT = 1350
SERVER_PORT = 8889


class AppScreenRenderer:
    def __init__(self, port=SERVER_PORT):
        self.port = port
        self.server_process = None
        self.playwright = None
        self.browser = None
        self.page = None

    def start(self):
        """Start local HTTP server + Playwright browser."""
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("playwright not installed")

        # Start HTTP server for templates
        self.server_process = subprocess.Popen(
            [sys.executable, "-m", "http.server", str(self.port)],
            cwd=str(TEMPLATE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)  # Wait for server to start

        # Start Playwright
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.page = self.browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

    def stop(self):
        """Clean up browser and server."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        if self.server_process:
            self.server_process.terminate()
            self.server_process.wait()

    def screenshot(self, template_name: str, params: dict, output_path: str) -> str:
        """
        Screenshot an HTML template with the given URL params.

        Args:
            template_name: e.g. "strength_score" (without .html)
            params: dict of URL params, e.g. {"score": 67, "exercise": "Squat"}
            output_path: where to save the PNG

        Returns:
            The output_path string.
        """
        if not self.page:
            raise RuntimeError("Renderer not started. Call .start() first.")

        # Build URL
        query = urlencode(params)
        url = f"http://localhost:{self.port}/{template_name}.html?{query}"

        # Navigate and wait for render
        self.page.goto(url, wait_until="networkidle")
        self.page.wait_for_timeout(500)  # Extra time for CSS animations

        # Ensure output directory exists
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # Screenshot at exact dimensions
        self.page.screenshot(
            path=output_path,
            clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT},
        )

        return output_path

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


# Quick test
if __name__ == "__main__":
    print("Testing AppScreenRenderer...")
    with AppScreenRenderer() as renderer:
        # Test strength score
        renderer.screenshot("strength_score", {
            "score": 67,
            "exercise": "Squat",
            "bodyweight": "180 lbs",
            "weight_lifted": "306 lbs",
            "multiplier": "1.7x",
        }, "test_output/strength_score_67.png")
        print("  strength_score_67.png")

        # Test tier card
        renderer.screenshot("tier_card", {
            "tier": "Elite",
            "score": 76,
            "exercise": "Bench Press",
            "multiplier": "1.5x bodyweight",
            "weight_example": "270 lbs / 122 kg",
            "bodyweight_ref": "180 lbs",
            "show_gauge": "true",
        }, "test_output/tier_card_elite.png")
        print("  tier_card_elite.png")

        # Test cover slide
        renderer.screenshot("cover_slide", {
            "hook_text": "What's your SQUAT SCORE?",
            "accent_word": "SQUAT",
            "show_gauge_preview": "true",
        }, "test_output/cover_slide_squat.png")
        print("  cover_slide_squat.png")

        # Test CTA slide
        renderer.screenshot("cta_slide", {
            "tagline": "Track your strength score",
            "show_app_store_badge": "true",
        }, "test_output/cta_slide.png")
        print("  cta_slide.png")

        # Test muscle body map
        renderer.screenshot("muscle_body_map", {
            "view": "front",
            "title": "Push Day",
            "chest_color": "#00B0FF",
            "chest_intensity": "0.9",
            "shoulders_color": "#00B0FF",
            "shoulders_intensity": "0.7",
            "biceps_color": "#FF9500",
            "biceps_intensity": "0.5",
        }, "test_output/body_map_push.png")
        print("  body_map_push.png")

    print("All test screenshots saved to test_output/")
