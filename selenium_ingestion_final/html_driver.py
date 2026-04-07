"""Lightweight HTML driver adapter for parsing cached pages without Selenium."""

from pathlib import Path
from typing import Callable, List, Optional

from lxml import html as lxml_html
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By


def _css_to_xpath(selector: str) -> str:
    """Best-effort selector conversion for a small subset of CSS queries."""
    selector = selector.strip()
    if selector.startswith(".") and " " not in selector and ":" not in selector and "[" not in selector:
        class_name = selector[1:]
        return (
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            f"' {class_name} ')]"
        )
    if selector.startswith("#") and " " not in selector and ":" not in selector and "[" not in selector:
        return f".//*[@id='{selector[1:]}']"
    return selector


class HtmlElement:
    """lxml-backed element with a Selenium-like API."""

    def __init__(self, element):
        self._element = element

    @property
    def text(self) -> str:
        return self._element.text_content()

    @property
    def tag_name(self) -> str:
        return self._element.tag

    def get_attribute(self, name: str) -> Optional[str]:
        if name in {"textContent", "innerText"}:
            return self._element.text_content()
        return self._element.get(name)

    def value_of_css_property(self, name: str) -> str:
        if name == "display":
            style = (self._element.get("style") or "").lower().replace(" ", "")
            if "display:none" in style:
                return "none"
            return "block"
        return ""

    def find_elements(self, by=By.CSS_SELECTOR, value: Optional[str] = None):
        return _find_elements(self._element, by, value)

    def find_element(self, by=By.CSS_SELECTOR, value: Optional[str] = None):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(f"Element not found: {by}={value}")
        return found[0]

    def click(self) -> None:
        return None

    def clear(self) -> None:
        return None

    def send_keys(self, *_args, **_kwargs) -> None:
        return None

    def is_displayed(self) -> bool:
        return self.value_of_css_property("display") != "none"


def _find_elements(root, by, value):
    if value is None:
        value = by
        by = By.CSS_SELECTOR

    if by == By.CSS_SELECTOR:
        matches = root.cssselect(value)
    elif by == By.XPATH:
        matches = root.xpath(value)
    elif by == By.TAG_NAME:
        tag = value.strip()
        if getattr(root, "tag", None) == tag:
            matches = [root]
        else:
            matches = root.xpath(f".//{tag}")
    elif by == By.ID:
        matches = root.xpath(f".//*[@id='{value}']")
    elif by == By.CLASS_NAME:
        matches = root.xpath(
            ".//*[contains(concat(' ', normalize-space(@class), ' '), "
            f"' {value} ')]"
        )
    else:
        matches = root.xpath(_css_to_xpath(value))

    wrapped = []
    for match in matches:
        if getattr(match, "tag", None) is not None:
            wrapped.append(HtmlElement(match))
    return wrapped


class HtmlDriver:
    """Minimal Selenium-like driver for cached HTML documents."""

    def __init__(self, source_html: str, current_url: str = "about:blank", email_resolver: Optional[Callable] = None):
        self.current_url = current_url
        self.email_resolver = email_resolver
        self._set_html(source_html)

    @classmethod
    def from_file(
        cls,
        html_path: str | Path,
        current_url: str = "about:blank",
        email_resolver: Optional[Callable] = None,
    ) -> "HtmlDriver":
        html_file = Path(html_path)
        source_html = html_file.read_text(encoding="utf-8")
        return cls(source_html, current_url=current_url, email_resolver=email_resolver)

    def _set_html(self, source_html: str) -> None:
        self.page_source = source_html
        self._document = lxml_html.fromstring(source_html)
        self._root = HtmlElement(self._document)

    def get(self, url: str) -> None:
        if url.startswith("file://"):
            self._set_html(Path(url[7:]).read_text(encoding="utf-8"))
            self.current_url = url
            return
        raise NotImplementedError("HtmlDriver only supports cached local file URLs")

    def find_elements(self, by=By.CSS_SELECTOR, value: Optional[str] = None):
        return self._root.find_elements(by, value)

    def find_element(self, by=By.CSS_SELECTOR, value: Optional[str] = None):
        return self._root.find_element(by, value)

    def execute_script(self, *_args, **_kwargs):
        return None

    def save_screenshot(self, *_args, **_kwargs) -> bool:
        return False

    def implicitly_wait(self, *_args, **_kwargs) -> None:
        return None

    def set_page_load_timeout(self, *_args, **_kwargs) -> None:
        return None

    def quit(self) -> None:
        return None

    @property
    def switch_to(self):
        class _SwitchTo:
            def default_content(self_inner):
                return None

            def frame(self_inner, *_args, **_kwargs):
                return None

        return _SwitchTo()
