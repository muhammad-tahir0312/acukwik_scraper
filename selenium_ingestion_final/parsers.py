"""
ETL-compliant parsers for aviation data extraction.
Focuses on raw data ingestion without transformation.
"""
import logging
import re
import time
import json
import os
import hashlib
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from lxml import html as lxml_html
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from roles import SECTION_ROLES, normalize_section_title

logger = logging.getLogger(__name__)


def extract_field(driver: WebDriver, selector: str, field_name: str, 
                 observed_fields: List[str], missing_fields: List[str], 
                 errors: List[Dict], by: str = By.CSS_SELECTOR) -> Optional[str]:
    """
    Safe field extraction with observability tracking.
    
    Args:
        driver: Selenium WebDriver
        selector: CSS selector or XPath
        field_name: Name of the field for tracking
        observed_fields: List to append successful extractions
        missing_fields: List to append failed extractions
        errors: List to append error details
        by: Selector type (By.CSS_SELECTOR or By.XPATH)
        
    Returns:
        Extracted text or None
    """
    try:
        element = driver.find_element(by, selector)
        if element and element.text.strip():
            value = element.text.strip()
            observed_fields.append(field_name)
            return value
        else:
            missing_fields.append(field_name)
            return None
    except Exception as e:
        errors.append({
            "field": field_name,
            "error": str(e),
            "selector": selector
        })
        missing_fields.append(field_name)
        return None


def clean_airport_name(raw_name: str) -> str:
    """
    Remove ICAO/IATA prefixes from airport name.
    
    Example: "UBBB - Baku Heydar Aliyev International" → "Baku Heydar Aliyev International"
    """
    # Remove pattern: ICAO - 
    cleaned = re.sub(r'^[A-Z]{4}\s*-\s*', '', raw_name)
    return cleaned.strip()


def generate_external_id(entity_type: str, data: Dict[str, Any], airport_icao: Optional[str] = None) -> str:
    """
    Generate deterministic external IDs.

    Airport:      acukwik_UBBB  (ICAO preferred, then FAA ID, then IATA, then hash)
    Organization occurrence: acukwik_org_<listing>_<airport>_<role>
    """
    if entity_type == "airport":
        icao = data.get('icao')
        if icao:
            return f"acukwik_{icao}"
        faa_id = data.get('faa_id')
        if faa_id:
            return f"acukwik_faa_{faa_id}"
        iata = data.get('iata')
        if iata:
            return f"acukwik_iata_{iata}"
        source_airport_id = data.get('source_airport_id')
        if source_airport_id:
            safe_source_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(source_airport_id)).strip("_")
            if safe_source_id:
                return f"acukwik_source_{safe_source_id}"
        # Last resort: hash
        serialized = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:20]
        return f"acukwik_{digest}"

    elif entity_type == "organization":
        listing_key = data.get("source_listing_key")
        if not listing_key:
            identity = "|".join([
                airport_icao or "",
                data.get("source_profile_url") or "",
                data.get("source_listing_id") or "",
                data.get("name") or "",
            ])
            listing_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        role_key = "-".join(sorted(data.get("roles") or ["ORGANIZATION"]))
        role_key = re.sub(r"[^A-Z0-9_-]", "_", role_key.upper())
        return f"acukwik_org_{listing_key}_{airport_icao or 'UNKNOWN'}_{role_key}"
    
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(serialized.encode('utf-8')).hexdigest()[:20]
    return f"acukwik_{entity_type}_{digest}"


def determine_scrape_status(observed_fields: List[str], errors: List[Dict]) -> str:
    """
    Determine scrape status based on observed fields and errors.

    SUCCESS: Core fields present, no errors
    PARTIAL: Some core fields missing
    FAILED: Critical errors or no core fields

    Airports may be identified by ICAO, IATA, or FAA ID — any one is acceptable.
    """
    if errors:
        return "FAILED"

    observed = set(observed_fields)
    # At least one identifier + name → SUCCESS
    has_identifier = bool(observed & {"icao", "iata", "faa_id"})
    has_name = "name" in observed

    if has_identifier and has_name:
        return "SUCCESS"

    return "PARTIAL"


def validate_and_clean(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post-extraction validation and cleaning.
    
    - Converts empty strings to None
    - Uppercases airport codes
    - Removes email placeholders like "Show Email"
    - Normalizes phone formats
    """
    # Convert empty strings to None
    for key, value in list(data.items()):
        if value == "":
            data[key] = None
    
    # Uppercase airport codes
    if data.get('icao'):
        data['icao'] = data['icao'].upper()
    if data.get('iata'):
        data['iata'] = data['iata'].upper()
    
    # Remove "Show Email" placeholders
    if data.get('email') in ["Show Email", "show email", "Show email"]:
        data['email'] = None
    
    # Clean website URLs
    if data.get('website') and not data['website'].startswith('http'):
        # Invalid website, set to None
        data['website'] = None
    
    return data


# Elements whose boundaries a browser renders as line breaks.  Used to build a
# Selenium-like ``.text`` from lxml, whose text_content() glues blocks together.
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
})
_NON_TEXT_TAGS = frozenset({"script", "style", "noscript", "template"})

# Hidden ASP.NET inputs carrying AC-U-KWIK source IDs on a listing.
_LISTING_HIDDEN_ID_FIELDS = ("hfAcukwik_ID", "hfGROUND_HANDLER_ID", "hfSupplier_ID", "hfVendor_ID", "hfHaveADV")

_CLASS_XPATH = "contains(concat(' ', normalize-space(@class), ' '), ' {} ')"


def block_text(element) -> str:
    """Return an element's visible text with one line per block element.

    Selenium's ``.text`` already behaves this way.  The lxml-backed HtmlDriver
    returns text_content(), which glues adjacent blocks ("SITADXBMX7X") and
    includes <script> bodies, so walk the lxml tree directly when available.
    """
    lxml_element = getattr(element, "_element", None)
    if lxml_element is None:
        raw = element.text or ""
    else:
        parts: List[str] = []

        def walk(node) -> None:
            tag = node.tag if isinstance(node.tag, str) else None
            if tag is None or tag.lower() in _NON_TEXT_TAGS:
                return
            is_block = tag.lower() in _BLOCK_TAGS
            if is_block:
                parts.append("\n")
            if node.text:
                parts.append(node.text)
            for child in node:
                walk(child)
                if child.tail:
                    parts.append(child.tail)
            if is_block:
                parts.append("\n")

        walk(lxml_element)
        raw = "".join(parts)
    lines = (" ".join(line.split()) for line in raw.splitlines())
    return "\n".join(line for line in lines if line)


def element_tail_text(driver, element) -> str:
    """Return the text node directly following ``element`` (lxml ``.tail``)."""
    lxml_element = getattr(element, "_element", None)
    if lxml_element is not None:
        return " ".join((lxml_element.tail or "").split())
    try:
        tail = driver.execute_script(
            "var n = arguments[0].nextSibling; var t = '';"
            "while (n && n.nodeType === 3) { t += n.textContent; n = n.nextSibling; }"
            "return t;",
            element,
        )
    except Exception:
        return ""
    return " ".join((tail or "").split())


class AirportPageParser:
    """
    Parser for acukwik.com airport pages.
    Extracts both airport entity and all organization entities (FBOs, hotels, etc.)
    """
    
    def __init__(self, driver: WebDriver):
        self.driver = driver
        self.wait = WebDriverWait(driver, 10)
    
    def parse(self, url: str) -> List[Dict[str, Any]]:
        """
        Parse airport page and return multiple entities.
        
        Returns:
            List of entities (1 airport + N organizations)
        """
        logger.info(f"Parsing airport page: {url}")
        
        entities = []
        
        # Parse airport entity
        try:
            airport_entity = self._parse_airport_entity(url)
            entities.append(airport_entity)
            
            # Use the source airport identifier when a location has no ICAO.
            airport_data = airport_entity.get('data', {})
            airport_reference = airport_data.get('icao') or airport_data.get('source_airport_id')
            
            # Parse organization entities on the same page
            org_entities = self._parse_organization_entities(url, airport_reference)
            entities.extend(org_entities)
            self._attach_panel_counts(airport_entity)

        except Exception as e:
            logger.error(f"Error parsing airport page: {str(e)}")
            # Return error entity
            entities.append({
                "entity_type": "airport",
                "external_id": f"acukwik_error_{hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]}",
                "url": url,
                "scrape_status": "FAILED",
                "data": {},
                "observed_fields": [],
                "missing_fields": [],
                "errors": [{"error": str(e)}]
            })
        
        return entities
    
    def _parse_airport_entity(self, url: str) -> Dict[str, Any]:
        """Extract airport entity from page."""
        
        observed_fields = []
        missing_fields = []
        errors = []
        
        # Wait for page load
        try:
            self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        except TimeoutException:
            errors.append({"field": "page_load", "error": "Page load timeout"})
        
        # Extract basic info from headers
        icao = self._extract_icao(observed_fields, missing_fields, errors)
        iata = self._extract_iata(observed_fields, missing_fields, errors)
        faa_id = self._extract_faa_id(observed_fields, missing_fields, errors)
        raw_name = self._extract_raw_name(observed_fields, missing_fields, errors)
        name = clean_airport_name(raw_name) if raw_name else None
        city, country = self._extract_city_country(observed_fields, missing_fields, errors)
        source_match = re.search(r"/Airport-Info/([^/?#]+)", url, re.IGNORECASE)
        source_airport_id = source_match.group(1) if source_match else None
        if source_airport_id:
            observed_fields.append("source_airport_id")

        # Expand the 'More Airport Information' section and extract ALL fields (basic + expanded)
        # _expand_more_info_section handles both expanding AND scraping all fields
        additional_data = self._expand_more_info_section(observed_fields, missing_fields, errors)

        # Build data dictionary
        data = {
            "icao": icao,
            "iata": iata,
            "faa_id": faa_id,
            "source_airport_id": source_airport_id,
            "name": name,
            "city": city,
            "country": country,
        }
        
        # Add additional data fields
        data.update(additional_data)

        # Extract Airport Restrictions and Information contact table
        restrictions = self._extract_restrictions_table(observed_fields, missing_fields, errors)
        if restrictions:
            data["airport_contacts_raw"] = restrictions

        customs_info = self._extract_customs_info(observed_fields, missing_fields, errors)
        if customs_info:
            data["customs_info"] = customs_info

        data.update(self._extract_diagram_urls(url, observed_fields))
        
        # Validate and clean
        data = validate_and_clean(data)
        
        # Remove None values (don't include unobserved fields)
        data = {k: v for k, v in data.items() if v is not None}
        
        # Generate external ID
        external_id = generate_external_id("airport", data)
        
        # Determine status
        scrape_status = determine_scrape_status(observed_fields, errors)
        
        return {
            "entity_type": "airport",
            "external_id": external_id,
            "url": url,
            "scrape_status": scrape_status,
            "data": data,
            "observed_fields": list(set(observed_fields)),
            "missing_fields": list(set(missing_fields)),
            "errors": errors
        }
    
    def _extract_icao(self, observed: List, missing: List, errors: List) -> Optional[str]:
        """Extract ICAO code from h3 tag (format: ICAO - UBBB, IATA - GYD)."""
        try:
            h3 = self.driver.find_element(By.CSS_SELECTOR, "h3")
            h3_text = h3.text
            
            if "ICAO -" in h3_text:
                icao_start = h3_text.find("ICAO -") + len("ICAO - ")
                icao_end = h3_text.find(",", icao_start) if "," in h3_text[icao_start:] else len(h3_text)
                icao = h3_text[icao_start:icao_end].strip().upper()
                
                if icao and len(icao) == 4:
                    observed.append("icao")
                    return icao
                else:
                    missing.append("icao")
                    return None
            else:
                missing.append("icao")
                return None
                
        except Exception as e:
            errors.append({"field": "icao", "error": str(e)})
            missing.append("icao")
            return None
    
    def _extract_iata(self, observed: List, missing: List, errors: List) -> Optional[str]:
        """Extract IATA code from h3 tag."""
        try:
            h3 = self.driver.find_element(By.CSS_SELECTOR, "h3")
            h3_text = h3.text

            if "IATA -" in h3_text:
                iata_start = h3_text.find("IATA -") + len("IATA - ")
                # IATA may be followed by a comma (e.g. "IATA - GYD, FAA - ...")
                iata_raw = h3_text[iata_start:]
                iata = iata_raw.split(",")[0].strip().upper()

                if iata and len(iata) == 3:
                    observed.append("iata")
                    return iata
                else:
                    missing.append("iata")
                    return None
            else:
                missing.append("iata")
                return None

        except Exception as e:
            errors.append({"field": "iata", "error": str(e)})
            missing.append("iata")
            return None

    def _extract_faa_id(self, observed: List, missing: List, errors: List) -> Optional[str]:
        """
        Extract FAA ID from the h3 tag.
        acukwik shows FAA ID for airports that lack an ICAO code, typically US/domestic airports.
        Expected format examples in h3:
          "FAA - KORD"  or  "ICAO - KORD, FAA - KORD"  or  "FAA ID - 5NY8"
        """
        try:
            h3 = self.driver.find_element(By.CSS_SELECTOR, "h3")
            h3_text = h3.text

            # Matches both "FAA -" and "FAA ID -" labels
            faa_match = re.search(r'FAA(?:\s+ID)?\s*-\s*([A-Z0-9]{2,7})', h3_text, re.IGNORECASE)
            if faa_match:
                faa_id = faa_match.group(1).strip().upper()
                observed.append("faa_id")
                return faa_id
            else:
                missing.append("faa_id")
                return None

        except Exception as e:
            errors.append({"field": "faa_id", "error": str(e)})
            missing.append("faa_id")
            return None
    
    def _extract_raw_name(self, observed: List, missing: List, errors: List) -> Optional[str]:
        """Extract raw airport name from h1 tag (may contain ICAO prefix)."""
        try:
            h1 = self.driver.find_element(By.CSS_SELECTOR, "h1")
            name = h1.text.strip()
            
            if name:
                observed.append("name")
                return name
            else:
                missing.append("name")
                return None
                
        except Exception as e:
            errors.append({"field": "name", "error": str(e)})
            missing.append("name")
            return None
    
    def _extract_city_country(self, observed: List, missing: List, errors: List) -> tuple:
        """Extract city and country from h2 tag (format: Located in Baku, AZERBAIJAN)."""
        try:
            h2 = self.driver.find_element(By.CSS_SELECTOR, "h2")
            h2_text = h2.text.strip()
            
            # Remove "Located in " prefix
            h2_text = h2_text.replace("Located in ", "")
            
            if "," in h2_text:
                parts = h2_text.split(",", 1)
                city = parts[0].strip()
                country = parts[1].strip()
                
                if city:
                    observed.append("city")
                else:
                    missing.append("city")
                    city = None
                
                if country:
                    observed.append("country")
                else:
                    missing.append("country")
                    country = None
                
                return city, country
            else:
                missing.append("city")
                missing.append("country")
                return None, None
                
        except Exception as e:
            errors.append({"field": "city_country", "error": str(e)})
            missing.append("city")
            missing.append("country")
            return None, None
    
    def _expand_more_info_section(self, observed: List, missing: List, errors: List) -> Dict[str, Any]:
        """Click the 'More Airport Information' accordion to expand it if collapsed."""
        try:
            # Find the panel title for 'More Airport Information'
            panel_title = self.driver.find_element(
                By.XPATH,
                "//div[contains(@class,'bluePanelTitle') and contains(.,'More Airport Information')]"
            )
            # Find the corresponding content panel (next sibling)
            panel_content = panel_title.find_element(By.XPATH, "following-sibling::*[1]")
            # Check if the content is hidden (collapsed)
            is_hidden = panel_content.value_of_css_property("display") in ["none", "hidden"]
            if is_hidden and getattr(self.driver, "supports_interaction", True):
                logger.info("Expanding 'More Airport Information' section")
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", panel_title)
                self.driver.execute_script("arguments[0].click();", panel_title)
                time.sleep(1)
        except Exception:
            pass  # Already expanded or not found — continue

        # Full field mapping — basic (always visible) + expanded section
        field_mapping = {
            # Basic visible fields (div.clearboth.p3xp.bold / div.clearboth.p3px structure)
            "Airport Type": "airport_type",
            "Lat/Long": "coordinates_raw",
            "Elevation (ft)": "elevation_raw",
            "Fuel Available": "fuel_available",
            "Current UTC": "utc_offset",
            "Local Standard Time": "local_standard_time",
            "Approaches": "approaches",
            "Longest Primary Runway (ft)": "longest_runway_raw",
            "Runway Surface": "runway_surface",
            "PCN": "pcn",
            # Expanded section fields (div.clearboth.table / .fl.bold / .fl structure)
            "Airport Light Intensity": "airport_light_intensity",
            "Airport of Entry": "airport_of_entry",
            "Airport of Entry Remarks": "airport_of_entry_remarks",
            "Fire Category": "fire_category",
            "Fire Category Remarks": "fire_category_remarks",
            "Customs": "customs",
            "US Customs Pre-Clearance": "us_customs_pre_clearance",
            "Slots Required": "slots_required",
            "Handling Mandatory": "handling_mandatory",
            "Airport Email": "airport_email",
            "Airport Website": "airport_website",
            "Airport Manager Phone": "airport_manager_phone",
            "Airport Ownership": "airport_ownership",
            "Facility Use": "facility_use",
            "DST": "dst",
            "Sunrise": "sunrise",
            "Sunset": "sunset",
            "Open 24 Hours": "open_24h",
            "Airport Hours": "airport_hours",
            "Control Tower Hours": "control_tower_hours",
            "Variation": "variation",
            "Distance from City": "distance_from_city",
            "AFS/AFTN": "afs_aftn",
            "Tower Frequency": "tower_frequency",
            "ATIS Frequency": "atis_frequency",
            "CTAF Frequency": "ctaf_frequency",
            "Unicom Frequency": "unicom_frequency",
            "Airport General Remarks": "airport_general_remarks",
        }

        boolean_fields = {
            "open_24h", "customs", "slots_required", "handling_mandatory",
            "airport_of_entry", "us_customs_pre_clearance",
        }

        data = {}
        raw_fields: List[Dict[str, Any]] = []

        def _process_pair(label_text: str, value_elem) -> None:
            """Map a label/value DOM pair into the data dict."""
            label = label_text.strip().replace("\xa0", "")
            value = value_elem.text.strip().replace("\xa0", "")
            links = []
            for link in value_elem.find_elements(By.TAG_NAME, "a"):
                href = link.get_attribute("href")
                if href:
                    links.append({"text": link.text.strip(), "url": href})
            raw_entry: Dict[str, Any] = {"label": label, "value": value}
            if links:
                raw_entry["links"] = links
            if label and raw_entry not in raw_fields:
                raw_fields.append(raw_entry)

            field_name = field_mapping.get(label)
            if not field_name:
                return

            if field_name in boolean_fields:
                data[field_name] = bool(value and value.lower() not in ["no", "false", "n/a", ""])
                observed.append(field_name)
                if field_name == "open_24h" and value:
                    # The bool turns "Restricted" into True; keep the source value.
                    data["open_24h_raw"] = value
                    observed.append("open_24h_raw")
                return
            else:
                if value:
                    # UTC offset: strip time portion e.g. "9:10:16 AM (0.00)" -> "0.00"
                    if field_name == "utc_offset" and "(" in value:
                        offset_match = re.search(r'\(([+-]?\d+\.\d+)\)', value)
                        if offset_match:
                            value = offset_match.group(1)
                    # Airport Email: handle button click to reveal email
                    if field_name == "airport_email":
                        resolver = getattr(self.driver, "email_resolver", None)
                        if callable(resolver):
                            try:
                                email = resolver(value_elem.find_element(By.CSS_SELECTOR, "button.aEmail"))
                                if email:
                                    data[field_name] = email
                                    observed.append(field_name)
                                    return
                            except Exception as e:
                                logger.debug(f"Cached airport_email resolution failed: {e}")
                        # Cached HTML cannot execute the site's JavaScript email
                        # reveal action. Avoid a full WebDriverWait for an action
                        # that can never mutate an HtmlDriver document.
                        if not getattr(self.driver, "supports_interaction", True):
                            try:
                                link = value_elem.find_element(By.TAG_NAME, "a")
                                email = link.get_attribute("href")
                                if email and email.startswith("mailto:"):
                                    data[field_name] = email.replace("mailto:", "")
                                    observed.append(field_name)
                                else:
                                    missing.append(field_name)
                            except Exception:
                                missing.append(field_name)
                            return
                        try:
                            # Check if there's a button to click
                            button = value_elem.find_element(By.CSS_SELECTOR, "button.aEmail")
                            # Use JavaScript click to ensure it works
                            self.driver.execute_script("arguments[0].click();", button)
                            # Wait for the a tag to appear
                            self.wait.until(lambda driver: value_elem.find_elements(By.TAG_NAME, "a"))
                            # Now find the a tag
                            link = value_elem.find_element(By.TAG_NAME, "a")
                            email = link.get_attribute("href")
                            if email and email.startswith("mailto:"):
                                data[field_name] = email.replace("mailto:", "")
                                observed.append(field_name)
                            else:
                                missing.append(field_name)
                        except Exception as e:
                            logger.warning(f"Failed to extract airport_email via button click: {e}")
                            # Take screenshot for debugging
                            screenshot_path = f"screenshot_airport_email_button_{int(time.time())}.png"
                            self.driver.save_screenshot(screenshot_path)
                            logger.info(f"Screenshot saved: {screenshot_path}")
                            # Try to get href directly if already revealed
                            try:
                                link = value_elem.find_element(By.TAG_NAME, "a")
                                email = link.get_attribute("href")
                                if email and email.startswith("mailto:"):
                                    data[field_name] = email.replace("mailto:", "")
                                    observed.append(field_name)
                                else:
                                    missing.append(field_name)
                            except Exception as e2:
                                logger.warning(f"Failed to extract airport_email directly: {e2}")
                                # Take another screenshot
                                screenshot_path2 = f"screenshot_airport_email_direct_{int(time.time())}.png"
                                self.driver.save_screenshot(screenshot_path2)
                                logger.info(f"Screenshot saved: {screenshot_path2}")
                                missing.append(field_name)
                        return
                    # Airport Website: grab actual href from <a> tag
                    if field_name == "airport_website":
                        try:
                            link = value_elem.find_element(By.TAG_NAME, "a")
                            href = link.get_attribute("href")
                            if href and href.startswith("http"):
                                value = href
                        except Exception:
                            pass
                    data[field_name] = value
                    observed.append(field_name)
                else:
                    missing.append(field_name)

        try:
            # ── Basic fields ────────────────────────────────────────────────────────
            # Structure: <div class="clearboth p3xp bold">Label</div>
            #            <div class="clearboth p3px">Value</div>  (siblings, alternating)
            basic_labels = self.driver.find_elements(By.CSS_SELECTOR, "div.clearboth.p3xp.bold")
            basic_values = self.driver.find_elements(By.CSS_SELECTOR, "div.clearboth.p3px:not(.bold)")
            logger.info(f"Basic section: {len(basic_labels)} labels, {len(basic_values)} values")
            for lbl, val in zip(basic_labels, basic_values):
                try:
                    _process_pair(lbl.text, val)
                except Exception as e:
                    logger.debug(f"Error in basic field: {e}")

            # ── Expanded section fields ──────────────────────────────────────────────
            # Structure: <div class="clearboth table">
            #                <div class="wXXp fl bold">Label</div>
            #                <div class="wXXp fl">Value</div>
            #            </div>
            table_rows = self.driver.find_elements(By.CSS_SELECTOR, "div.clearboth.table")
            logger.info(f"Expanded section: {len(table_rows)} table rows")
            for row in table_rows:
                try:
                    lbl_elem = row.find_element(By.CSS_SELECTOR, ".fl.bold")
                    val_elem = row.find_element(By.CSS_SELECTOR, ".fl:not(.bold)")
                    _process_pair(lbl_elem.text, val_elem)
                except Exception as e:
                    logger.debug(f"Error in expanded row: {e}")

            # ── Airport General Remarks (own container) ──────────────────────────────
            # Structure: <div class="fl w27p bold">Airport General Remarks</div>
            #            <div class="fl w73p">…text…</div>
            try:
                rem_label = self.driver.find_element(By.CSS_SELECTOR, "div.fl.w27p.bold")
                rem_value = self.driver.find_element(By.CSS_SELECTOR, "div.fl.w73p")
                _process_pair(rem_label.text, rem_value)
            except Exception:
                pass  # Remarks section absent on this page

            if raw_fields:
                data["airport_fields_raw"] = raw_fields
                observed.append("airport_fields_raw")

            logger.info(f"Extracted {len(data)} fields")

        except Exception as e:
            errors.append({"field": "additional_data", "error": str(e)})
            logger.error(f"Error in _expand_more_info_section: {e}", exc_info=True)

        return data
    
    def _extract_restrictions_table(self, observed: List, missing: List, errors: List) -> Optional[List[Dict]]:
        """
        Extract the 'Airport Restrictions and Information' contact table.
        Returns a list of dicts: [{section, frequency, phone, fax, email, website}, ...]
        """
        contacts = []
        try:
            # FNLU renders this section as result rows instead of a table.
            section_container = None
            try:
                heading = self.driver.find_element(
                    By.XPATH,
                    "//div[contains(@class,'h1') and contains(normalize-space(.),'Airport Restrictions and Information')]"
                )
                section_container = heading.find_element(By.XPATH, "./ancestor::div[contains(@class,'mb44px')][1]")
            except Exception:
                section_container = None

            section_rows = []
            if section_container:
                section_rows = section_container.find_elements(By.CSS_SELECTOR, ".results-content .clearfix.result")

            # Fallbacks for alternate layouts.
            if not section_rows:
                section_rows = self.driver.find_elements(
                    By.XPATH,
                    "//table[.//th[contains(text(),'Airport Information') or contains(text(),'Restrictions')]]//tr[position()>1]"
                    " | //div[contains(@class,'restrictionRow') or contains(@class,'restriction-row')]"
                )
            if not section_rows:
                section_rows = self.driver.find_elements(
                    By.XPATH,
                    "//*[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'restrictions and information')]"
                    "/following-sibling::table[1]//tr[position()>1]"
                )

            base_url = getattr(self.driver, "current_url", "") or ""
            for row in section_rows:
                try:
                    entry = self._parse_restriction_row(row, base_url)
                    if entry.get("section") or entry.get("phone") or entry.get("email"):
                        contacts.append(entry)
                except Exception as e:
                    logger.debug(f"Restriction row parse error: {e}")
                    continue

            if contacts:
                observed.append("airport_contacts_raw")
            else:
                missing.append("airport_contacts_raw")
        except Exception as e:
            errors.append({"field": "airport_contacts_raw", "error": str(e)})

        return contacts if contacts else None
  
    @staticmethod
    def _parse_restriction_row(row, base_url: str = "") -> Dict[str, Any]:
        """Parse one restrictions row: [section, frequency, phone/fax, email/website].

        Only the row's direct children are columns; the phone/fax column nests
        its own ``div.w30p`` label cells, which a descendant selector would
        mistake for columns.
        """
        entry: Dict[str, Any] = {}
        cells = row.find_elements(By.XPATH, "./div | ./td")

        if cells:
            section = " ".join(cells[0].text.split())
            if section:
                entry["section"] = section

        if len(cells) > 1:
            freq = " ".join(cells[1].text.split())
            if freq:
                entry["frequency"] = freq

        if len(cells) > 2:
            numbers: List[Dict[str, str]] = []
            for pair in cells[2].find_elements(By.XPATH, ".//div[contains(concat(' ', normalize-space(@class), ' '), ' clearfix ')]"):
                parts = [
                    " ".join(part.text.split())
                    for part in pair.find_elements(By.XPATH, "./div")
                ]
                parts = [part for part in parts if part]
                if len(parts) >= 2:
                    numbers.append({"label": parts[0].rstrip(":"), "value": " ".join(parts[1:])})
            phones = [n["value"] for n in numbers if re.match(r"(?:phone|tel)", n["label"], re.IGNORECASE)]
            faxes = [n["value"] for n in numbers if re.match(r"fax", n["label"], re.IGNORECASE)]
            if numbers:
                entry["contact_numbers"] = numbers
                if phones:
                    entry["phone"] = phones[0]
                    entry["phones"] = phones
                if faxes:
                    entry["fax"] = faxes[0]
                    entry["faxes"] = faxes
            else:
                pf_text = " ".join(cells[2].text.split())
                phone_match = re.search(r'(?:Phone|Tel)[:\s]+([+\d\s\-().]+)', pf_text, re.IGNORECASE)
                fax_match = re.search(r'Fax[:\s]+([+\d\s\-().]+)', pf_text, re.IGNORECASE)
                if phone_match:
                    entry["phone"] = phone_match.group(1).strip()
                if fax_match:
                    entry["fax"] = fax_match.group(1).strip()
                if not phone_match and not fax_match and pf_text:
                    entry["contact_raw"] = pf_text

        if len(cells) > 3:
            emails: List[str] = []
            websites: List[str] = []
            for link in cells[3].find_elements(By.TAG_NAME, "a"):
                href = (link.get_attribute("href") or "").strip()
                if href.lower().startswith("mailto:"):
                    email = href[7:].split("?", 1)[0].strip()
                    if email and email not in emails:
                        emails.append(email)
                elif href:
                    website = urljoin(base_url, href) if base_url else href
                    if website.startswith("http") and website not in websites:
                        websites.append(website)
            if emails:
                entry["email"] = emails[0]
                entry["emails"] = emails
            if websites:
                entry["website"] = websites[0]
                entry["websites"] = websites
            ew_text = " ".join(cells[3].text.split())
            if not emails and not websites and ew_text:
                entry["email_website_raw"] = ew_text

        return entry

    def _extract_customs_info(self, observed: List, missing: List, errors: List) -> Optional[Dict[str, Any]]:
        """Extract the 'Customs Information' panel: labelled rows plus free-text remarks."""
        try:
            panels = self.driver.find_elements(By.XPATH, "//div[contains(@id,'pnlCustoms')]")
            if not panels:
                return None
            panel = panels[0]
            info: Dict[str, Any] = {}
            fields: List[Dict[str, str]] = []
            for pair in panel.find_elements(By.XPATH, ".//div[contains(concat(' ', normalize-space(@class), ' '), ' clearfix ')]"):
                label_elems = pair.find_elements(By.XPATH, "./div[contains(concat(' ', normalize-space(@class), ' '), ' bold ')]")
                value_elems = pair.find_elements(By.XPATH, "./div[not(contains(concat(' ', normalize-space(@class), ' '), ' bold '))]")
                if not label_elems:
                    continue
                label = " ".join(label_elems[0].text.split()).rstrip(":").strip()
                value = " ".join(" ".join(elem.text.split()) for elem in value_elems).strip()
                if not label and not value:
                    continue
                fields.append({"label": label, "value": value})
                key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
                if key and value and key not in info:
                    info[key] = value

            # Remarks: unlabelled div.clearboth blocks, excluding the title and the
            # container that holds the labelled rows.
            remarks = []
            for block in panel.find_elements(
                By.XPATH,
                ".//div[contains(concat(' ', normalize-space(@class), ' '), ' clearboth ')]"
                "[not(.//strong[contains(@class,'fs18px')])]"
                "[not(.//div[contains(concat(' ', normalize-space(@class), ' '), ' clearfix ')])]",
            ):
                text = block_text(block)
                if text:
                    remarks.append(text)
            if remarks:
                info["remarks"] = "\n".join(remarks)
            if fields:
                info["fields"] = fields
            if not info:
                missing.append("customs_info")
                return None
            info["raw_text"] = "\n".join(
                line for line in block_text(panel).splitlines() if line != "Customs Information"
            )
            observed.append("customs_info")
            return info
        except Exception as e:
            errors.append({"field": "customs_info", "error": str(e)})
            return None

    def _extract_diagram_urls(self, url: str, observed: List) -> Dict[str, Any]:
        """Runway and FAA diagram links (absolute URLs)."""
        found: Dict[str, Any] = {}
        targets = (
            ("runway_diagram_url", "//a[contains(@id,'hlRunwayDiagramIMG')]", "href"),
            ("faa_diagram_url", "//a[contains(@id,'hlFAADiagramIMG')]", "href"),
            ("faa_diagram_img_src", "//img[contains(@id,'imgFAADiagram')]", "src"),
        )
        for field_name, xpath, attribute in targets:
            try:
                elements = self.driver.find_elements(By.XPATH, xpath)
                value = (elements[0].get_attribute(attribute) or "").strip() if elements else ""
            except Exception:
                value = ""
            if value:
                found[field_name] = urljoin(url, value)
                observed.append(field_name)
        return found

    def _extract_additional_data(self, observed: List, missing: List, errors: List) -> Dict[str, Any]:
        """
        Extract additional airport data from paired divs.
        CRITICAL: Store as raw text, NO transformation.
        """
        data = {}
        
        try:
            # Find all label/value pairs
            labels = self.driver.find_elements(By.CSS_SELECTOR, "div.clearboth.p3xp.bold")
            values = self.driver.find_elements(By.CSS_SELECTOR, "div.clearboth.p3px")
            
            # Map labels to field names (use _raw suffix to emphasize no transformation)
            field_mapping = {
                "Airport Type": "airport_type",
                "Lat/Long": "coordinates_raw",
                "Elevation (ft)": "elevation_raw",
                "Fuel Available": "fuel_available",
                "Current UTC": "utc_offset",
                "Approaches": "approaches",
                "Longest Primary Runway (ft)": "longest_runway_raw",
                "Runway Surface": "runway_surface",
                "PCN": "pcn",
                "Open 24 Hours": "open_24h",
                "Customs": "customs",
                "Slots Required": "slots_required",
                "Handling Mandatory": "handling_mandatory",
            }
            
            # Extract paired values
            for label_elem, value_elem in zip(labels, values):
                try:
                    label = label_elem.text.strip().replace("\xa0", "")
                    value = value_elem.text.strip().replace("\xa0", "")
                    
                    # Find matching field
                    field_name = field_mapping.get(label)
                    if field_name:
                        # Boolean fields
                        if field_name in ["open_24h", "customs", "slots_required", "handling_mandatory"]:
                            # Check if value indicates true
                            data[field_name] = bool(value and value.lower() not in ["no", "false", "n/a", ""])
                            if data[field_name]:
                                observed.append(field_name)
                            else:
                                missing.append(field_name)
                        else:
                            # Text fields - store RAW (NO transformation)
                            if value:
                                # Special handling for UTC offset - strip time if present
                                if field_name == "utc_offset" and "(" in value:
                                    # Extract just the offset part: "9:10:16 AM (+4.00)" -> "+4.00"
                                    import re
                                    offset_match = re.search(r'\(([+-]?\d+\.\d+)\)', value)
                                    if offset_match:
                                        value = offset_match.group(1)
                                
                                data[field_name] = value
                                observed.append(field_name)
                            else:
                                missing.append(field_name)
                    
                except Exception as e:
                    logger.debug(f"Error extracting paired value: {e}")
                    continue
            
        except Exception as e:
            errors.append({"field": "additional_data", "error": str(e)})
        
        return data
    
    def _parse_organization_entities(self, url: str, airport_icao: Optional[str]) -> List[Dict[str, Any]]:
        """
        Extract all organization entities from the airport page.
        Includes FBOs, handlers, hotels, caterers, maintenance, etc.
        """
        organizations = []
        self._panel_counts: Dict[str, Dict[str, int]] = {}

        # Parse every known AC-U-KWIK service section.  Role is deliberately
        # attached to the airport listing, never inferred as organization-wide.
        sections = list(SECTION_ROLES.items())
        known_sections = set(SECTION_ROLES)

        # Future-proofing: preserve previously unseen service panels as OTHER so
        # a site change cannot silently discard a complete category.
        for title in self.driver.find_elements(By.CSS_SELECTOR, ".bluePanelTitle"):
            raw_title = " ".join(title.text.split())
            canonical = normalize_section_title(raw_title)
            if canonical in known_sections:
                continue
            try:
                panel = title.find_element(By.XPATH, "ancestor::div[contains(@class,'bluePanel')][1]")
                if panel.find_elements(By.CSS_SELECTOR, ".vendor, .bluePanelRow"):
                    sections.append((raw_title, "OTHER"))
            except Exception:
                continue

        for section_name, role in sections:
            try:
                orgs = self._extract_vendors_from_section(section_name, role, url, airport_icao)
                organizations.extend(orgs)
            except Exception as e:
                logger.debug(f"Error extracting {section_name}: {e}")

        return self._dedupe_organizations(organizations)

    @staticmethod
    def _dedupe_organizations(organizations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Drop listings emitted twice for one airport occurrence.

        The same source listing may legitimately appear under two different
        roles (e.g. FBO and Handler), so the key is (source_listing_key, role).
        An OTHER copy of a listing that a known section already produced is
        a duplicate panel, not a new role.  Listings without source IDs (hotels)
        that share a name but differ in content are kept and re-keyed by
        name and address instead of being dropped.
        """
        result: List[Dict[str, Any]] = []
        by_key: Dict[tuple, Dict[str, Any]] = {}
        known_role_keys = set()
        for org in organizations:
            data = org.get("data") or {}
            key = data.get("source_listing_key")
            role = (data.get("roles") or [None])[0]
            if key and role != "OTHER":
                known_role_keys.add(key)
        for org in organizations:
            data = org.get("data") or {}
            key = data.get("source_listing_key")
            role = (data.get("roles") or [None])[0]
            if not key:
                result.append(org)
                continue
            if role == "OTHER" and key in known_role_keys:
                target = next((kept for kept in result
                               if kept["data"].get("source_listing_key") == key), None)
                if target is not None:
                    for section in data.get("source_sections") or []:
                        if section not in target["data"].setdefault("source_sections", []):
                            target["data"]["source_sections"].append(section)
                continue
            existing = by_key.get((key, role))
            if existing is None:
                by_key[(key, role)] = org
                result.append(org)
                continue
            if existing["data"].get("raw_text") == data.get("raw_text") or data.get("source_profile_url"):
                for section in data.get("source_sections") or []:
                    if section not in existing["data"].setdefault("source_sections", []):
                        existing["data"]["source_sections"].append(section)
                continue
            address = (data.get("address") or {}).get("full") or data.get("raw_text") or ""
            new_key = hashlib.sha256(
                f"{key}|{' '.join(address.split()).lower()}".encode("utf-8")
            ).hexdigest()[:24]
            data["source_listing_key"] = new_key
            org["external_id"] = generate_external_id(
                "organization", data, (data.get("associated_airports") or [None])[0]
            )
            by_key[(new_key, role)] = org
            result.append(org)
        return result

    def _attach_panel_counts(self, airport_entity: Dict[str, Any]) -> None:
        """Record listing counts per service panel on the airport record.

        ``expected`` is the number of distinct non-empty data-anchor-id values
        (distinct names for hotels); a mismatch means listings were merged or
        dropped, so it is reported as an error instead of passing silently.
        """
        counts = getattr(self, "_panel_counts", None)
        if not counts:
            return
        data = airport_entity.setdefault("data", {})
        data["organization_panel_counts"] = counts
        mismatches = {
            section: value for section, value in counts.items()
            if value.get("expected") and value.get("parsed") != value.get("expected")
        }
        if mismatches:
            airport_entity.setdefault("errors", []).append({
                "field": "organization_panel_counts",
                "error": "Parsed listing count differs from source anchors: " + json.dumps(mismatches, sort_keys=True),
            })
            if airport_entity.get("scrape_status") == "SUCCESS":
                airport_entity["scrape_status"] = "PARTIAL"

    def _extract_vendors_from_section(self, section_name: str, role: str, 
                                     url: str, airport_icao: Optional[str]) -> List[Dict[str, Any]]:
        """Extract vendor organizations from a specific section."""
        vendors = []

        try:
            section_container = None
            vendor_blocks = []

            if section_name == "FBOs":
                vendor_blocks = self.driver.find_elements(By.CSS_SELECTOR, "div.fbo div.vendor")

            panel_id_map = {
                "Handlers": "dnn_ctr422_VDC_ctl00_pnlHandlers",
                "Supervising Agents": "dnn_ctr422_VDC_ctl00_pnlSupervising_Agents",
                "Fuel Only": "dnn_ctr422_VDC_ctl00_pnlFuel",
                "Flight Support Organizations": "dnn_ctr422_VDC_ctl00_pnlFSO",
                "Caterers": "dnn_ctr422_VDC_ctl00_pnlCaterers",
                "Maintenance": "dnn_ctr422_VDC_ctl00_pnlMaintenance",
                "Limo": "dnn_ctr422_VDC_ctl00_pnlLimo",
                "Hotels": "dnn_ctr422_VDC_ctl00_pnlHotels",
                "Car Rental": "dnn_ctr422_VDC_ctl00_pnlCar",
                "Charter": "dnn_ctr422_VDC_ctl00_pnlCharter",
                "Detailers": "dnn_ctr422_VDC_ctl00_pnlDetailers",
                "Protection": "dnn_ctr422_VDC_ctl00_pnlProtection",
                "Stores": "dnn_ctr422_VDC_ctl00_pnlStores",
            }

            panel_id = panel_id_map.get(section_name)
            if panel_id:
                found = self.driver.find_elements(By.ID, panel_id)
                if found:
                    section_container = found[0]

            # Fallback for layout variations where IDs differ but title text is present.
            if section_name != "FBOs" and not section_container:
                fallback_xpath = (
                    "//div[contains(@class,'bluePanel')][.//div[contains(@class,'bluePanelTitle') "
                    f"and contains(normalize-space(.), '{section_name}')]]"
                )
                found = self.driver.find_elements(By.XPATH, fallback_xpath)
                if found:
                    section_container = found[0]

            if not vendor_blocks and not section_container:
                return vendors

            if section_container:
                vendor_blocks.extend(section_container.find_elements(
                    By.CSS_SELECTOR,
                    ".vendor, .advertPR.vendor, .advertPR .vendor, .bluePanelRow"
                ))

            # Layout-specific fallbacks for table-like hotel/car panels.
            if section_name == "Hotels":
                vendor_blocks.extend(self.driver.find_elements(
                    By.CSS_SELECTOR, ".Hotels .bluePanelRow, div[id*='pnlHotels'] .bluePanelRow"
                ))
            elif section_name == "Car Rental":
                vendor_blocks.extend(self.driver.find_elements(
                    By.CSS_SELECTOR, ".Car .bluePanelRow, div[id*='pnlCar'] .bluePanelRow"
                ))

            # Non-advert service rows hold two listings side by side, each in a
            # direct child div.w50p.  Parse every column as its own listing so
            # the second one is neither lost nor merged into the first.
            vendor_blocks = self._split_listing_columns(vendor_blocks)

            seen = set()
            for vendor_block in vendor_blocks:
                try:
                    signature = (
                        (vendor_block.get_attribute("id") or ""),
                        " ".join(vendor_block.text.split())[:500],
                    )
                    if signature in seen:
                        continue
                    seen.add(signature)
                    org = self._extract_complete_listing(
                        vendor_block, section_name, role, url, airport_icao
                    )
                    if org:
                        vendors.append(org)
                except Exception as e:
                    logger.debug(f"Error extracting single vendor: {e}")
                    continue

            self._record_panel_count(section_name, section_container, vendors)

        except Exception as e:
            logger.debug(f"Error in section {section_name}: {e}")

        return vendors

    @staticmethod
    def _split_listing_columns(blocks: List[Any]) -> List[Any]:
        """Replace two-column .bluePanelRow blocks with their listing columns.

        Only direct child ``div.w50p`` columns that carry a non-empty
        data-anchor-id or a listing name are kept; empty filler columns are
        skipped.  Advert rows (anchor directly on the row), FBO/Fuel
        ``.vendor`` blocks and table-like rows are returned unchanged.
        """
        column_xpath = f"./div[{_CLASS_XPATH.format('w50p')}]"
        name_xpath = (
            f".//strong[{_CLASS_XPATH.format('fs18px')}][normalize-space(.) != ''] | "
            f".//*[{_CLASS_XPATH.format('fs18px')} and {_CLASS_XPATH.format('bold')}][normalize-space(.) != '']"
        )
        expanded = []
        for block in blocks:
            if "bluePanelRow" not in (block.get_attribute("class") or "").split():
                expanded.append(block)
                continue
            columns = block.find_elements(By.XPATH, column_xpath)
            if not columns:
                expanded.append(block)
                continue
            for column in columns:
                anchored = column.find_elements(
                    By.XPATH, ".//a[@data-anchor-id and normalize-space(@data-anchor-id) != '']"
                )
                if anchored or column.find_elements(By.XPATH, name_xpath):
                    expanded.append(column)
        return expanded

    def _record_panel_count(self, section_name: str, section_container, vendors: List[Dict[str, Any]]) -> None:
        """Store expected (source anchors) vs parsed listing counts for a panel."""
        if not hasattr(self, "_panel_counts"):
            self._panel_counts = {}
        scopes = [section_container] if section_container is not None else []
        if section_name == "FBOs":
            scopes = self.driver.find_elements(By.CSS_SELECTOR, "div.fbo")
        if not scopes:
            return
        anchors = set()
        names = set()
        for scope in scopes:
            for anchor in scope.find_elements(By.XPATH, ".//a[@data-anchor-id]"):
                value = (anchor.get_attribute("data-anchor-id") or "").strip()
                if value:
                    anchors.add(value)
            if section_name == "Hotels" and not anchors:
                for element in scope.find_elements(
                    By.XPATH,
                    f".//*[{_CLASS_XPATH.format('bluePanelRow')}]"
                    f"/*[{_CLASS_XPATH.format('fs18px')}][normalize-space(.) != '']",
                ):
                    names.add(" ".join(element.text.split()))
        expected = len(anchors) or len(names)
        parsed = len(vendors)
        if section_name == "Hotels" and not anchors:
            parsed = len({v["data"].get("name") for v in vendors})
        if expected or parsed:
            self._panel_counts[section_name] = {"expected": expected, "parsed": parsed}

    @staticmethod
    def _append_attribute(attributes: Dict[str, Any], label: str, value: str) -> None:
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "field"
        if key not in attributes:
            attributes[key] = value
        elif attributes[key] != value:
            current = attributes[key] if isinstance(attributes[key], list) else [attributes[key]]
            if value not in current:
                current.append(value)
            attributes[key] = current

    def _extract_complete_listing(
        self, vendor_block, section_name: str, role: str, url: str,
        airport_icao: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Extract normalized values and a lossless snapshot of a service listing."""
        name = None
        name_selectors = [
            ".vendorName strong", ".vendorName a", ".vendorName",
            ".fs18px.bold", ".bold.fs18px", "strong.fs18px",
            "div.fl.w30p.fs18px.bold", "h3", "h4",
        ]
        for selector in name_selectors:
            for element in vendor_block.find_elements(By.CSS_SELECTOR, selector):
                candidate = " ".join(element.text.split()).strip()
                if candidate and candidate.upper() != "INTERNATIONAL" and len(candidate) >= 2:
                    name = candidate.split(" | ")[0]
                    break
            if name:
                break

        anchors = vendor_block.find_elements(By.TAG_NAME, "a")
        links = []
        identifiers: Dict[str, List[str]] = {}
        source_profile_url = None
        for anchor in anchors:
            href = anchor.get_attribute("href")
            absolute_href = urljoin(url, href) if href else None
            link = {"text": " ".join(anchor.text.split())}
            if absolute_href:
                link["url"] = absolute_href
            for attribute in ("data-anchor-id", "data-id", "data-service"):
                value = anchor.get_attribute(attribute)
                if value:
                    identifiers.setdefault(attribute, []).append(value)
                    link[attribute] = value
            if len(link) > 1 or link.get("text"):
                links.append(link)
            if absolute_href and re.search(
                r"/(?:Basic-Info|FBO|Ground-Handler|Supplier|Company|Organization)/",
                absolute_href, re.IGNORECASE
            ):
                source_profile_url = source_profile_url or absolute_href

        for button in vendor_block.find_elements(By.TAG_NAME, "button"):
            for attribute in ("data-id", "data-service"):
                value = button.get_attribute(attribute)
                if value:
                    identifiers.setdefault(attribute, []).append(value)

        # Hidden ASP.NET inputs (hfAcukwik_ID, hfSupplier_ID, ...).  Kept apart
        # from ``identifiers`` until the listing key is computed so existing
        # keys stay stable.
        hidden_identifiers: Dict[str, List[str]] = {}
        for hidden in vendor_block.find_elements(By.XPATH, ".//input[@type='hidden']"):
            field_name = (hidden.get_attribute("name") or hidden.get_attribute("id") or "")
            value = (hidden.get_attribute("value") or "").strip()
            for suffix in _LISTING_HIDDEN_ID_FIELDS:
                if value and field_name.endswith(suffix):
                    hidden_identifiers.setdefault(suffix, []).append(value)
                    break

        if not name:
            anchor_names = identifiers.get("data-anchor-id") or []
            if anchor_names:
                name = anchor_names[0].replace("-", " ").strip()
        if not name:
            return None

        generic_labels = {
            "address", "phone", "fax", "email", "website", "distance",
            "price range", "remarks", "sita", "aftn", "frequency", "brand",
            "name and contact info",
        }
        if name.lower() in generic_labels:
            return None

        # Block-aware text without <script>/<style> bodies, so adjacent label
        # and value blocks are never glued together ("SITADXBMX7X").
        raw_text = block_text(vendor_block)
        raw_fields: List[Dict[str, Any]] = []
        attributes: Dict[str, Any] = {}

        # Capture all DOM label/value pairs. Unknown labels are intentionally
        # retained in raw_fields/attributes instead of being discarded.  The
        # fuel price grid has its own bold header cells (Self/Full/JET A) and is
        # parsed separately, so it is excluded here.
        not_in_fuel_table = f"[not(ancestor::*[{_CLASS_XPATH.format('fuelTable')}])]"
        label_elements = vendor_block.find_elements(
            By.XPATH,
            f".//*[{_CLASS_XPATH.format('bold')}][normalize-space(.) != '']{not_in_fuel_table}"
        )
        # <strong>/<b> labels: "<strong>Brand </strong>PHILLIPS 66" (value is the
        # trailing text) and "<div><strong>SITA</strong></div><div>DXBMX7X</div>".
        strong_labels = vendor_block.find_elements(
            By.XPATH,
            f".//*[self::strong or self::b][not({_CLASS_XPATH.format('fs18px')})]"
            f"[normalize-space(.) != '']{not_in_fuel_table}"
        )
        seen_pairs = set()
        dom_labels = set()

        def add_raw_field(label: str, value: str, value_element=None) -> None:
            pair = (label.lower(), value)
            if pair in seen_pairs:
                return
            seen_pairs.add(pair)
            dom_labels.add(label.lower())
            entry: Dict[str, Any] = {"label": label, "value": value}
            value_lines = [line for line in value.splitlines() if line.strip()]
            if len(value_lines) > 1:
                # Keep the single-line value format of earlier HTTP output and
                # preserve the block structure (address lines etc.) losslessly.
                entry["value"] = " ".join(value_lines)
                entry["lines"] = value_lines
            value_links = []
            if value_element is not None:
                for value_link in value_element.find_elements(By.TAG_NAME, "a"):
                    href = value_link.get_attribute("href")
                    if href:
                        value_links.append({
                            "text": " ".join(value_link.text.split()),
                            "url": urljoin(url, href),
                        })
            if value_links:
                entry["links"] = value_links
            raw_fields.append(entry)
            self._append_attribute(attributes, label, entry["value"])

        for label_element in label_elements:
            label = " ".join(label_element.text.split()).strip(" :")
            if not label or label == name or len(label) > 80 or "\n" in label_element.text.strip():
                continue
            siblings = label_element.find_elements(By.XPATH, "following-sibling::*[1]")
            if not siblings:
                continue
            value_element = siblings[0]
            value = block_text(value_element)
            if not value or value == label:
                continue
            add_raw_field(label, value, value_element)

        for label_element in strong_labels:
            label = " ".join(label_element.text.split()).strip(" :")
            if not label or label == name or len(label) > 80:
                continue
            value_element = None
            value = element_tail_text(self.driver, label_element)
            if not value:
                siblings = label_element.find_elements(By.XPATH, "following-sibling::*[1]")
                if siblings:
                    value_element = siblings[0]
                else:
                    # Label wrapped alone in its own cell: value is the next cell.
                    parents = label_element.find_elements(By.XPATH, "..")
                    if parents and " ".join(parents[0].text.split()).strip(" :") == label:
                        siblings = parents[0].find_elements(By.XPATH, "following-sibling::*[1]")
                        value_element = siblings[0] if siblings else None
                value = block_text(value_element) if value_element is not None else ""
            if not value or value.strip(" :").lower() == label.lower():
                continue
            add_raw_field(label, value, value_element)

        # Some table layouts render labels outside each row. Preserve and
        # normalize common line-oriented fields as a fallback.  Labels already
        # paired from the DOM are not re-paired, and a value equal to its own
        # label (e.g. a "Website" link under a "Website" label) is skipped.
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        recognized_labels = {
            "phone", "tel", "telephone", "tel after hours", "phone after hours",
            "toll free", "toll-free", "toll free phone", "fax", "email", "website",
            "address", "sita", "aftn", "frequency", "remarks", "brand", "hours",
            "hours of operation", "distance", "distance from airport", "price range",
        }
        for index, line in enumerate(lines[:-1]):
            label = line.strip(" :")
            if label.lower() not in recognized_labels or label.lower() in dom_labels:
                continue
            value = lines[index + 1]
            pair = (label.lower(), value)
            if (
                value.strip(" :").lower() in recognized_labels
                or value.strip(" :").lower() == label.lower()
                or pair in seen_pairs
            ):
                continue
            seen_pairs.add(pair)
            raw_fields.append({"label": label, "value": value})
            self._append_attribute(attributes, label, value)

        fuel_info = self._extract_fuel_prices(vendor_block)

        contacts = []

        def add_contact(contact_type: str, value: Optional[str], label: Optional[str] = None) -> None:
            if not value:
                return
            cleaned = " ".join(str(value).split()).strip()
            if not cleaned or cleaned.lower() in {"show email", "n/a", "none"}:
                return
            contact = {"type": contact_type, "value": cleaned}
            if label:
                contact["label"] = label
            if contact not in contacts:
                contacts.append(contact)

        # Links are more reliable than display text for websites and email.
        for link in links:
            href = link.get("url")
            if not href:
                continue
            if href.lower().startswith("mailto:"):
                add_contact("email", href.split(":", 1)[1].split("?", 1)[0])
            elif href.startswith("http") and not self._is_source_site_link(href, source_profile_url):
                add_contact("website", href)

        # Resolve protected email buttons through the authenticated endpoint.
        resolver = getattr(self.driver, "email_resolver", None)
        if callable(resolver):
            for button in vendor_block.find_elements(By.CSS_SELECTOR, "button.ghEmail, button.sEmail"):
                try:
                    add_contact("email", resolver(button))
                except Exception as exc:
                    logger.debug(f"Listing email resolution failed: {exc}")

        address = None
        sita_code = None
        aftn_code = None
        remarks = None
        brands = []
        distance = None
        price_range = None
        hours = None
        for field in raw_fields:
            label = field["label"].lower().replace("&", "and").strip(" :")
            value = field["value"]
            value_lines = field.get("lines") or [value]
            if label in {"phone", "tel", "telephone"}:
                add_contact("phone", value, "Primary")
            elif label in {"tel after hours", "phone after hours", "after hours"}:
                add_contact("phone_after_hours", value)
            elif label in {"toll free", "toll-free", "tollfree", "toll free phone", "toll-free phone"}:
                add_contact("toll_free", value)
            elif label == "fax":
                add_contact("fax", value)
            elif label == "email" and "@" in value:
                add_contact("email", value)
            elif label == "website":
                for field_link in field.get("links", []):
                    link_url = field_link.get("url")
                    if link_url and not self._is_source_site_link(link_url, source_profile_url):
                        add_contact("website", link_url)
            elif label == "address":
                address = self._parse_address("\n".join(value_lines))
            elif label == "sita":
                sita_code = value
            elif label == "aftn":
                aftn_code = value
            elif label == "frequency":
                add_contact("frequency", value)
            elif label == "remarks":
                remarks = value
            elif label == "brand":
                brands.extend(
                    part.strip() for part in re.split(r"[,\n]", "\n".join(value_lines)) if part.strip()
                )
            elif label in {"distance", "distance from airport"}:
                distance = value
            elif label == "price range":
                price_range = value
            elif label in {"hours", "hours of operation"}:
                hours = value

        media = []
        for image in vendor_block.find_elements(By.TAG_NAME, "img"):
            src = image.get_attribute("src")
            if src:
                media.append({"url": urljoin(url, src), "alt": image.get_attribute("alt") or ""})

        identity_payload = source_profile_url or (
            json.dumps(identifiers, sort_keys=True) if identifiers else name.lower()
        )
        source_listing_key = hashlib.sha256(
            f"{airport_icao or ''}|{identity_payload}".encode("utf-8")
        ).hexdigest()[:24]
        for key, values in hidden_identifiers.items():
            identifiers.setdefault(key, []).extend(values)
        source_listing_id = None
        for key in ("data-anchor-id", "data-id"):
            values = identifiers.get(key) or []
            if values:
                source_listing_id = values[0]
                break

        data: Dict[str, Any] = {
            "name": name,
            "display_name": name,
            "roles": [role],
            "associated_airports": [airport_icao] if airport_icao else [],
            "source_section": section_name,
            "source_sections": [section_name],
            "source_listing_key": source_listing_key,
            "source_listing_id": source_listing_id,
            "source_profile_url": source_profile_url,
            "source_identifiers": identifiers,
            "contacts": contacts,
            "raw_fields": raw_fields,
            "attributes": attributes,
            "links": links,
            "media": media,
            "raw_text": raw_text,
        }
        optional_values = {
            "address": address,
            "sita_code": sita_code,
            "aftn_code": aftn_code,
            "remarks": remarks,
            "brand": sorted(set(brands)),
            "distance_from_airport": distance,
            "price_range": price_range,
            "hours": hours,
            "fuel_prices": fuel_info.get("fuel_prices"),
            "fuel_price_unit": fuel_info.get("fuel_price_unit"),
            "fuel_price_message": fuel_info.get("fuel_price_message"),
            "fuel_prices_last_updated": fuel_info.get("fuel_prices_last_updated"),
        }
        data.update({key: value for key, value in optional_values.items() if value})
        data = validate_and_clean(data)
        external_id = generate_external_id("organization", data, airport_icao)
        observed_fields = ["name", "roles", "associated_airports", "source_section", "raw_text"]
        observed_fields.extend(key for key, value in data.items() if value and key not in observed_fields)

        return {
            "entity_type": "organization",
            "external_id": external_id,
            "url": url,
            "scrape_status": "SUCCESS",
            "data": data,
            "observed_fields": sorted(set(observed_fields)),
            "missing_fields": [],
            "errors": [],
        }
    
    @staticmethod
    def _is_source_site_link(href: str, source_profile_url: Optional[str] = None) -> bool:
        """True for AC-U-KWIK's own pages (profiles, airports), never a website."""
        if not href:
            return False
        if source_profile_url and href == source_profile_url:
            return True
        host = urlparse(href).netloc.lower().split(":")[0]
        if host == "acukwik.com" or host.endswith(".acukwik.com"):
            return True
        return bool(re.search(r"/(?:Basic-Info|Airport-Info)/", href, re.IGNORECASE)) and not host

    def _extract_fuel_prices(self, vendor_block) -> Dict[str, Any]:
        """Parse an FBO's fuel price grid and price message.

        Header row (.fuelBorder1) holds the service columns (Self/Full); each
        .fuelBorder2 row holds a fuel name and one price cell per column.
        Empty cells are skipped rather than emitted as bogus values.
        """
        result: Dict[str, Any] = {}
        prices: List[Dict[str, Any]] = []
        for table in vendor_block.find_elements(By.XPATH, f".//*[{_CLASS_XPATH.format('fuelTable')}]"):
            services: List[str] = []
            headers = table.find_elements(By.XPATH, f".//*[{_CLASS_XPATH.format('fuelBorder1')}]")
            if headers:
                services = [" ".join(cell.text.split()) for cell in headers[0].find_elements(By.XPATH, "./div")]
            for row in table.find_elements(By.XPATH, f".//*[{_CLASS_XPATH.format('fuelBorder2')}]"):
                cells = row.find_elements(By.XPATH, "./div")
                if not cells:
                    continue
                fuel = " ".join(cells[0].text.split())
                for index, cell in enumerate(cells[1:], start=1):
                    price_spans = cell.find_elements(By.XPATH, f".//*[{_CLASS_XPATH.format('price')}]")
                    usd_spans = cell.find_elements(By.XPATH, f".//*[{_CLASS_XPATH.format('USDPrice')}]")
                    price = " ".join((price_spans[0].text if price_spans else "").split())
                    usd_price = " ".join(
                        ((usd_spans[0].get_attribute("textContent") or usd_spans[0].text) if usd_spans else "").split()
                    )
                    if not price and not usd_price:
                        continue
                    entry: Dict[str, Any] = {
                        "fuel": fuel or None,
                        "service": services[index] if index < len(services) and services[index] else None,
                        "price": price or None,
                        "usd_price": usd_price or None,
                    }
                    if entry not in prices:
                        prices.append(entry)
            for unit_cell in table.find_elements(By.XPATH, f"./div[{_CLASS_XPATH.format('DNNAlignright')}]"):
                unit = " ".join(unit_cell.text.split())
                if unit and "fuel_price_unit" not in result:
                    result["fuel_price_unit"] = unit
        if prices:
            result["fuel_prices"] = prices

        messages: List[str] = []
        for element in vendor_block.find_elements(By.XPATH, f".//*[{_CLASS_XPATH.format('fuelPriceMessage')}]"):
            message = " ".join(
                (element.get_attribute("textContent") or element.text or "").split()
            )
            if message and message not in messages:
                messages.append(message)
        if messages:
            result["fuel_price_message"] = "\n".join(messages)
            for message in messages:
                match = re.search(r"Last Updated:\s*(.+)", message, re.IGNORECASE)
                if match:
                    result["fuel_prices_last_updated"] = match.group(1).strip()
                    break
        return result

    def _extract_single_vendor(self, vendor_block, role: str, url: str, 
                               airport_icao: Optional[str]) -> Optional[Dict[str, Any]]:
        """Extract single vendor/organization from a block using DOM traversal."""
        
        observed_fields = []
        missing_fields = []
        errors = []
        
        try:
            # Extract vendor name across multiple page layouts.
            name = None
            name_selectors = [
                ".clearboth.vendorName a strong.fs18px",
                ".clearboth.vendorName strong.fs18px",
                ".clearboth.vendorName a",
                ".clearboth.vendorName strong",
                ".clearboth.vendorName",
                "strong.fs18px",
                ".vendorName strong",
                "strong",
            ]

            for selector in name_selectors:
                elems = vendor_block.find_elements(By.CSS_SELECTOR, selector)
                for elem in elems:
                    text = elem.text.strip()
                    if text:
                        name = text.split("\n")[0].strip()
                        break
                if name:
                    break

            # Fallback: derive a readable name from data-anchor-id when text nodes are redacted.
            if not name:
                anchor_elems = vendor_block.find_elements(By.XPATH, ".//a[@data-anchor-id and string-length(@data-anchor-id)>0]")
                if anchor_elems:
                    anchor_name = anchor_elems[0].get_attribute("data-anchor-id")
                    if anchor_name:
                        name = anchor_name.strip()
            
            if not name:
                return None
            
            # Filter out generic labels
            generic_labels = ["Address", "Phone", "Fax", "Email", "Website", "INTERNATIONAL", 
                            "Distance", "Price range", "Remarks", "SITA", "Frequency", "Brand"]
            if name in generic_labels or name.upper() == "INTERNATIONAL" or len(name) < 3:
                return None
            
            observed_fields.append("name")
            
            # Parse contact info using DOM traversal
            contacts = []
            address_data = {}
            sita_code = None
            aftn_code = None
            remarks = None
            brands = []
            
            # Find all contact rows, including AC-U-KWIK's inconsistent class spellings and rows without mb9px.
            contact_rows = vendor_block.find_elements(
                By.XPATH,
                ".//div[(contains(@class, 'clearfix') or contains(@class, 'clearfi') or contains(@class, 'clearboth')) "
                "and .//div[contains(@class, 'w35p') and (contains(@class, 'bold') or contains(@class, 'fl'))]]"
            )
            
            for row in contact_rows:
                try:
                    # Extract label and value elements
                    label_elem = row.find_element(By.CSS_SELECTOR, "div.fl.w35p.bold, div.fl.w35p")
                    label = label_elem.text.strip().lower().replace(':', '').replace('&', 'and')
                    label = re.sub(r"\s+", " ", label)
                    
                    value_elem = row.find_element(By.CSS_SELECTOR, "div.fl.w65p, div.fl.w60p")
                    
                    # Email - click button if present then read mailto/text
                    if label == "email":
                        button = None
                        email_resolver = getattr(self.driver, "email_resolver", None)
                        if callable(email_resolver):
                            try:
                                email = email_resolver(value_elem.find_element(By.CSS_SELECTOR, "button.ghEmail, button.sEmail"))
                                if email:
                                    contacts.append({"type": "email", "value": email})
                                    observed_fields.append("email")
                                    continue
                            except Exception as e:
                                logger.debug(f"Cached email resolution failed: {e}")
                        if getattr(self.driver, "supports_interaction", True):
                            try:
                                button = value_elem.find_element(By.CSS_SELECTOR, "button.ghEmail, button.sEmail")
                                try:
                                    # Click via JS for reliability
                                    self.driver.execute_script("arguments[0].click();", button)
                                except Exception:
                                    button.click()
                                # Wait briefly for mailto link to appear or button text to change
                                try:
                                    WebDriverWait(self.driver, 3).until(
                                        lambda d: value_elem.find_elements(By.CSS_SELECTOR, "a[href^='mailto:']") or "@" in button.text
                                    )
                                except Exception:
                                    pass
                            except Exception:
                                pass
                        else:
                            try:
                                button = value_elem.find_element(By.CSS_SELECTOR, "button.ghEmail, button.sEmail")
                            except Exception:
                                pass

                        # Try direct email link after potential click
                        try:
                            email_link = value_elem.find_element(By.CSS_SELECTOR, "a[href^='mailto:']")
                            email = (email_link.get_attribute("href") or email_link.text or "").replace("mailto:", "").strip()
                            if email:
                                contacts.append({"type": "email", "value": email})
                                observed_fields.append("email")
                            else:
                                missing_fields.append("email")
                        except Exception:
                            # Fallback: button text may now contain email
                            try:
                                text_email = button.text.strip() if button is not None else ""
                                if text_email and "@" in text_email:
                                    contacts.append({"type": "email", "value": text_email})
                                    observed_fields.append("email")
                                else:
                                    missing_fields.append("email")
                            except Exception:
                                missing_fields.append("email")
                    
                    # Website
                    elif label == "website":
                        try:
                            website_link = value_elem.find_element(By.CSS_SELECTOR, "a")
                            website_url = website_link.get_attribute('href')
                            if website_url and website_url.startswith('http'):
                                contacts.append({"type": "website", "value": website_url})
                                observed_fields.append("website")
                        except:
                            pass
                    
                    # Phone
                    elif label in {"phone", "tel", "telephone"}:
                        phone = value_elem.text.strip()
                        if phone:
                            contacts.append({"type": "phone", "value": phone, "label": "Primary"})
                            observed_fields.append("phone")
                    
                    # Tel After Hours
                    elif label == "tel after hours":
                        phone = value_elem.text.strip()
                        if phone:
                            contacts.append({"type": "phone_after_hours", "value": phone})
                            observed_fields.append("phone_after_hours")
                    
                    # Fax
                    elif label == "fax":
                        fax = value_elem.text.strip()
                        if fax:
                            contacts.append({"type": "fax", "value": fax})
                            observed_fields.append("fax")
                    
                    # Address
                    elif label == "address":
                        address_text = value_elem.text.strip()
                        if address_text:
                            address_data = self._parse_address(address_text)
                            observed_fields.append("address")
                    
                    # SITA
                    elif label == "sita":
                        sita = value_elem.text.strip()
                        if sita:
                            sita_code = sita
                            observed_fields.append("sita_code")
                    
                    # AFTN
                    elif label == "aftn":
                        aftn = value_elem.text.strip()
                        if aftn:
                            aftn_code = aftn
                            observed_fields.append("aftn_code")
                    
                    # Frequency
                    elif label == "frequency":
                        freq = value_elem.text.strip()
                        if freq:
                            contacts.append({"type": "frequency", "value": freq})
                            observed_fields.append("frequency")
                    
                    # Remarks
                    elif label == "remarks":
                        remarks_text = value_elem.text.strip()
                        if remarks_text:
                            remarks = remarks_text
                            observed_fields.append("remarks")
                
                except:
                    continue
            
            # Extract SITA/AFTN from right column (different structure)
            try:
                sita_blocks = vendor_block.find_elements(By.XPATH, ".//div[strong[text()='SITA']]/following-sibling::div[1]")
                for sita_block in sita_blocks:
                    sita_val = sita_block.text.strip()
                    if sita_val and not sita_code:
                        sita_code = sita_val
                        observed_fields.append("sita_code")
            except:
                pass
            
            try:
                aftn_blocks = vendor_block.find_elements(By.XPATH, ".//div[strong[text()='AFTN']]/following-sibling::div[1]")
                for aftn_block in aftn_blocks:
                    aftn_val = aftn_block.text.strip()
                    if aftn_val and not aftn_code:
                        aftn_code = aftn_val
                        observed_fields.append("aftn_code")
            except:
                pass
            
            # Extract Brand information
            try:
                vendor_text = vendor_block.text
                brand_matches = re.findall(r'Brand\s+([A-Z0-9\s]+?)(?:\n|$|Brand)', vendor_text)
                for brand in brand_matches:
                    brand = brand.strip()
                    if brand and brand not in brands:
                        brands.append(brand)
                
                if brands:
                    contacts.append({"type": "brand", "value": ", ".join(brands)})
                    observed_fields.append("brand")
            except:
                pass
            
            # Determine missing fields
            all_possible_fields = ["email", "address", "fax", "phone", "website"]
            for field in all_possible_fields:
                if field not in observed_fields and field not in missing_fields:
                    missing_fields.append(field)
            
            # Build data
            data = {
                "name": name,
                "roles": [role],
                "associated_airports": [airport_icao] if airport_icao else [],
                "contacts": contacts if contacts else None,
            }
            
            if address_data:
                data["address"] = address_data
            
            if sita_code:
                data["sita_code"] = sita_code
            
            if aftn_code:
                data["aftn_code"] = aftn_code
            
            if remarks:
                data["remarks"] = remarks
            
            # Clean
            data = validate_and_clean(data)
            
            # Generate external ID
            external_id = generate_external_id("organization", data, airport_icao)
            
            # Determine status
            scrape_status = "SUCCESS" if observed_fields and len(missing_fields) < 3 else "PARTIAL"
            
            return {
                "entity_type": "organization",
                "external_id": external_id,
                "url": url,
                "scrape_status": scrape_status,
                "data": data,
                "observed_fields": list(set(observed_fields)),
                "missing_fields": list(set(missing_fields)),
                "errors": errors
            }
            
        except Exception as e:
            logger.debug(f"Error extracting vendor: {e}")
            return None
    
    # "Teterboro, NJ 07608" / "Hasbrouck Heights NJ 07604"
    _US_LOCALITY = re.compile(r"^(?P<city>.*?)[,\s]+(?P<state>[A-Z]{2})[,\s]+(?P<zip>\d{5}(?:-\d{4})?)$")
    # "Hounslow, Middlesex TW6 3AE" / "Hayes UB3 5AN"
    _UK_LOCALITY = re.compile(
        r"^(?P<city>.*?)[,\s]+(?P<postcode>[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})$", re.IGNORECASE
    )
    # "75008 Paris" / "Paris 75008" / "1010 Wien"
    _LEADING_POSTCODE_LOCALITY = re.compile(r"^(?P<postcode>\d{4,6}(?:-\d{3,4})?)\s+(?P<city>\D.*)$")
    _TRAILING_POSTCODE_LOCALITY = re.compile(r"^(?P<city>\D.*?)[,\s]+(?P<postcode>\d{4,6}(?:-\d{3,4})?)$")
    _COUNTRY_LINE = re.compile(r"^[A-Z][A-Z .'&()-]*[A-Z.)]$")

    def _parse_address(self, address_text: str) -> Dict[str, Any]:
        """
        Parse address into structured components.

        ``full`` is the single-line address (lines joined by a space, as in
        earlier HTTP output); ``lines`` keeps the source line structure when
        there is more than one line.  Multi-line addresses are split line by
        line: street lines, a locality line ("City ST 12345", "Town POSTCODE")
        and an optional all-caps country line.  ``postal_code`` keeps its
        legacy "ST 12345" form for US addresses; ``state`` and ``zip_code``
        hold the parts.  If parsing fails, store as 'full' only.
        """
        lines = [" ".join(line.split()) for line in (address_text or "").split('\n') if line.strip()]
        address: Dict[str, Any] = {"full": " ".join(lines) if len(lines) > 1 else address_text}
        
        try:
            if len(lines) == 1:
                # Single-line address: no line structure to rely on.
                last_line = lines[0]
                postal_match = re.search(r'([A-Z]{2,3}[-\s]?\d{3,5})', last_line, re.IGNORECASE)
                if postal_match:
                    address['postal_code'] = postal_match.group(1)
                country_match = re.search(r'([A-Z\s]{3,})$', last_line)
                if country_match:
                    address['country'] = country_match.group(1).strip()
                return address
            if not lines:
                return address

            address['lines'] = lines
            remaining = list(lines)
            if self._COUNTRY_LINE.match(remaining[-1]) and not any(ch.isdigit() for ch in remaining[-1]):
                address['country'] = remaining.pop()
            if not remaining:
                return address

            locality = remaining.pop().rstrip(" ,")
            city = None
            us_match = self._US_LOCALITY.match(locality)
            uk_match = None if us_match else self._UK_LOCALITY.match(locality)
            if us_match:
                city = us_match.group("city")
                address['state'] = us_match.group("state")
                address['zip_code'] = us_match.group("zip")
                address['postal_code'] = f"{us_match.group('state')} {us_match.group('zip')}"
            elif uk_match and uk_match.group("city"):
                city = uk_match.group("city")
                address['postal_code'] = uk_match.group("postcode").upper()
            else:
                numeric_match = (
                    self._LEADING_POSTCODE_LOCALITY.match(locality)
                    or self._TRAILING_POSTCODE_LOCALITY.match(locality)
                )
                if numeric_match:
                    city = numeric_match.group("city")
                    address['postal_code'] = numeric_match.group("postcode")
                elif remaining:
                    city = locality
                else:
                    # A lone line before the country is a street, not a city.
                    remaining.append(locality)

            if city:
                city_parts = [part.strip() for part in city.strip(" ,").split(",") if part.strip()]
                if city_parts:
                    address['city'] = city_parts[0]
                    if len(city_parts) > 1:
                        address['region'] = ", ".join(city_parts[1:])

            street_lines = [line.rstrip(" ,") for line in remaining if line.rstrip(" ,")]
            if street_lines:
                address['street'] = ', '.join(street_lines)
        
        except Exception as e:
            logger.debug(f"Address parsing failed: {e}")
        
        return address
    
    def _extract_hotels(self, url: str, airport_icao: Optional[str]) -> List[Dict[str, Any]]:
        """Extract hotels from Hotels section using DOM traversal."""
        hotels = []
        
        try:
            # Hotels are in .bluePanelRow under Hotels section
            hotel_rows = self.driver.find_elements(By.CSS_SELECTOR, ".Hotels .bluePanelRow, div[id*='pnlHotels'] .bluePanelRow")
            
            for hotel_row in hotel_rows:
                try:
                    observed_fields = []
                    missing_fields = []
                    errors = []
                    
                    # Extract hotel name
                    name_elem = hotel_row.find_element(By.CSS_SELECTOR, "div.fs18px.bold, .bold.fs18px, .bluePanelRow .fs18px")
                    name = name_elem.text.strip()

                    if name.upper() == "INTERNATIONAL":
                        anchor = hotel_row.find_elements(By.CSS_SELECTOR, "a[data-anchor-id]")
                        if anchor:
                            anchor_name = anchor[0].get_attribute("data-anchor-id") or ""
                            name = anchor_name.replace("-", " ").strip()
                        else:
                            alt = hotel_row.find_elements(By.CSS_SELECTOR, "a[href*='/Basic-Info/'] strong")
                            if alt:
                                name = alt[0].text.strip()
                    
                    if not name or len(name) < 3:
                        continue
                    
                    # Filter out generic labels
                    generic_labels = ["Phone", "Fax", "Address", "Distance", "Price range", "Name and Contact Info"]
                    if name in generic_labels:
                        continue
                    if name.upper() == "INTERNATIONAL":
                        continue
                    
                    observed_fields.append("name")
                    
                    # Contacts
                    contacts = []
                    address_data = {}
                    distance_from_airport = None
                    price_range = None
                    
                    # Phone using DOM traversal
                    try:
                        phone_elem = hotel_row.find_element(By.XPATH, ".//div[contains(text(), 'Phone')]/following-sibling::div")
                        phone = phone_elem.text.strip()
                        if phone:
                            contacts.append({"type": "phone", "value": phone})
                            observed_fields.append("phone")
                    except:
                        missing_fields.append("phone")
                    
                    # Fax
                    try:
                        fax_elem = hotel_row.find_element(By.XPATH, ".//div[contains(text(), 'Fax')]/following-sibling::div")
                        fax = fax_elem.text.strip()
                        if fax:
                            contacts.append({"type": "fax", "value": fax})
                            observed_fields.append("fax")
                    except:
                        pass
                    
                    # Website
                    try:
                        website_elem = hotel_row.find_element(By.XPATH, ".//div[contains(text(), 'Website')]/following-sibling::div//a")
                        website_url = website_elem.get_attribute('href')
                        if website_url and website_url.startswith('http'):
                            contacts.append({"type": "website", "value": website_url})
                            observed_fields.append("website")
                    except:
                        missing_fields.append("website")
                    
                    # Address - multiple approaches
                    try:
                        # Try direct DOM lookup
                        address_elem = hotel_row.find_element(By.XPATH, ".//div[contains(text(), 'Address')]/following-sibling::div | .//div[@class='fl w100p' and contains(., 'BAHRAIN')]")
                        address_text = address_elem.text.strip()
                        if address_text:
                            address_data = self._parse_address(address_text)
                            observed_fields.append("address")
                    except:
                        # Try extracting from w25p column
                        try:
                            address_cols = hotel_row.find_elements(By.CSS_SELECTOR, "div.w25p.fl.pl15px div.fl.w100p")
                            if address_cols:
                                address_text = address_cols[0].text.strip()
                                if address_text and address_text not in ["Address"]:
                                    address_data = self._parse_address(address_text)
                                    observed_fields.append("address")
                        except:
                            missing_fields.append("address")
                    
                    # Distance from airport
                    try:
                        distance_cols = hotel_row.find_elements(By.CSS_SELECTOR, "div.w20p.fl.pl15px div.fl.w100p")
                        if len(distance_cols) >= 1:
                            distance_from_airport = distance_cols[0].text.strip()
                            if distance_from_airport and distance_from_airport not in ["Distance"]:
                                observed_fields.append("distance_from_airport")
                    except:
                        pass
                    
                    # Price range
                    try:
                        price_cols = hotel_row.find_elements(By.CSS_SELECTOR, "div.w20p.fl.pl15px div.fl.w100p")
                        if len(price_cols) >= 2:
                            price_range = price_cols[1].text.strip()
                            if price_range and price_range not in ["Price range"]:
                                observed_fields.append("price_range")
                    except:
                        pass
                    
                    # Build data
                    data = {
                        "name": name,
                        "roles": ["HOTEL"],
                        "associated_airports": [airport_icao] if airport_icao else [],
                        "contacts": contacts if contacts else None,
                    }
                    
                    if address_data:
                        data["address"] = address_data
                    
                    if distance_from_airport:
                        data["distance_from_airport"] = distance_from_airport
                    
                    if price_range:
                        data["price_range"] = price_range
                    
                    # Clean
                    data = validate_and_clean(data)
                    
                    # Generate external ID
                    external_id = generate_external_id("organization", data, airport_icao)
                    
                    hotels.append({
                        "entity_type": "organization",
                        "external_id": external_id,
                        "url": url,
                        "scrape_status": "SUCCESS" if len(missing_fields) < 2 else "PARTIAL",
                        "data": data,
                        "observed_fields": list(set(observed_fields)),
                        "missing_fields": list(set(missing_fields)),
                        "errors": []
                    })
                    
                except Exception as e:
                    logger.debug(f"Error extracting hotel: {e}")
                    continue
        
        except Exception as e:
            logger.debug(f"Error in hotel section: {e}")
        
        return hotels
    
    def _extract_car_rentals(self, url: str, airport_icao: Optional[str]) -> List[Dict[str, Any]]:
        """Extract car rentals from Car Rental section using DOM traversal."""
        car_rentals = []
        
        try:
            # Car rentals are in .bluePanelRow under Car section
            rental_rows = self.driver.find_elements(By.CSS_SELECTOR, ".Car .bluePanelRow, div[id*='pnlCar'] .bluePanelRow")
            
            for rental_row in rental_rows:
                try:
                    observed_fields = []
                    missing_fields = []
                    errors = []
                    
                    # Extract name from w30p bold div
                    name_elem = rental_row.find_element(By.CSS_SELECTOR, "div.fl.w30p.fs18px.bold")
                    name = name_elem.text.strip()
                    
                    if not name or len(name) < 3:
                        continue
                    
                    observed_fields.append("name")
                    
                    # Contacts
                    contacts = []
                    
                    # Phone using DOM traversal
                    try:
                        phone_elem = rental_row.find_element(By.XPATH, ".//div[contains(text(), 'Phone')]/following-sibling::div")
                        phone = phone_elem.text.strip()
                        if phone:
                            contacts.append({"type": "phone", "value": phone})
                            observed_fields.append("phone")
                    except:
                        missing_fields.append("phone")
                    
                    # Fax
                    try:
                        fax_elem = rental_row.find_element(By.XPATH, ".//div[contains(text(), 'Fax')]/following-sibling::div")
                        fax = fax_elem.text.strip()
                        if fax:
                            contacts.append({"type": "fax", "value": fax})
                            observed_fields.append("fax")
                    except:
                        pass
                    
                    # Website
                    try:
                        website_elem = rental_row.find_element(By.XPATH, ".//div[contains(text(), 'Website')]/following-sibling::div//a")
                        website_url = website_elem.get_attribute('href')
                        if website_url and website_url.startswith('http'):
                            contacts.append({"type": "website", "value": website_url})
                            observed_fields.append("website")
                    except:
                        missing_fields.append("website")
                    
                    # Build data
                    data = {
                        "name": name,
                        "roles": ["CAR_RENTAL"],
                        "associated_airports": [airport_icao] if airport_icao else [],
                        "contacts": contacts if contacts else None,
                    }
                    
                    # Clean
                    data = validate_and_clean(data)
                    
                    # Generate external ID
                    external_id = generate_external_id("organization", data, airport_icao)
                    
                    car_rentals.append({
                        "entity_type": "organization",
                        "external_id": external_id,
                        "url": url,
                        "scrape_status": "SUCCESS" if len(missing_fields) < 2 else "PARTIAL",
                        "data": data,
                        "observed_fields": list(set(observed_fields)),
                        "missing_fields": list(set(missing_fields)),
                        "errors": []
                    })
                    
                except Exception as e:
                    logger.debug(f"Error extracting car rental: {e}")
                    continue
        
        except Exception as e:
            logger.debug(f"Error in car rental section: {e}")
        
        return car_rentals


class ClearanceParser:
    """
    Parses the Clearance tab for a given airport.
    URL pattern: https://acukwik.com/Clearance-Overview/{ICAO}
    Extracts country-level clearance and operational information.
    """

    def __init__(self, driver: WebDriver):
        self.driver = driver
        self.wait = WebDriverWait(driver, 10)

    def parse(self, url: str, icao: str, load_page: bool = True) -> Optional[Dict[str, Any]]:
        """Scrape all clearance fields from the current page or from the live clearance URL."""
        import time
        observed_fields: List[str] = []
        missing_fields: List[str] = []
        errors: List[Dict] = []
        data: Dict[str, Any] = {"associated_airports": [icao]}
        raw_fields: List[Dict[str, Any]] = []

        try:
            if load_page:
                self.driver.get(url)
            print(f"Parsing clearance for {icao} at {url}")
            try:
                self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            except TimeoutException:
                errors.append({"field": "page_load", "error": "Clearance page load timeout"})
            if getattr(self.driver, "supports_interaction", True):
                time.sleep(2)

            # --- Key-value pair fields from main table ---
            kv_field_mapping = {
                "Country": "country",
                "Country Phone Code": "country_phone_code",
                "Currency": "currency",
                "Exchange Guide": "exchange_guide",
                "Time Zone": "time_zone",
                "General Information": "general_information",
                "WGS-84": "wgs84",
                "Visa": "visa",
                "Documentation": "documentation",
                "Application Format": "application_format",
                "Clearance Contacts": "clearance_contacts",
                "Comments": "comments",
            }
            # Find all rows in the main clearance table
            try:
                table_rows = self.driver.find_elements(By.XPATH, "//div[contains(@class,'clearanceOverviewContent')]//table//tr")
                for row in table_rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) != 2:
                        continue
                    label = cells[0].text.strip().replace("\xa0", "")
                    value = cells[1].text.strip().replace("\xa0", "")
                    raw_entry: Dict[str, Any] = {"label": label, "value": value}
                    row_links = []
                    for link in cells[1].find_elements(By.TAG_NAME, "a"):
                        href = link.get_attribute("href")
                        if href:
                            row_links.append({"text": link.text.strip(), "url": urljoin(url, href)})
                    if row_links:
                        raw_entry["links"] = row_links
                    if label or value:
                        raw_fields.append(raw_entry)
                    field = kv_field_mapping.get(label)
                    if not field:
                        continue
                    # Special handling for clearance_contacts: parse into structured contacts if possible
                    if field == "clearance_contacts":
                        contacts = self._parse_clearance_contacts(value)
                        if contacts:
                            data[field] = contacts
                        else:
                            data[field] = value
                        observed_fields.append(field)
                    else:
                        data[field] = value
                        observed_fields.append(field)
            except Exception as e:
                errors.append({"field": "table_parse", "error": str(e)})

            # --- Free-text section fields ---
            section_field_mapping = {
                "General Information": "general_information",
                "WGS-84": "wgs84",
                "Visa": "visa",
                "Documentation": "documentation",
                "Application Format": "application_format",
                "Comments": "comments",
            }
            for heading_text, field_name in section_field_mapping.items():
                # The table row is authoritative; the heading search is only a
                # fallback for layouts without one.  Unscoped, it matched page
                # text elsewhere (e.g. "Visa" in "Visakhapatnam").
                if field_name in data:
                    continue
                try:
                    heading_test = (
                        "(self::h1 or self::h2 or self::h3 or self::h4 or self::strong"
                        " or self::span or self::div or self::td or self::th)"
                    )
                    scope = "//div[contains(@class,'clearanceOverviewContent')]"
                    headings = self.driver.find_elements(
                        By.XPATH,
                        f"{scope}//*[normalize-space(text())='{heading_text}' and {heading_test}]"
                    ) or self.driver.find_elements(
                        By.XPATH,
                        f"{scope}//*[starts-with(normalize-space(text()),'{heading_text}') and {heading_test}]"
                    )
                    if not headings:
                        missing_fields.append(field_name)
                        continue
                    heading = headings[0]
                    # Get sibling/following text block
                    try:
                        sibling = heading.find_element(
                            By.XPATH, "following-sibling::*[1]"
                        )
                        text = sibling.text.strip()
                    except Exception:
                        text = ""
                    # Fallback: parent container text minus the heading text
                    if not text:
                        try:
                            parent = heading.find_element(By.XPATH, "..")
                            parent_text = parent.text.strip()
                            text = parent_text.replace(heading_text, "").strip()
                        except Exception:
                            pass
                    if text:
                        data[field_name] = text
                        observed_fields.append(field_name)
                    else:
                        missing_fields.append(field_name)
                except Exception:
                    missing_fields.append(field_name)

            # --- Clearance Contacts (table rows) ---
            clearance_contacts: List[Dict] = []
            try:
                contact_rows = self.driver.find_elements(
                    By.XPATH,
                    "//*[contains(normalize-space(.//text()),'Clearance Contact')]"
                    "/following-sibling::table[1]//tr[position()>1]"
                    " | //table[.//th[contains(text(),'Clearance')]]//tr[position()>1]"
                )
                for row in contact_rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if not cells:
                        continue
                    entry: Dict[str, Any] = {"section": cells[0].text.strip() if cells else ""}
                    if len(cells) > 1:
                        entry["frequency"] = cells[1].text.strip()
                    if len(cells) > 2:
                        pf = cells[2].text.strip()
                        pm = re.search(r'(?:Phone|Tel)[:\s]+([+\d\s\-().]+)', pf, re.IGNORECASE)
                        fm = re.search(r'Fax[:\s]+([+\d\s\-().]+)', pf, re.IGNORECASE)
                        if pm:
                            entry["phone"] = pm.group(1).strip()
                        if fm:
                            entry["fax"] = fm.group(1).strip()
                        if not pm and not fm and pf:
                            entry["contact_raw"] = pf
                    if len(cells) > 3:
                        try:
                            el = cells[3].find_element(By.CSS_SELECTOR, "a[href^='mailto:']")
                            entry["email"] = el.get_attribute("href").replace("mailto:", "").strip()
                        except Exception:
                            pass
                        try:
                            wl = cells[3].find_element(By.XPATH, ".//a[not(starts-with(@href,'mailto:'))]")
                            href = wl.get_attribute("href")
                            if href and href.startswith("http"):
                                entry["website"] = href
                        except Exception:
                            pass
                    if any(v for v in entry.values() if v):
                        clearance_contacts.append(entry)
            except Exception as e:
                logger.debug(f"Clearance contacts parse error: {e}")

            if clearance_contacts:
                data["clearance_contacts"] = clearance_contacts
                observed_fields.append("clearance_contacts")
            else:
                missing_fields.append("clearance_contacts")

            body = self.driver.find_element(By.TAG_NAME, "body")
            data["raw_text"] = "\n".join(
                line.strip() for line in body.text.splitlines() if line.strip()
            )
            data["raw_fields"] = raw_fields
            data["links"] = [
                {"text": " ".join(link.text.split()), "url": urljoin(url, link.get_attribute("href"))}
                for link in body.find_elements(By.TAG_NAME, "a")
                if link.get_attribute("href")
            ]
            observed_fields.extend(["raw_text", "raw_fields", "links"])

            scrape_status = "SUCCESS" if observed_fields else "PARTIAL"

            # Only include truly missing fields
            all_possible_fields = [
                "country", "country_phone_code", "currency", "exchange_guide", "time_zone",
                "general_information", "wgs84", "visa", "documentation", "application_format",
                "clearance_contacts", "comments"
            ]
            missing_cleaned = [f for f in all_possible_fields if f not in data or not data.get(f)]
            return {
                "entity_type": "clearance",
                "external_id": f"acukwik_clearance_{icao}",
                "url": url,
                "scrape_status": scrape_status,
                "data": {k: v for k, v in data.items() if v is not None},
                "observed_fields": list(set(observed_fields)),
                "missing_fields": missing_cleaned,
                "errors": errors,
            }

        except Exception as e:
            logger.error(f"ClearanceParser error for {url}: {e}")
            return {
                "entity_type": "clearance",
                "external_id": f"acukwik_clearance_{icao}",
                "url": url,
                "scrape_status": "FAILED",
                "data": {},
                "observed_fields": [],
                "missing_fields": [],
                "errors": [{"error": str(e)}],
            }


    # Labels that start a new value inside one Clearance Contacts section.
    _CONTACT_KEYWORDS = r"(?:Tel|Fax|e-mail|Web|AFS/AFTN|SITA)"

    @classmethod
    def _parse_clearance_contacts(cls, value: str) -> List[Dict[str, Any]]:
        """Split the Clearance Contacts text into one entry per flight category.

        Example section: "Corporate/Private Flights: Curacao CAA, Tel +599 9 839
        3319, 839 3324, e-mail civilair@gobiernu.cw, Web www.ccaa.cw/, AFS/AFTN
        TNCCYAYX."  ``phone``/``email``/``website``/``afs_aftn`` keep the first
        value for existing consumers; the plural keys hold every value.
        """
        contacts: List[Dict[str, Any]] = []
        text = " ".join((value or "").split())
        if not text:
            return contacts
        keyword = cls._CONTACT_KEYWORDS
        sections = re.split(r"(?:^|(?<=\.)\s+)(?=[A-Z][\w/ ]*Flights?:)", text)
        for part in sections:
            part = part.strip()
            if not part:
                continue
            entry: Dict[str, Any] = {"raw_value": part}
            header = re.match(r"([A-Z][\w/ ]*Flights?):\s*", part)
            if header:
                entry["section"] = header.group(1).strip()
                rest = part[header.end():]
            elif ":" in part and not re.match(rf"{keyword}\b", part):
                section, rest = part.split(":", 1)
                entry["section"] = section.strip()
            else:
                rest = part
            rest = rest.strip()

            agency_match = re.match(rf"(.*?)(?:,\s*|^){keyword}\b", rest)
            agency = (agency_match.group(1) if agency_match else rest).strip(" ,.")
            if agency:
                entry["agency"] = agency

            def _values(label: str) -> List[str]:
                found: List[str] = []
                pattern = rf"(?:^|,\s*){label}\s+(.*?)(?=,\s*{keyword}\b|\.?$)"
                for match in re.finditer(pattern, rest):
                    # Commas inside "(ext. 5911, 5920)" belong to one number.
                    for item in re.split(r",\s*(?![^()]*\))", match.group(1)):
                        item = item.strip().rstrip(".;").strip()
                        if item and item not in found:
                            found.append(item)
                return found

            phones = _values("Tel")
            faxes = _values("Fax")
            emails: List[str] = []
            for email in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", rest):
                if email not in emails:
                    emails.append(email)
            websites: List[str] = []
            for website in re.findall(r"(?:^|,\s*)Web\s+(\S+)", rest):
                website = website.rstrip(".,;")
                if website and website not in websites:
                    websites.append(website)
            def _codes(label: str) -> List[str]:
                # Addresses are 7-8 character codes; skip joining words such as
                # "& copy" or "(via ...)" around them.
                codes: List[str] = []
                for chunk in _values(label):
                    for code in re.findall(r"\b(?=(?:[A-Za-z0-9]*[A-Z]){2})[A-Za-z0-9]{7,8}\b", chunk):
                        if code not in codes:
                            codes.append(code)
                return codes

            afs = _codes("AFS/AFTN")
            sita = _codes("SITA")

            if phones:
                entry["phone"] = phones[0]
                entry["phones"] = phones
            if faxes:
                entry["fax"] = faxes[0]
                entry["faxes"] = faxes
            if emails:
                entry["email"] = emails[0]
                entry["emails"] = emails
            if websites:
                entry["website"] = websites[0]
                entry["websites"] = websites
            if afs:
                entry["afs_aftn"] = afs[0]
                entry["afs_aftn_addresses"] = afs
            if sita:
                entry["sita"] = sita[0]
                entry["sita_addresses"] = sita
            contacts.append(entry)
        return contacts


class NearbyParser:
    """
    Parses the Nearby tab for a given airport.
    URL pattern: https://acukwik.com/Nearby/{ICAO}
    Extracts a paginated table of nearby airports.
    """

    def __init__(self, driver: WebDriver):
        self.driver = driver
        self.wait = WebDriverWait(driver, 10)

    def parse(self, url: str, icao: str, load_page: bool = True) -> Optional[Dict[str, Any]]:
        """Scrape the nearby airports table from the current page or from the live nearby URL."""
        import time
        observed_fields: List[str] = []
        missing_fields: List[str] = []
        errors: List[Dict] = []
        nearby_airports: List[Dict[str, Any]] = []

        try:
            if load_page:
                self.driver.get(url)
            try:
                self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            except TimeoutException:
                errors.append({"field": "page_load", "error": "Nearby page load timeout"})
            if getattr(self.driver, "supports_interaction", True):
                time.sleep(2)

            self._base_url = url
            # The map script is on the first page only and lists the origin
            # plus every nearby airport across all list pages.
            map_markers = self.extract_map_markers(
                getattr(self.driver, "page_source", "") or "", url
            )

            page_num = 1
            max_pages = 50  # Safety limit
            while page_num <= max_pages:
                rows = self._extract_table_rows(errors)
                nearby_airports.extend(rows)

                if not getattr(self.driver, "supports_interaction", True):
                    break

                # Try to click the "Next" pagination button
                advanced = self._go_to_next_page()
                if not advanced:
                    break
                time.sleep(1.5)
                page_num += 1

            if nearby_airports:
                deduplicated = []
                seen = set()
                for airport in nearby_airports:
                    key = (
                        airport.get("icao") or airport.get("source_airport_id"),
                        airport.get("url"),
                        tuple(airport.get("raw_cells") or []),
                    )
                    if key not in seen:
                        seen.add(key)
                        deduplicated.append(airport)
                nearby_airports = deduplicated
                observed_fields.append("nearby_airports")
            else:
                missing_fields.append("nearby_airports")

            extra_data: Dict[str, Any] = {}
            if map_markers:
                self.apply_map_markers(nearby_airports, map_markers)
                extra_data["map_markers"] = map_markers
                expected_count = len([m for m in map_markers if not m.get("is_origin")])
                extra_data["expected_count"] = expected_count
                observed_fields.append("map_markers")
            # An empty list is a real answer (no airports nearby), not a failure.
            table_failed = any(e.get("field") == "nearby_table" for e in errors)
            scrape_status = "PARTIAL" if table_failed and not nearby_airports else "SUCCESS"
            if (
                map_markers
                and getattr(self.driver, "supports_interaction", True)
                and extra_data["expected_count"] != len(nearby_airports)
            ):
                # Browser mode walked every page itself, so the count is final.
                scrape_status = "PARTIAL"
                errors.append({
                    "field": "nearby_count",
                    "error": f"nearby row count {len(nearby_airports)} does not match expected {extra_data['expected_count']}",
                })
            elif not getattr(self.driver, "supports_interaction", True) and (
                map_markers and not nearby_airports and extra_data["expected_count"]
            ):
                scrape_status = "PARTIAL"
                errors.append({
                    "field": "nearby_count",
                    "error": f"no nearby rows parsed but the map lists {extra_data['expected_count']}",
                })

            return {
                "entity_type": "nearby_airports",
                "external_id": f"acukwik_nearby_{icao}",
                "url": url,
                "scrape_status": scrape_status,
                "data": {
                    "associated_airports": [icao],
                    "nearby_airports": nearby_airports,
                    **extra_data,
                    "raw_text": "\n".join(
                        line.strip()
                        for line in self.driver.find_element(By.TAG_NAME, "body").text.splitlines()
                        if line.strip()
                    ),
                    "links": [
                        {"text": " ".join(link.text.split()), "url": urljoin(url, link.get_attribute("href"))}
                        for link in self.driver.find_elements(By.TAG_NAME, "a")
                        if link.get_attribute("href")
                    ],
                },
                "observed_fields": list(set(observed_fields)),
                "missing_fields": list(set(missing_fields)),
                "errors": errors,
            }

        except Exception as e:
            logger.error(f"NearbyParser error for {url}: {e}")
            return {
                "entity_type": "nearby_airports",
                "external_id": f"acukwik_nearby_{icao}",
                "url": url,
                "scrape_status": "FAILED",
                "data": {
                    "associated_airports": [icao] if icao else [],
                    "nearby_airports": [],
                },
                "observed_fields": [],
                "missing_fields": ["nearby_airports"],
                "errors": [{"error": str(e)}],
            }

    def _extract_table_rows(self, errors: List[Dict]) -> List[Dict[str, Any]]:
        """Extract all rows from the nearby airports table or div.result blocks on the current page."""
        rows: List[Dict[str, Any]] = []
        try:
            # Try table-based extraction first
            table_rows = self.driver.find_elements(
                By.XPATH,
                "//table[.//th[contains(text(),'Airport') or contains(text(),'Rwy') or contains(text(),'City')]]//tr[position()>1 and td]"
            )
            if table_rows:
                for row in table_rows:
                    try:
                        cells = row.find_elements(By.TAG_NAME, "td")
                        if len(cells) < 2:
                            continue
                        entry: Dict[str, Any] = {}
                        entry["raw_cells"] = [cell.text.strip() for cell in cells]
                        entry["links"] = [
                            {"text": " ".join(link.text.split()), "url": self._absolute_url(link.get_attribute("href"))}
                            for link in row.find_elements(By.TAG_NAME, "a")
                            if link.get_attribute("href")
                        ]
                        # Column 0: Airport ICAO (may be a link)
                        icao_cell = cells[0]
                        try:
                            link = icao_cell.find_element(By.TAG_NAME, "a")
                            href = link.get_attribute("href")
                            source_match = re.search(
                                r"/Airport-Info/([^/?#]+)", href or "", re.IGNORECASE
                            )
                            identifier = source_match.group(1) if source_match else link.text.strip()
                            if re.fullmatch(r"[A-Z]{4}", identifier):
                                entry["icao"] = identifier
                            else:
                                entry["source_airport_id"] = identifier
                                entry["name"] = link.text.strip()
                            if href:
                                entry["url"] = self._absolute_url(href)
                        except Exception:
                            identifier = icao_cell.text.strip()
                            if re.fullmatch(r"[A-Z]{4}", identifier):
                                entry["icao"] = identifier
                            elif identifier:
                                entry["source_airport_id"] = identifier
                        # Column 1: Primary Runway
                        if len(cells) > 1:
                            entry["primary_runway"] = cells[1].text.strip()
                        # Column 2: Type
                        if len(cells) > 2:
                            entry["airport_type"] = cells[2].text.strip()
                        # Column 3: City
                        if len(cells) > 3:
                            entry["city"] = cells[3].text.strip()
                        if entry.get("icao") or entry.get("source_airport_id"):
                            rows.append(entry)
                    except Exception as e:
                        logger.debug(f"Nearby row parse error: {e}")
                        continue
            else:
                # Try div-based extraction for .result blocks
                result_divs = self.driver.find_elements(By.CSS_SELECTOR, "div.result")
                for div in result_divs:
                    try:
                        entry: Dict[str, Any] = {}
                        entry["raw_cells"] = [
                            child.text.strip()
                            for child in div.find_elements(By.XPATH, "./div")
                            if child.text.strip()
                        ]
                        entry["raw_text"] = "\n".join(
                            line.strip() for line in div.text.splitlines() if line.strip()
                        )
                        entry["links"] = [
                            {"text": " ".join(link.text.split()), "url": self._absolute_url(link.get_attribute("href"))}
                            for link in div.find_elements(By.TAG_NAME, "a")
                            if link.get_attribute("href")
                        ]
                        # ICAO and name: "LESU - Andorra-La Seu d'Urgell". Split on
                        # the first " - " only; names contain hyphens.
                        icao_link = div.find_element(By.CSS_SELECTOR, ".col2 a")
                        href = icao_link.get_attribute("href")
                        link_text = " ".join(icao_link.text.split())
                        code_part, separator, name_part = link_text.partition(" - ")
                        source_match = re.search(
                            r"/Airport-Info/([^/?#]+)", href or "", re.IGNORECASE
                        )
                        identifier = (
                            source_match.group(1)
                            if source_match
                            else code_part.strip()
                        )
                        if re.fullmatch(r"[A-Z]{4}", identifier):
                            entry["icao"] = identifier
                        else:
                            entry["source_airport_id"] = identifier
                        entry["name"] = name_part.strip() if separator else ""
                        entry["url"] = self._absolute_url(href)
                        # Primary Runway
                        entry["primary_runway"] = div.find_element(By.CSS_SELECTOR, ".col3.w15p.fl.p10px").text.strip()
                        # Type
                        col3s = div.find_elements(By.CSS_SELECTOR, ".col3.w15p.fl.p10px")
                        if len(col3s) > 1:
                            entry["airport_type"] = col3s[1].text.strip()
                        else:
                            entry["airport_type"] = ""
                        # City
                        entry["city"] = div.find_element(By.CSS_SELECTOR, ".col4").text.strip()
                        if entry.get("icao") or entry.get("source_airport_id"):
                            rows.append(entry)
                    except Exception as e:
                        logger.debug(f"Nearby div parse error: {e}")
                        continue
        except Exception as e:
            errors.append({"field": "nearby_table", "error": str(e)})
        return rows

    def _absolute_url(self, href: Optional[str]) -> Optional[str]:
        """Resolve a row link against the nearby page URL."""
        if not href:
            return href
        base = getattr(self, "_base_url", None) or getattr(self.driver, "current_url", "") or ""
        if not base or base == "about:blank":
            return href
        return urljoin(base, href)

    _MARKER_CALL = re.compile(
        r"addMarker(\w+)\(\s*map\s*,\s*([-+\d.]+)\s*,\s*([-+\d.]+)\s*,\s*'((?:[^'\\]|\\.)*)'"
    )

    @classmethod
    def extract_map_markers(cls, page_source: str, url: str = "") -> List[Dict[str, Any]]:
        """Parse the nearby map's addMarker*(map, lat, lon, '<table class="Mapdata">...')
        calls, one entry per distinct /Airport-Info/ ID (the script repeats them).

        The yellow marker, or the ID in the page URL, is the origin airport.
        """
        markers: List[Dict[str, Any]] = []
        seen = set()
        origin_match = re.search(r"/Nearby/([^/?#]+)", url or "", re.IGNORECASE)
        origin_id = origin_match.group(1) if origin_match else None
        for match in cls._MARKER_CALL.finditer(page_source or ""):
            colour, lat, lon, popup = match.groups()
            popup = popup.replace("\\'", "'")
            id_match = re.search(r"/Airport-Info/([^/?#\"']+)", popup, re.IGNORECASE)
            if not id_match:
                continue
            source_id = id_match.group(1)
            if source_id in seen:
                continue
            seen.add(source_id)
            marker: Dict[str, Any] = {
                "source_airport_id": source_id,
                "marker_category": colour.lower(),
            }
            try:
                marker["latitude"] = float(lat)
                marker["longitude"] = float(lon)
            except ValueError:
                marker["latitude_raw"], marker["longitude_raw"] = lat, lon
            if re.fullmatch(r"[A-Z]{4}", source_id):
                marker["icao"] = source_id
            try:
                table = lxml_html.fragment_fromstring(popup, create_parent="div")
            except Exception:
                table = None
            fields: Dict[str, str] = {}
            if table is not None:
                for tr in table.iter("tr"):
                    cells = [" ".join(td.text_content().split()) for td in tr.findall("td")]
                    if len(cells) == 2 and cells[0]:
                        fields[cells[0]] = cells[1]
                for link in table.iter("a"):
                    href = (link.get("href") or "").strip()
                    if "/airport-info/" in href.lower():
                        marker["url"] = urljoin(url, href) if url else href
                    elif href and "website" not in marker:
                        marker["website"] = urljoin(url, href) if url else href
            if fields.get("Name"):
                marker["name"] = fields["Name"]
            codes = fields.get("ICAO-IATA")
            if codes:
                marker["codes_raw"] = codes
                _, separator, iata = codes.partition(" - ")
                if separator and iata.strip():
                    marker["iata"] = iata.strip()
            runway = fields.get("Rwy Length")
            if runway:
                runway_digits = runway.replace(",", "").strip()
                marker["runway_length_ft"] = int(runway_digits) if runway_digits.isdigit() else runway
            if "24 hours" in fields:
                marker["hours_24"] = fields["24 hours"]
            marker["is_origin"] = (
                source_id == origin_id if origin_id else colour.lower() == "yellow"
            )
            if fields:
                marker["raw_fields"] = fields
            markers.append(marker)
        return markers

    _MARKER_ROW_FIELDS = (
        "latitude", "longitude", "iata", "runway_length_ft", "website", "hours_24", "marker_category",
    )

    @classmethod
    def apply_map_markers(cls, rows: List[Dict[str, Any]], markers: List[Dict[str, Any]]) -> int:
        """Copy marker fields onto list rows with the same source ID; returns rows matched.

        Public so rows from later list pages (which carry no map script) can be
        enriched from the first page's markers.
        """
        by_id = {marker["source_airport_id"]: marker for marker in markers or []}
        matched = 0
        for row in rows:
            row_id = row.get("icao") or row.get("source_airport_id")
            if not row_id:
                url_match = re.search(r"/Airport-Info/([^/?#]+)", row.get("url") or "", re.IGNORECASE)
                row_id = url_match.group(1) if url_match else None
            marker = by_id.get(row_id)
            if not marker:
                continue
            matched += 1
            for field in cls._MARKER_ROW_FIELDS:
                if field in marker and field not in row:
                    row[field] = marker[field]
        return matched

    def _go_to_next_page(self) -> bool:
        """
        Click the 'Next' pagination button for all known nearby layouts.
        Returns True if navigation happened, False if no next page found.
        """
        # Try multiple strategies for pagination
        try:
            # 1. Standard 'Next' button (enabled)
            next_btns = self.driver.find_elements(By.XPATH, "//a[contains(@id,'lbtnNext') and not(contains(@class,'aspNetDisabled'))]")
            for btn in next_btns:
                if btn.is_displayed() and btn.is_enabled():
                    self.driver.execute_script("arguments[0].click();", btn)
                    return True
            # 2. Generic '>' or 'Next' text
            next_btns = self.driver.find_elements(By.XPATH, "//a[(normalize-space(text())='>' or normalize-space(text())='Next') and not(contains(@class,'aspNetDisabled'))]")
            for btn in next_btns:
                if btn.is_displayed() and btn.is_enabled():
                    self.driver.execute_script("arguments[0].click();", btn)
                    return True
            # 3. Any enabled LinkPaging class
            next_btns = self.driver.find_elements(By.XPATH, "//a[contains(@class,'LinkPaging') and not(contains(@class,'aspNetDisabled'))]")
            for btn in next_btns:
                if btn.is_displayed() and btn.is_enabled():
                    # Only click if not already active
                    if 'active' not in btn.get_attribute('class'):
                        self.driver.execute_script("arguments[0].click();", btn)
                        return True
        except Exception:
            pass
        return False


# Legacy compatibility - keep old interfaces
class AirportParser:
    """Legacy wrapper for backward compatibility."""
    def __init__(self, driver: WebDriver):
        self.parser = AirportPageParser(driver)
    
    def parse(self, url: str) -> Dict[str, Any]:
        """Return first entity (airport) for compatibility."""
        entities = self.parser.parse(url)
        return entities[0] if entities else {}


class OrganizationParser:
    """Legacy wrapper for backward compatibility."""
    def __init__(self, driver: WebDriver):
        self.driver = driver
    
    def parse(self, url: str, associated_airport: Optional[str] = None) -> Dict[str, Any]:
        """Minimal organization parser for compatibility."""
        return {
            "entity_type": "organization",
            "external_id": f"acukwik_org_{hashlib.sha256(url.encode('utf-8')).hexdigest()[:20]}",
            "url": url,
            "scrape_status": "SUCCESS",
            "data": {}
        }
