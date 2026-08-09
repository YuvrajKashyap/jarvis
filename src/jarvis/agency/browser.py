from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jarvis.agency.capabilities import CapabilityContext, CapabilityMetadata
from jarvis.agency.policy import RiskClass


class BrowserValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NavigateBrowser(BrowserValue):
    url: str = Field(min_length=1, max_length=2_048)
    wait_until: Literal["commit", "domcontentloaded", "load"] = "domcontentloaded"
    timeout_seconds: float = Field(default=30, ge=1, le=60)

    @field_validator("url")
    @classmethod
    def require_web_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("browser navigation requires an http or https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("browser navigation URL cannot contain credentials")
        return value


BrowserRole = Literal[
    "button",
    "checkbox",
    "combobox",
    "dialog",
    "link",
    "menuitem",
    "option",
    "radio",
    "searchbox",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
]


class BrowserTarget(BrowserValue):
    kind: Literal["role", "label", "placeholder", "text"]
    value: str = Field(min_length=1, max_length=240)
    role: BrowserRole | None = None
    exact: bool = True

    @model_validator(mode="after")
    def require_role_only_for_role_target(self) -> "BrowserTarget":
        if self.kind == "role" and self.role is None:
            raise ValueError("role targets require an accessibility role")
        if self.kind != "role" and self.role is not None:
            raise ValueError("accessibility role is valid only for role targets")
        return self


class InspectBrowser(BrowserValue):
    max_characters: int = Field(default=6_000, ge=200, le=12_000)


class ClickBrowser(BrowserValue):
    target: BrowserTarget
    timeout_seconds: float = Field(default=15, ge=1, le=30)


class FillBrowser(BrowserValue):
    target: BrowserTarget
    value: str = Field(max_length=16_000)
    timeout_seconds: float = Field(default=15, ge=1, le=30)


class BrowserPage(BrowserValue):
    url: str = Field(max_length=2_048)
    title: str = Field(max_length=1_000)
    text: str = Field(max_length=12_000)


class BrowserController(Protocol):
    async def navigate(self, command: NavigateBrowser) -> BrowserPage: ...

    async def inspect(self, command: InspectBrowser) -> BrowserPage: ...

    async def click(self, command: ClickBrowser) -> BrowserPage: ...

    async def fill(self, command: FillBrowser) -> BrowserPage: ...


class InspectBrowserCapability:
    metadata = CapabilityMetadata(
        name="browser.inspect",
        description="Read the URL, title, and bounded visible text from JARVIS's managed browser",
        risk=RiskClass.OBSERVE,
        timeout_seconds=20,
        reversible=False,
    )
    input_model = InspectBrowser
    output_model = BrowserPage

    def __init__(self, browser: BrowserController) -> None:
        self._browser = browser

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> BrowserPage:
        return await self._browser.inspect(InspectBrowser.model_validate(arguments))


class NavigateBrowserCapability:
    metadata = CapabilityMetadata(
        name="browser.navigate",
        description="Navigate JARVIS's managed browser to one explicit HTTP or HTTPS URL",
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        timeout_seconds=65,
        reversible=False,
    )
    input_model = NavigateBrowser
    output_model = BrowserPage

    def __init__(self, browser: BrowserController) -> None:
        self._browser = browser

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> BrowserPage:
        return await self._browser.navigate(NavigateBrowser.model_validate(arguments))


class ClickBrowserCapability:
    metadata = CapabilityMetadata(
        name="browser.click",
        description=(
            "Click one element in JARVIS's managed browser using an accessible role, label, "
            "placeholder, or visible text"
        ),
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        timeout_seconds=35,
        reversible=False,
    )
    input_model = ClickBrowser
    output_model = BrowserPage

    def __init__(self, browser: BrowserController) -> None:
        self._browser = browser

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> BrowserPage:
        return await self._browser.click(ClickBrowser.model_validate(arguments))


class FillBrowserCapability:
    metadata = CapabilityMetadata(
        name="browser.fill",
        description=(
            "Fill one form control in JARVIS's managed browser using an accessible locator; "
            "this does not submit the form"
        ),
        risk=RiskClass.EXTERNAL_IRREVERSIBLE,
        timeout_seconds=35,
        reversible=False,
    )
    input_model = FillBrowser
    output_model = BrowserPage

    def __init__(self, browser: BrowserController) -> None:
        self._browser = browser

    async def execute(self, arguments: BaseModel, context: CapabilityContext) -> BrowserPage:
        return await self._browser.fill(FillBrowser.model_validate(arguments))
