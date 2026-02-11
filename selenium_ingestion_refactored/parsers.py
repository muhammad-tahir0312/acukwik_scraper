"""
ETL-compliant parsers for aviation data extraction.
Focuses on raw data ingestion without transformation.
"""
import logging
import re
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

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
    
    Airport: acukwik_UBBB
    Organization: acukwik_org_ASG_BUSINESS_AVIATION_UBBB
    """
    if entity_type == "airport":
        icao = data.get('icao')
        if icao:
            return f"acukwik_{icao}"
        # Fallback to URL hash
        return f"acukwik_{abs(hash(data.get('url', '')))}"
    
    elif entity_type == "organization":
        # Slugify org name
        name = data.get('name', '').upper()
        slug = name.replace(' ', '_').replace('-', '_')
        slug = re.sub(r'[^A-Z0-9_]', '', slug)
        
        if slug and airport_icao:
            return f"acukwik_org_{slug}_{airport_icao}"
        elif slug:
            return f"acukwik_org_{slug}"
        else:
            return f"acukwik_org_{abs(hash(name))}"
    
    return f"acukwik_{entity_type}_{abs(hash(str(data)))}"


def determine_scrape_status(observed_fields: List[str], errors: List[Dict]) -> str:
    """
    Determine scrape status based on observed fields and errors.
    
    SUCCESS: Core fields present, no errors
    PARTIAL: Some core fields missing
    FAILED: Critical errors or no core fields
    """
    core_fields = {"icao", "name"}
    
    if errors:
        return "FAILED"
    
    if core_fields.issubset(set(observed_fields)):
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


class AirportPageParser:
    """
    Parser for acukwik.com airport pages.
    Extracts both airport entity and all organization entities (FBOs, hotels, etc.)
    """
    
    def __init__(self, driver: WebDriver, source: str = "acukwik"):
        self.driver = driver
        self.source = source
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
            
            # Extract ICAO for organization linking
            airport_icao = airport_entity.get('data', {}).get('icao')
            
            # Parse organization entities on the same page
            org_entities = self._parse_organization_entities(url, airport_icao)
            entities.extend(org_entities)
            
        except Exception as e:
            logger.error(f"Error parsing airport page: {str(e)}")
            # Return error entity
            entities.append({
                "source": self.source,
                "entity_type": "airport",
                "external_id": f"acukwik_error_{abs(hash(url))}",
                "scraped_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
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
        raw_name = self._extract_raw_name(observed_fields, missing_fields, errors)
        name = clean_airport_name(raw_name) if raw_name else None
        city, country = self._extract_city_country(observed_fields, missing_fields, errors)
        
        # Extract additional data from paired divs
        additional_data = self._extract_additional_data(observed_fields, missing_fields, errors)
        
        # Build data dictionary
        data = {
            "icao": icao,
            "iata": iata,
            "name": name,
            "city": city,
            "country": country,
        }
        
        # Add additional data fields
        data.update(additional_data)
        
        # Validate and clean
        data = validate_and_clean(data)
        
        # Remove None values (don't include unobserved fields)
        data = {k: v for k, v in data.items() if v is not None}
        
        # Generate external ID
        external_id = generate_external_id("airport", data)
        
        # Determine status
        scrape_status = determine_scrape_status(observed_fields, errors)
        
        return {
            "source": self.source,
            "entity_type": "airport",
            "external_id": external_id,
            "scraped_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "url": url,
            "scrape_status": scrape_status,
            "scrape_duration_ms": None,  # Will be filled by scraper
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
                iata = h3_text[iata_start:].strip().upper()
                
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
        
        # Section mapping: section header → role
        section_roles = {
            "FBOs": "FBO",
            "Handlers": "HANDLER",
            "Supervising Agents": "SUPERVISING_AGENT",
            "Fuel Only": "FUEL_SUPPLIER",
            "Flight Support Organizations": "FLIGHT_SUPPORT",
            "Caterers": "CATERING",
            "Limo": "GROUND_TRANSPORTATION",
            "Maintenance": "MAINTENANCE",
            "Hotels": "HOTEL",
            "Car Rental": "CAR_RENTAL",
        }
        
        # Try to find each section and extract vendors
        for section_name, role in section_roles.items():
            try:
                orgs = self._extract_vendors_from_section(section_name, role, url, airport_icao)
                organizations.extend(orgs)
            except Exception as e:
                logger.debug(f"Error extracting {section_name}: {e}")
        
        return organizations
    
    def _extract_vendors_from_section(self, section_name: str, role: str, 
                                     url: str, airport_icao: Optional[str]) -> List[Dict[str, Any]]:
        """Extract vendor organizations from a specific section."""
        vendors = []
        
        try:
            # Hotels have special handling
            if section_name == "Hotels":
                return self._extract_hotels(url, airport_icao)
            
            # Car Rental has special handling
            if section_name == "Car Rental":
                return self._extract_car_rentals(url, airport_icao)
            
            # Find section by heading text
            section_xpath = f"//h3[contains(text(), '{section_name}')] | //h2[contains(text(), '{section_name}')]"
            section_elements = self.driver.find_elements(By.XPATH, section_xpath)
            
            if not section_elements:
                return vendors
            
            # Find all .vendor blocks in this section
            vendor_blocks = self.driver.find_elements(By.CSS_SELECTOR, ".vendor, .advertPR .vendor")
            
            for vendor_block in vendor_blocks:
                try:
                    org = self._extract_single_vendor(vendor_block, role, url, airport_icao)
                    if org:
                        vendors.append(org)
                except Exception as e:
                    logger.debug(f"Error extracting single vendor: {e}")
                    continue
        
        except Exception as e:
            logger.debug(f"Error in section {section_name}: {e}")
        
        return vendors
    
    def _extract_single_vendor(self, vendor_block, role: str, url: str, 
                               airport_icao: Optional[str]) -> Optional[Dict[str, Any]]:
        """Extract single vendor/organization from a block using DOM traversal."""
        
        observed_fields = []
        missing_fields = []
        errors = []
        
        try:
            # Extract name (from strong.fs18px or first strong)
            name_elem = vendor_block.find_element(By.CSS_SELECTOR, "strong.fs18px, .vendorName strong, strong")
            name = name_elem.text.strip()
            
            if not name:
                return None
            
            # Filter out generic labels
            generic_labels = ["Address", "Phone", "Fax", "Email", "Website", "INTERNATIONAL", 
                            "Distance", "Price range", "Remarks", "SITA", "Frequency", "Brand"]
            if name in generic_labels or len(name) < 3:
                return None
            
            observed_fields.append("name")
            
            # Parse contact info using DOM traversal
            contacts = []
            address_data = {}
            sita_code = None
            aftn_code = None
            remarks = None
            brands = []
            
            # Find all contact rows (div.clearfix.mb9px or variations)
            contact_rows = vendor_block.find_elements(By.XPATH, ".//div[contains(@class, 'clearfix') and (contains(@class, 'mb9px') or contains(@class, 'mb9pxx'))]")
            
            for row in contact_rows:
                try:
                    # Extract label and value elements
                    label_elem = row.find_element(By.CSS_SELECTOR, "div.fl.w35p.bold, div.fl.w35p")
                    label = label_elem.text.strip().lower().replace(':', '')
                    
                    value_elem = row.find_element(By.CSS_SELECTOR, "div.fl.w65p, div.fl.w60p")
                    
                    # Email - check for button first
                    if label == "email":
                        try:
                            button = value_elem.find_element(By.CSS_SELECTOR, "button.ghEmail, button.sEmail")
                            # Email behind button
                            errors.append({"field": "email", "error": "Email requires button click (ghEmail/sEmail)"})
                            missing_fields.append("email")
                        except:
                            # Try direct email link
                            try:
                                email_link = value_elem.find_element(By.CSS_SELECTOR, "a[href^='mailto:']")
                                email = email_link.text.strip()
                                if email:
                                    contacts.append({"type": "email", "value": email})
                                    observed_fields.append("email")
                            except:
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
                    elif label == "phone":
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
                "source": "acukwik",
                "entity_type": "organization",
                "external_id": external_id,
                "scraped_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "url": url,
                "scrape_status": scrape_status,
                "scrape_duration_ms": None,
                "data": data,
                "observed_fields": list(set(observed_fields)),
                "missing_fields": list(set(missing_fields)),
                "errors": errors
            }
            
        except Exception as e:
            logger.debug(f"Error extracting vendor: {e}")
            return None
    
    def _parse_address(self, address_text: str) -> Dict[str, Any]:
        """
        Parse address into structured components.
        If parsing fails, store as 'full' only.
        """
        address = {"full": address_text}
        
        try:
            # Try to extract components
            lines = [line.strip() for line in address_text.split('\n') if line.strip()]
            
            # Last line often has country and postal code
            if lines:
                last_line = lines[-1]
                
                # Extract postal code (various formats)
                postal_match = re.search(r'([A-Z]{2,3}[-\s]?\d{3,5})', last_line, re.IGNORECASE)
                if postal_match:
                    address['postal_code'] = postal_match.group(1)
                
                # Extract country (uppercase words at end)
                country_match = re.search(r'([A-Z\s]{3,})$', last_line)
                if country_match:
                    address['country'] = country_match.group(1).strip()
                
                # City (second to last, or before country in last line)
                if len(lines) > 1:
                    second_last = lines[-2]
                    # City might be in second-to-last line
                    city_match = re.search(r'^([A-Za-z\s]+)', second_last)
                    if city_match:
                        address['city'] = city_match.group(1).strip()
                
                # Street (first line or lines)
                if len(lines) > 2:
                    address['street'] = ', '.join(lines[:-2])
                elif len(lines) == 2:
                    address['street'] = lines[0]
        
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
                    name_elem = hotel_row.find_element(By.CSS_SELECTOR, "div.fs18px.bold, .bold.fs18px")
                    name = name_elem.text.strip()
                    
                    if not name or len(name) < 3:
                        continue
                    
                    # Filter out generic labels
                    generic_labels = ["Phone", "Fax", "Address", "Distance", "Price range", "Name and Contact Info"]
                    if name in generic_labels:
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
                        "source": "acukwik",
                        "entity_type": "organization",
                        "external_id": external_id,
                        "scraped_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        "url": url,
                        "scrape_status": "SUCCESS" if len(missing_fields) < 2 else "PARTIAL",
                        "scrape_duration_ms": None,
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
                        "source": "acukwik",
                        "entity_type": "organization",
                        "external_id": external_id,
                        "scraped_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                        "url": url,
                        "scrape_status": "SUCCESS" if len(missing_fields) < 2 else "PARTIAL",
                        "scrape_duration_ms": None,
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


# Legacy compatibility - keep old interfaces
class AirportParser:
    """Legacy wrapper for backward compatibility."""
    def __init__(self, driver: WebDriver, source: str = "acukwik"):
        self.parser = AirportPageParser(driver, source)
    
    def parse(self, url: str) -> Dict[str, Any]:
        """Return first entity (airport) for compatibility."""
        entities = self.parser.parse(url)
        return entities[0] if entities else {}


class OrganizationParser:
    """Legacy wrapper for backward compatibility."""
    def __init__(self, driver: WebDriver, source: str = "acukwik"):
        self.driver = driver
        self.source = source
    
    def parse(self, url: str, associated_airport: Optional[str] = None) -> Dict[str, Any]:
        """Minimal organization parser for compatibility."""
        return {
            "source": self.source,
            "entity_type": "organization",
            "external_id": f"acukwik_org_{abs(hash(url))}",
            "scraped_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "url": url,
            "scrape_status": "SUCCESS",
            "data": {}
        }
