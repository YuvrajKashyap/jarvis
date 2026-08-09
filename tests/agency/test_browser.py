import importlib
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from jarvis.agency.capabilities import CapabilityContext
from jarvis.agency.policy import RiskClass

NOW = datetime(2026, 8, 9, 2, 0, tzinfo=UTC)


class StatefulBrowser:
    def __init__(self, browser_module) -> None:
        self._browser = browser_module
        self.url = "about:blank"
        self.title = ""
        self.text = ""

    async def navigate(self, command):
        self.url = command.url
        self.title = "JARVIS test page"
        self.text = "Ready"
        return self._snapshot()

    async def inspect(self, command):
        return self._snapshot(max_characters=command.max_characters)

    async def click(self, command):
        self.text = f"Clicked {command.target.value}"
        return self._snapshot()

    async def fill(self, command):
        self.text = f"Filled {command.target.value}"
        return self._snapshot()

    def _snapshot(self, *, max_characters: int = 6_000):
        return self._browser.BrowserPage(
            url=self.url,
            title=self.title,
            text=self.text[:max_characters],
        )


def context() -> CapabilityContext:
    return CapabilityContext(
        invocation_id=UUID("019fd977-1d96-7892-950c-6afbb71f7cf0"),
        device_id="desktop",
        requested_at=NOW,
    )


def test_browser_navigation_rejects_active_url_schemes() -> None:
    browser = importlib.import_module("jarvis.agency.browser")

    with pytest.raises(ValidationError, match="http or https"):
        browser.NavigateBrowser(url="javascript:alert(1)")


async def test_browser_inspection_is_bounded_observation() -> None:
    browser = importlib.import_module("jarvis.agency.browser")
    driver = StatefulBrowser(browser)
    driver.text = "a" * 250
    capability = browser.InspectBrowserCapability(driver)

    result = await capability.execute(browser.InspectBrowser(max_characters=200), context())

    assert capability.metadata.risk is RiskClass.OBSERVE
    assert result.text == "a" * 200


@pytest.mark.parametrize(
    ("capability_name", "command", "expected_text"),
    [
        (
            "NavigateBrowserCapability",
            lambda browser: browser.NavigateBrowser(url="https://example.com"),
            "Ready",
        ),
        (
            "ClickBrowserCapability",
            lambda browser: browser.ClickBrowser(
                target=browser.BrowserTarget(kind="role", role="button", value="Continue")
            ),
            "Clicked Continue",
        ),
        (
            "FillBrowserCapability",
            lambda browser: browser.FillBrowser(
                target=browser.BrowserTarget(kind="label", value="Email"),
                value="person@example.com",
            ),
            "Filled Email",
        ),
    ],
)
async def test_browser_mutations_require_fresh_external_approval(
    capability_name: str,
    command,
    expected_text: str,
) -> None:
    browser = importlib.import_module("jarvis.agency.browser")
    driver = StatefulBrowser(browser)
    capability = getattr(browser, capability_name)(driver)

    result = await capability.execute(command(browser), context())

    assert capability.metadata.risk is RiskClass.EXTERNAL_IRREVERSIBLE
    assert result.text == expected_text
