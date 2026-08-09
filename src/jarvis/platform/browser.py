import asyncio
from pathlib import Path

from playwright.async_api import (
    BrowserContext,
    Locator,
    Page,
    Playwright,
    async_playwright,
)

from jarvis.agency.browser import (
    BrowserPage,
    BrowserTarget,
    ClickBrowser,
    FillBrowser,
    InspectBrowser,
    NavigateBrowser,
)


class PlaywrightBrowser:
    """Owns one isolated, persistent browser context for structured operation."""

    def __init__(
        self,
        profile_directory: Path,
        *,
        channel: str = "msedge",
        headless: bool = False,
    ) -> None:
        self._profile_directory = profile_directory
        self._channel = channel
        self._headless = headless
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    async def navigate(self, command: NavigateBrowser) -> BrowserPage:
        async with self._lock:
            page = await self._page()
            await page.goto(
                command.url,
                wait_until=command.wait_until,
                timeout=command.timeout_seconds * 1_000,
            )
            return await self._snapshot(page, 12_000)

    async def inspect(self, command: InspectBrowser) -> BrowserPage:
        async with self._lock:
            return await self._snapshot(await self._page(), command.max_characters)

    async def click(self, command: ClickBrowser) -> BrowserPage:
        async with self._lock:
            page = await self._page()
            await self._locator(page, command.target).click(timeout=command.timeout_seconds * 1_000)
            return await self._snapshot(page, 12_000)

    async def fill(self, command: FillBrowser) -> BrowserPage:
        async with self._lock:
            page = await self._page()
            await self._locator(page, command.target).fill(
                command.value,
                timeout=command.timeout_seconds * 1_000,
            )
            return await self._snapshot(page, 12_000)

    async def close(self) -> None:
        async with self._lock:
            context, playwright = self._context, self._playwright
            self._context = None
            self._playwright = None
            if context is not None:
                await context.close()
            if playwright is not None:
                await playwright.stop()

    async def _page(self) -> Page:
        if self._context is None:
            self._profile_directory.mkdir(parents=True, exist_ok=True)
            playwright = await async_playwright().start()
            try:
                context = await playwright.chromium.launch_persistent_context(
                    self._profile_directory,
                    channel=self._channel,
                    headless=self._headless,
                    viewport={"width": 1_440, "height": 900},
                )
            except BaseException:
                await playwright.stop()
                raise
            self._playwright = playwright
            self._context = context
        if self._context.pages:
            return self._context.pages[0]
        return await self._context.new_page()

    @staticmethod
    def _locator(page: Page, target: BrowserTarget) -> Locator:
        if target.kind == "role":
            assert target.role is not None
            return page.get_by_role(target.role, name=target.value, exact=target.exact)
        if target.kind == "label":
            return page.get_by_label(target.value, exact=target.exact)
        if target.kind == "placeholder":
            return page.get_by_placeholder(target.value, exact=target.exact)
        return page.get_by_text(target.value, exact=target.exact)

    @staticmethod
    async def _snapshot(page: Page, max_characters: int) -> BrowserPage:
        text = (await page.locator("body").inner_text())[:max_characters]
        return BrowserPage(url=page.url, title=await page.title(), text=text)


class InMemoryBrowser:
    """Behavioral fake for tests and callers that do not own a real browser."""

    def __init__(self) -> None:
        self._page = BrowserPage(url="about:blank", title="", text="")
        self.values: dict[tuple[str, str], str] = {}
        self.closed = False

    async def navigate(self, command: NavigateBrowser) -> BrowserPage:
        self._page = BrowserPage(url=command.url, title=command.url, text="")
        return self._page

    async def inspect(self, command: InspectBrowser) -> BrowserPage:
        return self._page.model_copy(update={"text": self._page.text[: command.max_characters]})

    async def click(self, command: ClickBrowser) -> BrowserPage:
        return self._page

    async def fill(self, command: FillBrowser) -> BrowserPage:
        self.values[(command.target.kind, command.target.value)] = command.value
        return self._page

    async def close(self) -> None:
        self.closed = True
