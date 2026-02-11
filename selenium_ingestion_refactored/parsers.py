"""
Data parsers for different entity types.
Extracts structured data from web elements.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import re

logger = logging.getLogger(__name__)


class BaseParser:
    """Base class for entity parsers."""
    
    def __init__(self, driver: WebDriver, source: str = "acukwik"):
        self.driver = driver
        self.source = source
        self.wait = WebDriverWait(driver, 10)
    
    def safe_find_text(self, selectors: List[str], default: str = "") -> str:
        """Safely find text using multiple selector strategies."""
        for selector in selectors:
            try:
                if selector.startswith("//"):
                    element = self.driver.find_element(By.XPATH, selector)
                else:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                text = element.text.strip()
                if text:
                    logger.debug(f"Found text with selector '{selector}': {text[:50]}")
                    return text
            except (NoSuchElementException, TimeoutException):
                continue
        logger.debug(f"No text found with any selector: {selectors}")
        return default
    
    def safe_find_attribute(self, selectors: List[str], attribute: str, default: str = "") -> str:
        """Safely find element attribute using multiple selector strategies."""
        for selector in selectors:
            try:
                if selector.startswith("//"):
                    element = self.driver.find_element(By.XPATH, selector)
                else:
                    element = self.driver.find_element(By.CSS_SELECTOR, selector)
                attr_value = element.get_attribute(attribute)
                if attr_value:
                    return attr_value.strip()
            except (NoSuchElementException, TimeoutException):
                continue
        return default
    
    def extract_emails(self, text: str) -> List[str]:
        """Extract email addresses from text."""
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        return list(set(re.findall(email_pattern, text)))
    
    def extract_phones(self, text: str) -> List[str]:
        """Extract phone numbers from text."""
        phone_pattern = r'[\+\(]?[1-9][0-9 .\-\(\)]{8,}[0-9]'
        phones = re.findall(phone_pattern, text)
        return [p.strip() for p in phones if len(re.sub(r'\D', '', p)) >= 7]
    
    def clean_text(self, text: str) -> str:
        """Clean and normalize text."""
        if not text:
            return ""
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', '', text)
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove special characters that might cause issues
        text = text.strip()
        return text


class AirportParser(BaseParser):
    """Parser for airport entities."""
    
    def parse(self, url: str) -> Dict[str, Any]:
        """
        Parse airport data from the current page.
        
        Returns:
            Structured airport data
        """
        logger.info(f"Parsing airport data from: {url}")
        
        try:
            # Wait for page to load
            self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            
            # DEBUG: Save HTML to inspect actual structure (only first time)
            debug_file = Path(__file__).parent / "errors" / "airport_page_sample.html"
            if not debug_file.exists():
                debug_file.parent.mkdir(exist_ok=True)
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                logger.info(f"DEBUG: Saved page HTML to {debug_file}")
            
            # Extract airport code from URL or page
            external_id = self._extract_airport_id(url)
            
            # Extract basic airport information
            data = {
                "icao": self._extract_icao(),
                "iata": self._extract_iata(),
                "name": self._extract_name(),
                "city": self._extract_city(),
                "country": self._extract_country(),
                "latitude": self._extract_latitude(),
                "longitude": self._extract_longitude(),
                "elevation": self._extract_elevation(),
                "timezone": self._extract_timezone(),
                "runways": self._extract_runways(),
                "frequencies": self._extract_frequencies(),
            }
            
            return {
                "source": self.source,
                "entity_type": "airport",
                "external_id": external_id,
                "scraped_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "url": url,
                "data": data
            }
            
        except Exception as e:
            logger.error(f"Error parsing airport data: {str(e)}")
            raise
    
    def _extract_airport_id(self, url: str) -> str:
        """Extract airport ID from URL."""
        # Try to extract from URL pattern
        match = re.search(r'/airport/([A-Z0-9]+)', url, re.IGNORECASE)
        if match:
            return f"{self.source}_{match.group(1).upper()}"
        
        # Fallback: use ICAO if available
        icao = self._extract_icao()
        if icao:
            return f"{self.source}_{icao}"
        
        # Last resort: hash of URL
        import hashlib
        return f"{self.source}_{hashlib.md5(url.encode()).hexdigest()[:8]}"
    
    def _extract_icao(self) -> Optional[str]:
        """Extract ICAO code from h3 tag."""
        # Format: "ICAO - UBBB, IATA - GYD" or just "ICAO - UBBB"
        try:
            h3_text = self.driver.find_element(By.CSS_SELECTOR, "h3").text
            if "ICAO -" in h3_text:
                icao_start = h3_text.find("ICAO -") + len("ICAO - ")
                icao_end = h3_text.find(",", icao_start) if "," in h3_text[icao_start:] else len(h3_text)
                icao = h3_text[icao_start:icao_end].strip()
                return icao.upper() if icao and len(icao) == 4 else None
        except (NoSuchElementException, Exception) as e:
            logger.debug(f"Could not extract ICAO: {e}")
        return None
    
    def _extract_iata(self) -> Optional[str]:
        """Extract IATA code from h3 tag."""
        # Format: "ICAO - UBBB, IATA - GYD"
        try:
            h3_text = self.driver.find_element(By.CSS_SELECTOR, "h3").text
            if "IATA -" in h3_text:
                iata_start = h3_text.find("IATA -") + len("IATA - ")
                iata = h3_text[iata_start:].strip()
                return iata.upper() if iata and len(iata) == 3 else None
        except (NoSuchElementException, Exception) as e:
            logger.debug(f"Could not extract IATA: {e}")
        return None
    
    def _extract_name(self) -> str:
        """Extract airport name from h1 tag."""
        try:
            h1_text = self.driver.find_element(By.CSS_SELECTOR, "h1").text
            return self.clean_text(h1_text)
        except (NoSuchElementException, Exception) as e:
            logger.warning(f"Could not extract airport name: {e}")
            return ""
    
    def _extract_city(self) -> str:
        """Extract city from h2 tag (format: City, Country)."""
        try:
            h2_text = self.driver.find_element(By.CSS_SELECTOR, "h2").text
            if "," in h2_text:
                parts = h2_text.split(",", 1)
                return self.clean_text(parts[0])
            return self.clean_text(h2_text)
        except (NoSuchElementException, Exception) as e:
            logger.debug(f"Could not extract city: {e}")
            return ""
    
    def _extract_country(self) -> str:
        """Extract country from h2 tag (format: City, Country)."""
        try:
            h2_text = self.driver.find_element(By.CSS_SELECTOR, "h2").text
            if "," in h2_text:
                parts = h2_text.split(",", 1)
                return self.clean_text(parts[1]) if len(parts) > 1 else ""
            return ""
        except (NoSuchElementException, Exception) as e:
            logger.debug(f"Could not extract country: {e}")
            return ""
    
    def _extract_latitude(self) -> Optional[float]:
        """Extract latitude from airport info section."""
        # Look in .clearfix.mb20px.mainInfo .table or div.clearboth pairs
        try:
            # Try additional airport data first
            tables = self.driver.find_elements(By.CSS_SELECTOR, ".clearfix.mb20px.mainInfo .table")
            for table in tables:
                try:
                    key = table.find_element(By.CSS_SELECTOR, ".bold").text.strip()
                    if "latitude" in key.lower():
                        value = table.find_element(By.CSS_SELECTOR, ".w45p, .w38p").text.strip()
                        # Parse various formats
                        lat_text = re.sub(r'[^\d\.\-]', '', value.split()[0] if value else "")
                        return float(lat_text) if lat_text else None
                except:
                    continue
        except Exception as e:
            logger.debug(f"Could not extract latitude: {e}")
        return None
    
    def _extract_longitude(self) -> Optional[float]:
        """Extract longitude from airport info section."""
        try:
            tables = self.driver.find_elements(By.CSS_SELECTOR, ".clearfix.mb20px.mainInfo .table")
            for table in tables:
                try:
                    key = table.find_element(By.CSS_SELECTOR, ".bold").text.strip()
                    if "longitude" in key.lower():
                        value = table.find_element(By.CSS_SELECTOR, ".w45p, .w38p").text.strip()
                        lon_text = re.sub(r'[^\d\.\-]', '', value.split()[0] if value else "")
                        return float(lon_text) if lon_text else None
                except:
                    continue
        except Exception as e:
            logger.debug(f"Could not extract longitude: {e}")
        return None
    
    def _extract_elevation(self) -> Optional[int]:
        """Extract elevation from airport info section."""
        try:
            tables = self.driver.find_elements(By.CSS_SELECTOR, ".clearfix.mb20px.mainInfo .table")
            for table in tables:
                try:
                    key = table.find_element(By.CSS_SELECTOR, ".bold").text.strip()
                    if "elevation" in key.lower() or "altitude" in key.lower():
                        value = table.find_element(By.CSS_SELECTOR, ".w45p, .w38p").text.strip()
                        elev_text = re.sub(r'[^\d]', '', value)
                        return int(elev_text) if elev_text else None
                except:
                    continue
        except Exception as e:
            logger.debug(f"Could not extract elevation: {e}")
        return None
    
    def _extract_timezone(self) -> Optional[str]:
        """Extract timezone from airport info section."""
        try:
            tables = self.driver.find_elements(By.CSS_SELECTOR, ".clearfix.mb20px.mainInfo .table")
            for table in tables:
                try:
                    key = table.find_element(By.CSS_SELECTOR, ".bold").text.strip()
                    if "timezone" in key.lower() or "time zone" in key.lower():
                        value = table.find_element(By.CSS_SELECTOR, ".w45p, .w38p").text.strip()
                        return value if value else None
                except:
                    continue
        except Exception as e:
            logger.debug(f"Could not extract timezone: {e}")
        return None
    
    def _extract_runways(self) -> List[Dict[str, Any]]:
        """Extract runway information from airport info section."""
        runways = []
        try:
            # Check in the additional airport data section
            # Often runway info is in div.clearboth.p3xp.bold and div.clearboth.p3px pairs
            keys = self.driver.find_elements(By.CSS_SELECTOR, "div.clearboth.p3xp.bold")
            values = self.driver.find_elements(By.CSS_SELECTOR, "div.clearboth.p3px")
            
            for key_elem, value_elem in zip(keys, values):
                try:
                    key = key_elem.text.strip().replace("\xa0", "")
                    value = value_elem.text.strip().replace("\xa0", "")
                    
                    if "runway" in key.lower():
                        runways.append({
                            "designation": key,
                            "details": value
                        })
                except:
                    continue
        except Exception as e:
            logger.debug(f"Could not extract runways: {e}")
        return runways
    
    def _extract_frequencies(self) -> List[Dict[str, Any]]:
        """Extract frequency information from contact info section."""
        frequencies = []
        try:
            # Frequencies are in div.results-content div.clearfix.result
            contact_elements = self.driver.find_elements(By.CSS_SELECTOR, "div.results-content div.clearfix.result")
            for contact in contact_elements:
                try:
                    # Contact name
                    name_elem = contact.find_element(By.CSS_SELECTOR, "div.w31p.fl.bold")
                    name = name_elem.text.strip()
                    
                    # Frequency
                    try:
                        freq_elem = contact.find_element(By.CSS_SELECTOR, "div.w17p.fl.p3px div")
                        frequency = freq_elem.text.strip()
                    except:
                        frequency = None
                    
                    if frequency:
                        frequencies.append({
                            "type": name,
                            "frequency": frequency
                        })
                except:
                    continue
        except Exception as e:
            logger.debug(f"Could not extract frequencies: {e}")
        return frequencies


class OrganizationParser(BaseParser):
    """Parser for organization entities (FBO, hotels, fuel suppliers, etc.)."""
    
    def parse(self, url: str, associated_airport: Optional[str] = None) -> Dict[str, Any]:
        """
        Parse organization data from the current page.
        
        Args:
            url: URL of the organization page
            associated_airport: ICAO code of associated airport (if known)
        
        Returns:
            Structured organization data
        """
        logger.info(f"Parsing organization data from: {url}")
        
        try:
            # Wait for page to load
            self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            
            # Extract organization ID
            external_id = self._extract_org_id(url)
            
            # Extract organization information
            data = {
                "name": self._extract_name(),
                "description": self._extract_description(),
                "website": self._extract_website(),
                "email": self._extract_email(),
                "phone": self._extract_phone(),
                "services": self._extract_services(),
                "roles": self._extract_roles(),
                "associated_airports": self._extract_associated_airports(associated_airport),
                "address": self._extract_address(),
                "hours": self._extract_hours(),
            }
            
            return {
                "source": self.source,
                "entity_type": "organization",
                "external_id": external_id,
                "scraped_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "url": url,
                "data": data
            }
            
        except Exception as e:
            logger.error(f"Error parsing organization data: {str(e)}")
            raise
    
    def _extract_org_id(self, url: str) -> str:
        """Extract organization ID from URL."""
        # Try to extract from URL pattern
        match = re.search(r'/(fbo|handler|hotel|fuel)/([A-Z0-9_-]+)', url, re.IGNORECASE)
        if match:
            return f"{self.source}_{match.group(2)}"
        
        # Fallback: hash of URL
        import hashlib
        return f"{self.source}_org_{hashlib.md5(url.encode()).hexdigest()[:8]}"
    
    def _extract_name(self) -> str:
        """Extract organization name."""
        selectors = [
            "h1",
            ".organization-name",
            ".fbo-name",
            "//h1[@class='page-title']",
            "//div[@class='org-header']//h1"
        ]
        return self.clean_text(self.safe_find_text(selectors))
    
    def _extract_description(self) -> str:
        """Extract organization description."""
        selectors = [
            ".description",
            ".about",
            "//div[contains(@class, 'description')]",
            "//div[contains(@class, 'about')]//p"
        ]
        return self.clean_text(self.safe_find_text(selectors))
    
    def _extract_website(self) -> Optional[str]:
        """Extract website URL."""
        selectors = [
            "//a[contains(text(), 'Website')]/@href",
            "//span[contains(text(), 'Website')]/following-sibling::a/@href",
            ".website a",
            "a.website-link"
        ]
        website = self.safe_find_attribute(selectors, "href")
        if not website:
            # Try to find in text
            website = self.safe_find_text(["//span[contains(text(), 'Website')]/following-sibling::span"])
        
        return website if website and website.startswith("http") else None
    
    def _extract_email(self) -> Optional[str]:
        """Extract email address."""
        # Try structured fields first
        selectors = [
            "//span[contains(text(), 'Email')]/following-sibling::span",
            "//td[contains(text(), 'Email')]/following-sibling::td",
            ".email",
            "a[href^='mailto:']"
        ]
        
        email = self.safe_find_text(selectors)
        if not email:
            # Try to extract from mailto link
            email = self.safe_find_attribute(["a[href^='mailto:']"], "href")
            if email:
                email = email.replace("mailto:", "")
        
        if not email:
            # Search entire page text
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            emails = self.extract_emails(page_text)
            email = emails[0] if emails else None
        
        return email.lower() if email else None
    
    def _extract_phone(self) -> Optional[str]:
        """Extract phone number."""
        selectors = [
            "//span[contains(text(), 'Phone')]/following-sibling::span",
            "//td[contains(text(), 'Phone')]/following-sibling::td",
            ".phone",
            "a[href^='tel:']"
        ]
        
        phone = self.safe_find_text(selectors)
        if not phone:
            # Try to extract from tel link
            phone = self.safe_find_attribute(["a[href^='tel:']"], "href")
            if phone:
                phone = phone.replace("tel:", "")
        
        return phone.strip() if phone else None
    
    def _extract_services(self) -> List[str]:
        """Extract services offered."""
        services = []
        
        try:
            # Look for service lists
            service_elements = self.driver.find_elements(
                By.CSS_SELECTOR, 
                ".service-item, .service, li.service, .services li"
            )
            
            for elem in service_elements:
                service = self.clean_text(elem.text)
                if service and len(service) < 100:  # Sanity check
                    services.append(service)
            
            # If no structured services found, look for keywords in page text
            if not services:
                page_text = self.driver.find_element(By.TAG_NAME, "body").text.upper()
                common_services = [
                    "JET_A", "JET_A1", "100LL", "AVGAS",
                    "GROUND_HANDLING", "FUELING", "HANGAR",
                    "CATERING", "CUSTOMS", "IMMIGRATION",
                    "MAINTENANCE", "PARKING", "DEICING"
                ]
                services = [s for s in common_services if s in page_text]
        
        except Exception as e:
            logger.debug(f"Could not extract services: {e}")
        
        return services
    
    def _extract_roles(self) -> List[str]:
        """Extract organization roles (FBO, HOTEL, FUEL, etc.)."""
        roles = []
        
        # Get page text and URL
        page_text = self.driver.find_element(By.TAG_NAME, "body").text.upper()
        url = self.driver.current_url.upper()
        
        # Check for role indicators
        role_mapping = {
            "FBO": ["FBO", "FIXED BASE OPERATOR"],
            "FUEL": ["FUEL", "FUELING", "REFUEL"],
            "HANDLER": ["HANDLER", "GROUND HANDLING"],
            "HOTEL": ["HOTEL", "ACCOMMODATION", "LODGING"],
            "CATERING": ["CATERING", "FOOD SERVICE"],
            "MAINTENANCE": ["MAINTENANCE", "REPAIR", "MRO"],
            "CUSTOMS": ["CUSTOMS", "IMMIGRATION"],
        }
        
        for role, keywords in role_mapping.items():
            if any(keyword in url or keyword in page_text for keyword in keywords):
                roles.append(role)
        
        # Default to ORGANIZATION if no specific role found
        if not roles:
            roles.append("ORGANIZATION")
        
        return list(set(roles))
    
    def _extract_associated_airports(self, known_airport: Optional[str] = None) -> List[str]:
        """Extract associated airport ICAO codes."""
        airports = []
        
        if known_airport:
            airports.append(known_airport.upper())
        
        try:
            # Look for airport codes in page
            page_text = self.driver.find_element(By.TAG_NAME, "body").text
            # Match 4-letter airport codes (ICAO format)
            icao_pattern = r'\b[A-Z]{4}\b'
            potential_codes = re.findall(icao_pattern, page_text.upper())
            
            # Filter out common false positives
            false_positives = {"BACK", "NEXT", "PAGE", "HOME", "MORE", "INFO", "CALL"}
            valid_codes = [code for code in potential_codes if code not in false_positives]
            
            airports.extend(valid_codes[:5])  # Limit to first 5 found
        
        except Exception as e:
            logger.debug(f"Could not extract associated airports: {e}")
        
        return list(set(airports))  # Remove duplicates
    
    def _extract_address(self) -> Optional[str]:
        """Extract physical address."""
        selectors = [
            ".address",
            "//span[contains(text(), 'Address')]/following-sibling::span",
            "//td[contains(text(), 'Address')]/following-sibling::td",
            ".contact-address"
        ]
        return self.clean_text(self.safe_find_text(selectors)) or None
    
    def _extract_hours(self) -> Optional[str]:
        """Extract operating hours."""
        selectors = [
            ".hours",
            "//span[contains(text(), 'Hours')]/following-sibling::span",
            "//td[contains(text(), 'Hours')]/following-sibling::td",
            ".operating-hours"
        ]
        return self.clean_text(self.safe_find_text(selectors)) or None
