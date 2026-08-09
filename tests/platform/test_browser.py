import importlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from jarvis.agency.browser import (
    BrowserTarget,
    ClickBrowser,
    FillBrowser,
    InspectBrowser,
    NavigateBrowser,
)
from jarvis.platform.browser import InMemoryBrowser


class BrowserTestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"""<!doctype html>
<html><head><title>Browser adapter</title></head>
<body><main>Ready</main><form action='/submitted'>
<label>Email <input name='email'></label><button>Continue</button>
</form></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return None


@pytest.mark.integration
async def test_playwright_browser_operates_local_page_through_accessible_locators(tmp_path) -> None:
    platform_browser = importlib.import_module("jarvis.platform.browser")
    server = ThreadingHTTPServer(("127.0.0.1", 0), BrowserTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = platform_browser.PlaywrightBrowser(
        profile_directory=tmp_path / "profile",
        channel="msedge",
        headless=True,
    )
    try:
        page = await browser.navigate(
            NavigateBrowser(url=f"http://127.0.0.1:{server.server_port}/")
        )
        assert page.title == "Browser adapter"
        assert "Ready" in page.text

        await browser.fill(
            FillBrowser(
                target=BrowserTarget(kind="label", value="Email"),
                value="person@example.com",
            )
        )
        submitted = await browser.click(
            ClickBrowser(target=BrowserTarget(kind="role", role="button", value="Continue"))
        )

        assert urlsplit(submitted.url).path == "/submitted"
        assert parse_qs(urlsplit(submitted.url).query) == {"email": ["person@example.com"]}
        assert (
            await browser.inspect(InspectBrowser(max_characters=200))
        ).title == "Browser adapter"
    finally:
        await browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def test_in_memory_browser_mirrors_the_structured_contract() -> None:
    browser = InMemoryBrowser()
    page = await browser.navigate(NavigateBrowser(url="https://example.test/path"))
    await browser.fill(
        FillBrowser(
            target=BrowserTarget(kind="label", value="Email"),
            value="person@example.test",
        )
    )

    assert (
        await browser.click(ClickBrowser(target=BrowserTarget(kind="text", value="Go")))
    ) == page
    assert (await browser.inspect(InspectBrowser(max_characters=200))) == page
    assert browser.values[("label", "Email")] == "person@example.test"

    await browser.close()

    assert browser.closed is True
