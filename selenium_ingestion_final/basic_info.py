"""
Parser for AC-U-KWIK organization profile pages (/Basic-Info/{AIRPORT}/{LISTING}).

The airport page lists each FBO/handler/service briefly; its Basic-Info page
adds the vendor ID, opening hours, fuel brand and types and the fuel/credit
cards accepted. parse_basic_info() works on saved HTML only and keeps every
label/value pair, link and image so nothing on the page is lost.
"""
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from lxml import html as lxml_html

BASE_URL = "https://acukwik.com"

_BLOCK_TAGS = {
    "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol",
    "tr", "table", "tbody", "thead", "section", "br", "dt", "dd",
}
_DROP_TAGS = ("script", "style", "noscript", "textarea", "input", "select", "svg")
_CODE_PATTERN = re.compile(r"\b(ICAO|IATA|FAA)\s*-\s*([A-Z0-9]+)", re.IGNORECASE)


def _clean(text: Optional[str]) -> str:
    """Collapse whitespace (including non-breaking spaces)."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _has_class(element, name: str) -> bool:
    return name in (element.get("class") or "").split()


def _field_key(label: str) -> str:
    """Turn a page label ("Tel After Hours", "Brand:") into a snake_case key."""
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _block_text(element) -> str:
    """Text of an element with line breaks between block-level children."""
    parts: List[str] = []

    def walk(node) -> None:
        if not isinstance(node.tag, str):
            # Comments and processing instructions: keep only their tail.
            if node.tail:
                parts.append(node.tail)
            return
        block = node.tag.lower() in _BLOCK_TAGS
        if block:
            parts.append("\n")
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
        if block:
            parts.append("\n")
        if node.tail:
            parts.append(node.tail)

    walk(element)
    lines = (_clean(line) for line in "".join(parts).split("\n"))
    return "\n".join(line for line in lines if line)


def _absolute(url: Optional[str], base_url: str) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if url.lower().startswith(("javascript:", "#")):
        return None
    return urljoin(base_url, url)


def _content_root(document):
    """The profile content, without the review form or page chrome."""
    for xpath in (
        '//div[contains(concat(" ", normalize-space(@class), " "), " basicInfoContentWrap ")]',
        '//div[contains(@id, "_VDC_ContentPane")]',
        "//body",
    ):
        found = document.xpath(xpath)
        if found:
            return found[0]
    return document


def _strip_noise(root) -> None:
    """Remove scripts, form controls, the comments/review panel and ad slots."""
    for element in root.xpath(
        './/*[contains(@id, "WriteReview_divComments") or starts-with(@id, "div-gpt-ad")]'
    ):
        element.drop_tree()
    for tag in _DROP_TAGS:
        for element in root.xpath(f".//{tag}"):
            element.drop_tree()


def _value_details(cell, base_url: str) -> Dict[str, Any]:
    """Text plus any links or buttons inside a value cell."""
    details: Dict[str, Any] = {"value": _clean(cell.text_content())}
    urls = [
        _absolute(anchor.get("href"), base_url)
        for anchor in cell.xpath(".//a[@href]")
    ]
    urls = [url for url in urls if url]
    if urls:
        details["urls"] = urls
    buttons = []
    for button in cell.xpath(".//button"):
        attributes = {
            key: value for key, value in button.attrib.items()
            if key in ("class", "data-id", "data-service")
        }
        attributes["text"] = _clean(button.text_content())
        buttons.append(attributes)
    if buttons:
        details["buttons"] = buttons
    return details


def _section_for(label_element, root) -> Optional[str]:
    """Nearest heading that labels a field (the listing block has none)."""
    for ancestor in label_element.iterancestors():
        if _has_class(ancestor, "listingMainInfo"):
            return "Listing"
        if ancestor is root:
            break
    headings = label_element.xpath("preceding::h2[1]")
    if headings and root in headings[0].iterancestors():
        return _clean(headings[0].text_content()) or None
    return None


def _extract_raw_fields(root, base_url: str) -> List[Dict[str, Any]]:
    """Every bold label followed by a value cell, in page order."""
    fields: List[Dict[str, Any]] = []
    for label_element in root.xpath(
        './/div[contains(concat(" ", normalize-space(@class), " "), " bold ")]'
    ):
        value_element = label_element.getnext()
        if value_element is None or value_element.tag != "div":
            continue
        if label_element.xpath(".//a"):
            # A bold link line ("All Airport Data for ...") is not a label.
            continue
        label = _clean(label_element.text_content()).rstrip(":").strip()
        if not label:
            continue
        field = {"label": label}
        field.update(_value_details(value_element, base_url))
        section = _section_for(label_element, root)
        if section:
            field["section"] = section
        fields.append(field)
    return fields


def _split_list(value: str) -> List[str]:
    return [item.strip() for item in re.split(r"\s*,\s*", value) if item.strip()]


def parse_basic_info(source_html: str, url: Optional[str] = None) -> Dict[str, Any]:
    """
    Parse a Basic-Info profile page.

    Args:
        source_html: The saved page HTML
        url: The page URL (used to resolve relative links)

    Returns:
        Profile dictionary. ``name`` is None when the page is not a profile
        (for example a login or error page), which callers treat as a failure.
    """
    base_url = url or BASE_URL
    document = lxml_html.fromstring(source_html)

    vendor_inputs = document.xpath('//input[contains(@name, "hfVendor_ID")]')
    vendor_id = None
    if vendor_inputs:
        vendor_id = (vendor_inputs[0].get("value") or "").strip() or None

    root = _content_root(document)
    main = root.xpath('.//div[contains(concat(" ", normalize-space(@class), " "), " listingMainInfo ")]')
    main = main[0] if main else root

    name = None
    rating = None
    headings = main.xpath(".//h1")
    if headings:
        name = _clean(headings[0].text_content()) or None
        rated = headings[0].xpath('.//*[@data-rateit-value]')
        if rated:
            rating = rated[0].get("data-rateit-value")
    location = None
    location_headings = main.xpath(".//h2")
    if location_headings:
        location = _clean(location_headings[0].text_content()) or None
    codes_line = None
    codes: Dict[str, str] = {}
    code_headings = main.xpath(".//h3")
    if code_headings:
        codes_line = _clean(code_headings[0].text_content()) or None
        for code_type, code in _CODE_PATTERN.findall(codes_line or ""):
            codes[code_type.lower()] = code.upper()

    comments_count = None
    comment_titles = document.xpath(
        '//*[contains(@id, "WriteReview_divComments")]'
        '//div[contains(concat(" ", normalize-space(@class), " "), " bluePanelTitle ")]'
    )
    if comment_titles:
        match = re.search(r"\d+", comment_titles[0].text_content())
        if match:
            comments_count = int(match.group(0))

    # Card logos carry the card name only in their alt text.
    fuel_cards: List[str] = []
    media: List[Dict[str, Any]] = []
    for image in root.xpath(".//img"):
        src = _absolute(image.get("src"), base_url)
        alt = _clean(image.get("alt"))
        in_cards = any(
            _has_class(ancestor, "fuelCards") or _has_class(ancestor, "cards")
            for ancestor in image.iterancestors()
        )
        if in_cards and alt and alt not in fuel_cards:
            fuel_cards.append(alt)
        media.append({"src": src, "alt": alt, **({"context": "fuel_cards"} if in_cards else {})})

    # Other FBOs at the same airport, each with its ground-handler ID.
    additional_fbos: List[Dict[str, Any]] = []
    for handler_input in root.xpath('.//input[contains(@name, "hfGHID")]'):
        block = handler_input.getparent()
        names = block.xpath(".//strong")
        details = [
            _clean(child.text_content()) for child in block.xpath("./div")
            if not child.xpath(".//strong")
        ]
        additional_fbos.append({
            "name": _clean(names[0].text_content()) if names else None,
            "ground_handler_id": (handler_input.get("value") or "").strip() or None,
            "details": " ".join(detail for detail in details if detail) or None,
            "raw_text": _block_text(block),
        })

    # Work on a copy so raw_text and links never include review/ads/scripts.
    content = lxml_html.fromstring(lxml_html.tostring(root, encoding="unicode"))
    _strip_noise(content)

    raw_fields = _extract_raw_fields(content, base_url)

    attributes: Dict[str, Any] = {}
    for field in raw_fields:
        key = _field_key(field["label"])
        if field.get("section") not in (None, "Listing"):
            section_key = _field_key(field["section"])
            if not key.startswith(section_key):
                key = f"{section_key}_{key}"
        if key in attributes:
            existing = attributes[key]
            values = existing if isinstance(existing, list) else [existing]
            if field["value"] not in values:
                attributes[key] = values + [field["value"]]
        else:
            attributes[key] = field["value"]

    def first(*labels: str) -> Optional[str]:
        wanted = {label.lower() for label in labels}
        for field in raw_fields:
            if field["label"].lower() in wanted and field["value"]:
                return field["value"]
        return None

    links: List[Dict[str, Any]] = []
    for anchor in content.xpath(".//a[@href]"):
        link_url = _absolute(anchor.get("href"), base_url)
        if not link_url:
            continue
        links.append({"text": _clean(anchor.text_content()), "url": link_url})

    website = None
    for field in raw_fields:
        if field["label"].lower() == "website" and field.get("urls"):
            website = field["urls"][0]
            break

    email_buttons = [
        button for field in raw_fields for button in field.get("buttons", [])
        if button.get("data-id")
    ]

    fuel_types_value = first("Fuel types", "Fuel type")
    fuel_tables = []
    for table in content.xpath('.//*[contains(concat(" ", normalize-space(@class), " "), " fuelTable ")] | .//table'):
        rows = []
        for row in table.xpath('.//tr | ./div[contains(@class, "clearfix")]'):
            cells = [_clean(cell.text_content()) for cell in row.xpath("./td | ./th | ./div")]
            if any(cells):
                rows.append(cells)
        if rows:
            fuel_tables.append(rows)

    services: Dict[str, List[str]] = {}
    for block_class, key in (("BGAS", "business_general_aviation"), ("Ramp_Services", "ramp"), ("AdminOps", "admin_operations")):
        items = [
            _clean(item.text_content())
            for item in content.xpath(f'.//*[contains(concat(" ", normalize-space(@class), " "), " {block_class} ")]//li')
        ]
        items = [item for item in items if item]
        if items:
            services[key] = items

    fees = [
        _block_text(fee)
        for fee in content.xpath('.//div[contains(concat(" ", normalize-space(@class), " "), " fee ")]')
    ]
    fees = [fee for fee in fees if fee]

    profile: Dict[str, Any] = {
        "url": url,
        "name": name,
        "vendor_id": vendor_id,
        "location": location,
        "codes_line": codes_line,
        "airport_codes": codes,
        "address": first("Address"),
        "phone": first("Phone", "Tel"),
        "phone_after_hours": first("Tel After Hours", "After Hours"),
        "fax": first("Fax"),
        "website": website,
        "frequency": first("Frequency"),
        "hours": first("Hours"),
        "fuel_brand": first("Fuel brand", "Brand"),
        "fuel_types": _split_list(fuel_types_value) if fuel_types_value else [],
        "fuel_cards_accepted": fuel_cards,
        "rating": rating,
        "comments_count": comments_count,
        "source_identifiers": {"hfVendor_ID": vendor_id} if vendor_id else {},
        "attributes": attributes,
        "raw_fields": raw_fields,
        "links": links,
        "media": media,
        "raw_text": _block_text(content),
    }
    if email_buttons:
        profile["email_buttons"] = email_buttons
    if fuel_tables:
        profile["fuel_tables"] = fuel_tables
    if services:
        profile["services"] = services
    if fees:
        profile["fees"] = fees
    if additional_fbos:
        profile["additional_fbos"] = additional_fbos
    return profile


def is_basic_info_url(url: Optional[str]) -> bool:
    """True for an AC-U-KWIK organization profile URL."""
    if not url or "/basic-info/" not in url.lower():
        return False
    return url.startswith("/") or bool(re.match(r"https?://(www\.)?acukwik\.com/", url, re.IGNORECASE))
