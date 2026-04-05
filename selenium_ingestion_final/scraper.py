"""
Main scraper orchestrator.
Coordinates the entire scraping process with parallel execution, retries, and resumability.
"""
import csv
import logging
import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import random

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.chrome.service import Service

from config_loader import load_config
from auth import CookieAuthentication
from parsers import AirportPageParser, AirportParser, OrganizationParser, ClearanceParser, NearbyParser
from validators import validate_record
from output import BatchOutputWriter
from progress import ProgressTracker

logger = logging.getLogger(__name__)


def auto_login_if_needed(config: Dict[str, Any]) -> bool:
    """
    Check if cookies are valid, if not, login automatically.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        True if cookies are available (either existing or newly created)
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    
    auth_config = config.get("authentication", {})
    if not auth_config.get("enabled", False):
        return True
    
    cookies_file = Path(auth_config.get("cookies_file", "../cookies.json"))
    if not cookies_file.is_absolute():
        cookies_file = Path(__file__).parent / cookies_file
    
    # Check if cookies exist and are recent (less than 7 days old)
    if cookies_file.exists():
        file_age_days = (time.time() - cookies_file.stat().st_mtime) / 86400
        if file_age_days < 7:
            logger.info(f"Using existing cookies (age: {file_age_days:.1f} days)")
            return True
        else:
            logger.warning(f"Cookies are {file_age_days:.1f} days old, will refresh...")
    else:
        logger.info("No cookies found, logging in...")
    
    # Auto-login to get fresh cookies
    email = auth_config.get("email")
    password = auth_config.get("password")
    
    if not email or not password:
        logger.error("Cannot auto-login: email or password missing in config")
        return False
    
    logger.info(f"Logging in as {email}...")
    
    driver = None
    try:
        from selenium.webdriver.chrome.options import Options as ChromeOptions
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        
        options = ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")  # Helps in headless mode
        options.add_argument("--remote-debugging-port=9222")  # Ensures DevTools communication
        
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(30)
        
        # Navigate to homepage
        base_url = auth_config.get("base_url", "https://acukwik.com")
        logger.info(f"Navigating to {base_url}")
        driver.get(base_url)
        time.sleep(3)
        
        # Find and click Login button using JavaScript (more reliable)
        logger.info("Looking for Login button...")
        try:
            # Find the login link by its ID or onclick attribute
            login_script = """
                var loginLink = document.querySelector("a[onclick*='dnnModal.show']");
                if (loginLink) {
                    loginLink.click();
                    return true;
                }
                return false;
            """
            clicked = driver.execute_script(login_script)
            if clicked:
                logger.info("Clicked Login button via JavaScript")
            else:
                logger.error("Could not find Login button")
                return False
        except Exception as e:
            logger.error(f"Failed to click login button: {e}")
            return False
        
        logger.info("Waiting for modal container to appear...")
        time.sleep(3)
        
        # Wait longer for modal CONTENT to load (it loads via AJAX)
        logger.info("Waiting for modal form to load via AJAX...")
        time.sleep(3)
        
        # Handle cookie consent popup if it appears - SHORT TIMEOUT since it's usually not there
        logger.info("Checking for cookie consent popup (quick check)...")
        try:
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            
            wait_short = WebDriverWait(driver, 2)  # Only 2 seconds - don't waste time
            
            # Try multiple selectors for the Accept button
            accept_button = None
            selectors = [
                (By.XPATH, "//button[contains(text(), 'Accept all')]"),
                (By.XPATH, "//button[contains(text(), 'Accept All')]"),
                (By.CSS_SELECTOR, "button[class*='accept']"),
            ]
            
            for by_type, selector in selectors:
                try:
                    accept_button = wait_short.until(
                        EC.element_to_be_clickable((by_type, selector))
                    )
                    logger.info(f"✓ Found cookie consent button: {selector}")
                    # Click using JavaScript for reliability
                    driver.execute_script("arguments[0].click();", accept_button)
                    logger.info("✓ CLICKED 'Accept all' cookie button")
                    time.sleep(2)  # Wait for popup to disappear
                    break
                except Exception as e:
                    continue
            
            if not accept_button:
                logger.info("✓ No cookie popup detected (already dismissed or not present - this is normal)")
        except Exception as e:
            logger.info(f"✓ No cookie popup found (this is normal if already dismissed)")
        
        # Check if modal uses iframe
        logger.info("Checking for iframe in modal...")
        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            logger.info(f"Found {len(iframes)} iframe(s) on page")
            
            # Try to switch to modal iframe if exists
            for idx, iframe in enumerate(iframes):
                iframe_src = iframe.get_attribute("src") or ""
                iframe_id = iframe.get_attribute("id") or ""
                logger.info(f"  iframe {idx}: id={iframe_id}, src={iframe_src[:100] if iframe_src else 'none'}")
                
                # DNN modals often use iframes with 'iPopUp' in the id
                if "popup" in iframe_id.lower() or "modal" in iframe_id.lower() or "login" in iframe_src.lower():
                    logger.info(f"Switching to iframe: {iframe_id or idx}")
                    driver.switch_to.frame(iframe)
                    time.sleep(2)
                    break
        except Exception as e:
            logger.warning(f"Iframe check failed: {e}")
        
        # IMPORTANT: Check for cookie consent INSIDE the iframe (MUST do this AFTER switching to iframe)
        logger.info("Checking for cookie consent popup INSIDE iframe...")
        try:
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            wait_cookie = WebDriverWait(driver, 4)
            cookie_dismissed = False

            # Try case-insensitive text match on buttons/links
            try:
                accept_btn = wait_cookie.until(
                    EC.element_to_be_clickable(
                        (
                            By.XPATH,
                            "//*[self::button or self::a][contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept all')]"
                        )
                    )
                )
                logger.info("✓ Found cookie consent inside iframe via text match")
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", accept_btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", accept_btn)
                time.sleep(2)
                cookie_dismissed = True
            except Exception as e:
                logger.debug(f"Text-match selector failed: {e}")

            # JavaScript fallback: search all buttons/links for text containing 'accept all'
            if not cookie_dismissed:
                try:
                    logger.info("Trying JavaScript search for 'Accept all' button inside iframe...")
                    js_clicked = driver.execute_script(
                        """
                        const els = Array.from(document.querySelectorAll('button, a'));
                        const target = els.find(el => (el.innerText || '').toLowerCase().includes('accept all'));
                        if (target) { target.click(); return true; }
                        return false;
                        """
                    )
                    if js_clicked:
                        logger.info("✓ Clicked 'Accept all' via JavaScript")
                        cookie_dismissed = True
                        time.sleep(2)
                except Exception as e:
                    logger.debug(f"JS search failed: {e}")

            if cookie_dismissed:
                try:
                    errors_dir = Path(__file__).parent / "errors"
                    errors_dir.mkdir(exist_ok=True)
                    screenshot_cookies = errors_dir / "login_step0_cookies_dismissed.png"
                    driver.save_screenshot(str(screenshot_cookies))
                    logger.info(f"📸 Screenshot saved: {screenshot_cookies}")
                except Exception as e:
                    logger.warning(f"Failed to save cookie screenshot: {e}")
            else:
                logger.info("No cookie popup in iframe (not found or already dismissed)")
        except Exception as e:
            logger.info(f"Cookie popup handling in iframe failed: {e}")
        
        # Try to find email field with exact selectors from HTML
        logger.info("Looking for email field in modal...")
        email_field = None
        
        # Wait explicitly for email field to appear
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        
        wait = WebDriverWait(driver, 20)
        selectors_to_try = [
            (By.ID, "dnn_ctr594_VDC_ctl00_txtUsername"),  # Exact ID from HTML
            (By.NAME, "dnn$ctr594$VDC$ctl00$txtUsername"),  # Exact name from HTML
            (By.XPATH, "//input[@type='text' or @type='email']"),
            (By.CSS_SELECTOR, "input[type='text']"),
        ]
        
        for by_type, selector_value in selectors_to_try:
            try:
                logger.info(f"Trying selector: {by_type} = {selector_value}")
                email_field = wait.until(
                    EC.presence_of_element_located((by_type, selector_value))
                )
                logger.info(f"✓ Found email field: {by_type} = {selector_value}")
                break
            except Exception as e:
                logger.debug(f"Selector failed: {e}")
                continue
        
        if not email_field:
            logger.error("Could not find email field in modal")
            # Save screenshot and HTML for debugging
            try:
                # Create errors directory if it doesn't exist
                errors_dir = Path(__file__).parent / "errors"
                errors_dir.mkdir(exist_ok=True)
                
                screenshot_path = errors_dir / "login_modal_error.png"
                driver.save_screenshot(str(screenshot_path))
                logger.info(f"Saved screenshot to {screenshot_path}")
                
                # Save page source to inspect
                html_path = errors_dir / "login_modal_source.html"
                with open(html_path, "w") as f:
                    f.write(driver.page_source)
                logger.info(f"Saved page source to {html_path}")
                
                # List all input fields
                inputs = driver.find_elements(By.TAG_NAME, "input")
                logger.info(f"Found {len(inputs)} input elements on page:")
                for inp in inputs[:10]:  # First 10 only
                    logger.info(f"  - type={inp.get_attribute('type')}, name={inp.get_attribute('name')}, id={inp.get_attribute('id')}")
            except Exception as e:
                logger.warning(f"Debug info collection failed: {e}")
            return False
        
        email_field.clear()
        email_field.send_keys(email)
        time.sleep(0.5)  # Small delay after typing
        logger.info(f"✓ Email entered: {email}")
        
        # Find and fill password field
        logger.info("Looking for password field...")
        password_field = None
        
        password_selectors = [
            (By.ID, "dnn_ctr594_VDC_ctl00_txtPassword"),  # Exact ID from HTML
            (By.NAME, "dnn$ctr594$VDC$ctl00$txtPassword"),  # Exact name from HTML
            (By.CSS_SELECTOR, "input[type='password']")
        ]
        
        for by_type, selector_value in password_selectors:
            try:
                logger.info(f"Trying password selector: {by_type} = {selector_value}")
                password_field = wait.until(
                    EC.presence_of_element_located((by_type, selector_value))
                )
                logger.info(f"✓ Found password field: {by_type} = {selector_value}")
                break
            except:
                continue
        
        if not password_field:
            logger.error("Could not find password field in modal")
            return False
        
        password_field.clear()
        time.sleep(0.5)  # Small delay after clear
        password_field.send_keys(password)
        
        # Find and click submit button - IT'S AN <A> TAG, NOT A BUTTON!
        logger.info("Looking for login button...")
        login_submit = None
        
        # The login button is: <a id="dnn_ctr594_VDC_ctl00_cmdLogin" class="dnnPrimaryAction" href="javascript:__doPostBack...">Login</a>
        login_selectors = [
            (By.ID, "dnn_ctr594_VDC_ctl00_cmdLogin"),  # Exact ID from HTML
            (By.CSS_SELECTOR, "a.dnnPrimaryAction"),  # It's an <a> tag with this class
            (By.XPATH, "//a[contains(@id, 'cmdLogin')]"),
            (By.XPATH, "//a[contains(text(), 'Login')]"),
        ]
        
        for by_type, selector_value in login_selectors:
            try:
                logger.info(f"Trying login button selector: {by_type} = {selector_value}")
                login_submit = wait.until(
                    EC.element_to_be_clickable((by_type, selector_value))
                )
                logger.info(f"✓ Found login button: {by_type} = {selector_value}")
                break
            except:
                continue
        
        if login_submit:
            # Use JavaScript click (most reliable for <a> tags with onclick)
            logger.info("Clicking login button...")
            try:
                driver.execute_script("arguments[0].click();", login_submit)
                logger.info("✓ Clicked Login button via JavaScript")
            except Exception as e:
                logger.error(f"Failed to click login button: {e}")
                return False
        else:
            logger.warning("No login button found, trying Enter key as fallback")
            password_field.send_keys(Keys.RETURN)

        # Wait for login to complete and check for success
        logger.info("Waiting for login to complete...")
        time.sleep(5)
        
        # Switch back to main content if we were in an iframe
        try:
            driver.switch_to.default_content()
            logger.info("Switched back to main content")
        except:
            pass
        
        # Verify login succeeded BEFORE saving cookies
        time.sleep(2)  # Additional wait for page to update
        page_source = driver.page_source
        
        # Check for account menu (positive indicator of successful login)
        if 'id="dnn_MyAccountLink1_MyAccount"' in page_source or 'class="myAccount opened"' in page_source:
            logger.info("✓ Login verification: Account menu detected!")
        elif 'id="dnn_MyAccountLink1_LoginLi"' in page_source:
            logger.error("✗ Login FAILED: Still seeing Login button, not logged in!")
            # Save failure screenshot
            try:
                screenshot_fail = errors_dir / "login_FAILED_still_logged_out.png"
                driver.save_screenshot(str(screenshot_fail))
                logger.error(f"📸 Failure screenshot: {screenshot_fail}")
            except:
                pass
            return False
        else:
            logger.warning("⚠️  Cannot confirm login status - no clear indicators found")
        
        # Get and save cookies
        cookies = driver.get_cookies()
        
        if len(cookies) == 0:
            logger.error("✗ No cookies found - login likely failed")
            return False
        
        with open(cookies_file, 'w') as f:
            json.dump(cookies, f, indent=2)
        
        logger.info(f"✓ Login successful! Saved {len(cookies)} cookies to {cookies_file}")        
        return True
        
    except Exception as e:
        logger.error(f"Auto-login failed: {e}")
        return False
        
    finally:
        if driver:
            driver.quit()


class ScraperOrchestrator:
    """Main orchestrator for the scraping process."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        Initialize scraper orchestrator.
        
        Args:
            config_path: Path to configuration file
        """
        self.config = load_config(config_path)
        self.source = self.config["source"]["name"]
        
        # Initialize components
        self.output_writer = BatchOutputWriter(
            output_dir=self.config["output"]["directory"],
            batch_size=self.config["output"].get("batch_size", 100),
            records_per_file=self.config["output"].get("records_per_file", 5000)
        )
        
        self.progress_tracker = ProgressTracker(
            progress_file=self.config["progress"]["file"]
        )
        
        # Stats
        self.stats = {
            "started_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0
        }
        
        logger.info(f"Scraper orchestrator initialized for source: {self.source}")
    
    def create_driver(self) -> webdriver.Remote:
        """
        Create a new Selenium WebDriver instance.
        
        Returns:
            Configured WebDriver instance
        """
        browser = self.config["selenium"]["browser"].lower()
        headless = self.config["selenium"]["headless"]
        
        if browser == "chrome":
            options = ChromeOptions()
            if headless:
                options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option('useAutomationExtension', False)
            
            # Add user agent
            options.add_argument(f"user-agent={self.config['selenium'].get('user_agent', 'Mozilla/5.0')}")
            
            service = Service("/usr/local/bin/chromedriver")
            driver = webdriver.Chrome(service=service, options=options)
            
        elif browser == "firefox":
            options = FirefoxOptions()
            if headless:
                options.add_argument("--headless")
            
            driver = webdriver.Firefox(options=options)
            
        else:
            raise ValueError(f"Unsupported browser: {browser}")
        
        # Set timeouts
        driver.set_page_load_timeout(self.config["selenium"].get("page_load_timeout", 30))
        driver.implicitly_wait(self.config["selenium"].get("implicit_wait", 10))
        
        return driver
    
    def authenticate_driver(self, driver: webdriver.Remote) -> None:
        """
        Authenticate driver using cookies.
        
        Args:
            driver: WebDriver instance to authenticate
        """
        auth_config = self.config.get("authentication", {})
        if not auth_config.get("enabled", False):
            logger.info("Authentication disabled, skipping")
            return
        
        cookies_file = auth_config.get("cookies_file")
        if not cookies_file:
            logger.warning("Authentication enabled but no cookies file specified")
            return
        
        base_url = auth_config.get("base_url", "https://acukwik.com")  # FIXED: Changed default to non-www
        
        auth = CookieAuthentication(cookies_file)
        auth.apply_cookies(driver, base_url)
        
        num_cookies = len(auth.cookies) if auth.cookies else 0
        logger.info(f"Driver authenticated with {num_cookies} cookies")
        
        # Verify authentication by checking a simple page
        try:
            driver.get(base_url)
            time.sleep(2)
            
            # Check if we can see user account menu (positive indicator)
            page_source = driver.page_source
            
            if 'id="dnn_MyAccountLink1_MyAccount"' in page_source or 'class="myAccount opened"' in page_source:
                logger.info("✓ Authentication successful - user account menu detected")
                return
            
            # If positive indicator not found, check for login form/prompts (negative indicators)
            page_source_lower = page_source.lower()
            
            # Check for login form HTML patterns
            login_form_patterns = [
                'class="loginForm"',
                'id="dnn_ctr594_VDC_ctl00_cmdLogin"',
                'src="/Login?returnurl='
            ]
            has_login_form = any(pattern in page_source for pattern in login_form_patterns)
            
            # Check for login text prompts
            has_login_text = "please log in" in page_source_lower or "please login" in page_source_lower or "log in to ac-u-kwik" in page_source_lower
            
            if has_login_form or has_login_text:
                logger.warning("⚠️  Authentication may have failed - login form/prompts detected!")
                logger.warning("   Cookies might be expired. Please refresh cookies.json")
            else:
                logger.warning("⚠️  Could not confirm authentication - no account menu or login prompts found")
                logger.warning("   This might be okay, but verify output data quality")
        except Exception as e:
            logger.warning(f"Could not verify authentication: {e}")
    
    def load_input_data(self) -> List[Dict[str, Any]]:
        """
        Load input data from CSV files.
        
        Returns:
            List of records to scrape
        """
        input_paths = self.config["input"]["csv_paths"]
        if isinstance(input_paths, str):
            input_paths = [input_paths]
        
        all_records = []
        
        for csv_path in input_paths:
            csv_file = Path(csv_path)
            if not csv_file.exists():
                logger.warning(f"Input file not found: {csv_path}")
                continue
            
            logger.info(f"Loading input from: {csv_path}")
            
            try:
                with open(csv_file, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    records = list(reader)
                    all_records.extend(records)
                    logger.info(f"Loaded {len(records)} records from {csv_path}")
                    
            except Exception as e:
                logger.error(f"Failed to load {csv_path}: {e}")
        
        logger.info(f"Total records loaded: {len(all_records)}")
        return all_records
    
    def scrape_record(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Scrape a single record with retry logic.
        
        Args:
            record: Input record with URL and metadata
            
        Returns:
            Scraped data or None if failed
        """
        url = record.get("url") or record.get("link") or record.get("Airport Link")
        if not url:
            logger.error(f"No URL found in record: {record}")
            return None
        
        # Detect entity type from record or URL
        entity_type = record.get("entity_type")
        if not entity_type:
            # If record has ICAO field, it's likely an airport
            if record.get("ICAO") or "/Airport-Info/" in url:
                entity_type = "airport"
            else:
                entity_type = "organization"
        
        external_id = self._generate_external_id(url, entity_type)
        
        # Check if already completed
        if self.progress_tracker.is_completed(external_id):
            logger.debug(f"Skipping already completed: {external_id}")
            self.stats["skipped"] += 1
            return None
        
        # Check if should retry
        max_retries = self.config["scraping"]["max_retries"]
        if not self.progress_tracker.should_process(external_id, max_retries):
            logger.debug(f"Skipping due to max retries: {external_id}")
            self.stats["skipped"] += 1
            return None
        
        # Attempt scraping with retries
        for attempt in range(max_retries):
            driver = None
            try:
                # Create driver
                driver = self.create_driver()
                self.authenticate_driver(driver)
                
                # Navigate to URL
                logger.info(f"Scraping {url} (attempt {attempt + 1}/{max_retries})")
                
                try:
                    driver.get(url)
                except TimeoutException:
                    logger.warning(f"Page load timeout for {url}, continuing anyway...")
                    # Page might have partially loaded, try to parse
                
                # Wait for page load
                time.sleep(self.config["scraping"].get("delay_between_requests", 2))
                
                # Check for authentication issues
                self._verify_authentication(driver, url, external_id)
                
                # Parse based on entity type
                entities = []
                if entity_type == "airport":
                    # Use new AirportPageParser that returns multiple entities
                    parser = AirportPageParser(driver)
                    entities = parser.parse(url)  # Returns list of entities (airport + orgs)

                    # Use ICAO from CSV if not found on page (for first entity - airport)
                    if entities and not entities[0].get("data", {}).get("icao") and record.get("ICAO"):
                        entities[0]["data"]["icao"] = record["ICAO"].upper()

                    # Extract ICAO to build the other tab URLs
                    icao = (
                        (entities[0].get("data", {}).get("icao") if entities else None)
                        or record.get("ICAO")
                        or self._extract_icao_from_url(url)
                    )

                    scraping_cfg = self.config.get("scraping", {})

                    # Scrape Clearance tab
                    if icao and scraping_cfg.get("scrape_clearance", True):
                        try:
                            clearance_url = f"https://acukwik.com/Clearance-Overview/{icao}"
                            clearance_parser = ClearanceParser(driver)
                            clearance_entity = clearance_parser.parse(clearance_url, icao)
                            if clearance_entity:
                                entities.append(clearance_entity)
                        except Exception as ce:
                            logger.warning(f"Clearance scrape failed for {icao}: {ce}")

                    # Scrape Nearby tab
                    if icao and scraping_cfg.get("scrape_nearby", True):
                        try:
                            nearby_url = f"https://acukwik.com/Nearby/{icao}"
                            nearby_parser = NearbyParser(driver)
                            nearby_entity = nearby_parser.parse(nearby_url, icao)
                            if nearby_entity:
                                entities.append(nearby_entity)
                        except Exception as ne:
                            logger.warning(f"Nearby scrape failed for {icao}: {ne}")

                else:
                    # Legacy organization scraping (single entity)
                    parser = OrganizationParser(driver)
                    associated_airport = record.get("airport_icao")
                    single_entity = parser.parse(url, associated_airport)
                    entities = [single_entity]
                
                # Validate, optionally screenshot, and write all entities
                for entity in entities:
                    # Validate
                    validation_errors = validate_record(entity)
                    if validation_errors:
                        entity["validation_errors"] = validation_errors
                        logger.warning(f"Validation errors for {entity.get('external_id')}: {validation_errors}")

                    # Write entity
                    self.output_writer.write_success(entity)

                # Success!
                self.progress_tracker.mark_completed(external_id)
                self.stats["success"] += 1

                logger.info(f"Successfully scraped {len(entities)} entities from: {external_id}")
                return entities[0] if entities else None  # Return first for compatibility
                
            except Exception as e:
                logger.error(f"Error scraping {url} (attempt {attempt + 1}): {str(e)}")
                
                # Exponential backoff
                if attempt < max_retries - 1:
                    backoff = self.config["scraping"]["retry_backoff"] * (2 ** attempt)
                    jitter = random.uniform(0, backoff * 0.1)
                    sleep_time = backoff + jitter
                    logger.info(f"Retrying in {sleep_time:.2f} seconds...")
                    time.sleep(sleep_time)
                else:
                    # Final failure
                    self.progress_tracker.mark_failed(external_id, max_retries)
                    self.output_writer.write_failure(external_id, url, str(e), entity_type)
                    self.stats["failed"] += 1
                    return None
            
            finally:
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass
        
        return None
    
    def _verify_authentication(self, driver: webdriver.Remote, url: str, external_id: str) -> None:
        """Verify that user is properly authenticated by checking page content."""
        try:
            page_source = driver.page_source
            page_source_lower = page_source.lower()
            
            # First, check for POSITIVE authentication indicators (user is logged in)
            # When logged in, the page shows: <li id="dnn_MyAccountLink1_MyAccount" class="myAccount opened">
            if 'id="dnn_MyAccountLink1_MyAccount"' in page_source or 'class="myAccount opened"' in page_source:
                logger.debug(f"✓ Authentication verified for {external_id} - user account menu detected")
                return  # Successfully authenticated, no need to check failure indicators
            
            # If positive indicator not found, check for NEGATIVE indicators (authentication failure)
            
            # Check for login form/modal indicators (case-insensitive)
            login_text_indicators = [
                "please login",
                "sign in to view",
                "login required",
                "you must be logged in",
                "log in to ac-u-kwik"  # Specific to acukwik login form header
            ]
            
            # Check for specific HTML patterns that indicate login form/modal (case-sensitive)
            login_html_patterns = [
                'class="loginForm"',           # Login form class
                'class="reg-form loginForm"',  # Full login form class
                'id="dnn_ctr594_VDC_ctl00_cmdLogin"',  # Login button ID
                'class="dnnFormPopup"',        # Login modal popup class
                'src="/Login?returnurl=',      # Login iframe source
                'AC-U-KWIK &gt; sign in',      # Modal title (HTML encoded)
                'AC-U-KWIK > sign in'          # Modal title (plain text)
            ]
            
            # Check if any login form patterns exist
            has_login_form = any(pattern in page_source for pattern in login_html_patterns)
            has_login_text = any(indicator in page_source_lower for indicator in login_text_indicators)
            
            # Check for the specific pattern when content is hidden: <a href="#" class="International">INTERNATIONAL</a>
            # This is a very reliable indicator of authentication failure
            hidden_content_pattern = 'class="International"'
            has_hidden_content = hidden_content_pattern in page_source
            
            # Check if page has suspicious "INTERNATIONAL" everywhere (indicates auth failure)
            international_count = page_source_lower.count("international")
            
            if has_login_form or has_login_text or has_hidden_content or international_count > 50:
                logger.error(f"⚠️  AUTHENTICATION FAILURE DETECTED for {external_id}")
                
                failure_reasons = []
                if has_login_form:
                    failure_reasons.append("login form/modal detected (HTML patterns matched)")
                if has_login_text:
                    failure_reasons.append("login text prompts found")
                if has_hidden_content:
                    failure_reasons.append('hidden content markers (class="International") found')
                if international_count > 50:
                    failure_reasons.append(f"excessive 'INTERNATIONAL' text (count: {international_count})")
                
                logger.error(f"   Reason(s): {', '.join(failure_reasons)}")
                
                # Save screenshot and HTML for debugging
                errors_dir = Path(__file__).parent / "errors"
                errors_dir.mkdir(exist_ok=True)
                
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                screenshot_path = errors_dir / f"auth_failure_{external_id}_{timestamp}.png"
                html_path = errors_dir / f"auth_failure_{external_id}_{timestamp}.html"
                
                try:
                    driver.save_screenshot(str(screenshot_path))
                    logger.error(f"   Screenshot saved: {screenshot_path}")
                except Exception as e:
                    logger.error(f"   Failed to save screenshot: {e}")
                
                try:
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(driver.page_source)
                    logger.error(f"   HTML saved: {html_path}")
                except Exception as e:
                    logger.error(f"   Failed to save HTML: {e}")
                
                logger.error(f"   URL: {url}")
                logger.error(f"   ⚠️  COOKIES MAY BE INVALID OR EXPIRED - Please refresh cookies!")
                
                # Raise exception to mark this scrape as failed
                raise Exception("Authentication failure detected - cookies may be invalid")
        
        except Exception as e:
            if "Authentication failure" in str(e):
                raise  # Re-raise auth failures
            logger.debug(f"Auth verification check failed: {e}")
    
    def _extract_icao_from_url(self, url: str) -> Optional[str]:
        """Extract 4-letter ICAO code from an Airport-Info URL."""
        import re
        match = re.search(r'/Airport-Info/([A-Z0-9]{3,4})', url, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return None

    def _generate_external_id(self, url: str, entity_type: str) -> str:
        """Generate external ID from URL."""
        import hashlib
        import re
        
        # Try to extract meaningful ID from URL
        patterns = [
            r'/airport/([A-Z0-9]+)',
            r'/fbo/([A-Z0-9_-]+)',
            r'/org/([A-Z0-9_-]+)',
            r'/detail/([A-Z0-9_-]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                return f"{self.source}_{match.group(1).upper()}"
        
        # Fallback: hash of URL
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        return f"{self.source}_{entity_type}_{url_hash}"
    
    def scrape_parallel(self, records: List[Dict[str, Any]]) -> None:
        """
        Scrape records in parallel using thread pool.
        
        Args:
            records: List of records to scrape
        """
        max_workers = self.config["scraping"]["parallel_workers"]
        
        logger.info(f"Starting parallel scraping with {max_workers} workers")
        logger.info(f"Total records to process: {len(records)}")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_record = {
                executor.submit(self.scrape_record, record): record
                for record in records
            }
            
            # Process completed tasks
            for future in as_completed(future_to_record):
                self.stats["processed"] += 1
                
                # Periodic progress save
                if self.stats["processed"] % 10 == 0:
                    self.progress_tracker.save_progress()
                    self._log_progress()
                
                # Periodic output flush
                if self.stats["processed"] % 50 == 0:
                    self.output_writer.flush_all()
        
        # Final save
        self.progress_tracker.save_progress()
        self.output_writer.flush_all()
        
        logger.info("Parallel scraping completed")
    
    def scrape_sequential(self, records: List[Dict[str, Any]]) -> None:
        """
        Scrape records sequentially (for debugging).
        
        Args:
            records: List of records to scrape
        """
        logger.info(f"Starting sequential scraping")
        logger.info(f"Total records to process: {len(records)}")
        
        for i, record in enumerate(records, 1):
            self.stats["processed"] += 1
            self.scrape_record(record)
            
            # Periodic saves
            if i % 10 == 0:
                self.progress_tracker.save_progress()
                self._log_progress()
            
            if i % 50 == 0:
                self.output_writer.flush_all()
        
        # Final save
        self.progress_tracker.save_progress()
        self.output_writer.flush_all()
        
        logger.info("Sequential scraping completed")
    
    def run(self) -> None:
        """Main entry point to run the scraper."""
        logger.info("="*60)
        logger.info("Starting Aviation Data Scraper")
        logger.info("="*60)
        
        try:
            # Check and refresh cookies if needed
            if not auto_login_if_needed(self.config):
                logger.error("Failed to obtain valid cookies")
                return
            
            # Load input data
            records = self.load_input_data()
            
            if not records:
                logger.error("No records to process")
                return
            
            # Choose execution mode
            parallel_workers = self.config["scraping"]["parallel_workers"]
            if parallel_workers > 1:
                self.scrape_parallel(records)
            else:
                self.scrape_sequential(records)
            
            # Final stats
            self._log_final_stats()
            
        except KeyboardInterrupt:
            logger.warning("Scraping interrupted by user")
            self.progress_tracker.save_progress()
            self.output_writer.flush_all()
            
        except Exception as e:
            logger.error(f"Fatal error in scraper: {str(e)}", exc_info=True)
            raise
        
        finally:
            logger.info("Scraper shutdown complete")
    
    def _log_progress(self) -> None:
        """Log current progress."""
        logger.info(f"Progress: {self.stats['processed']} processed, "
                   f"{self.stats['success']} success, "
                   f"{self.stats['failed']} failed, "
                   f"{self.stats['skipped']} skipped")
    
    def _log_final_stats(self) -> None:
        """Log final statistics."""
        self.stats["completed_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        # Calculate duration
        start_time = datetime.fromisoformat(self.stats["started_at"].replace("Z", "+00:00"))
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        logger.info("="*60)
        logger.info("SCRAPING COMPLETED")
        logger.info("="*60)
        logger.info(f"Total processed:  {self.stats['processed']}")
        logger.info(f"Successful:       {self.stats['success']}")
        logger.info(f"Failed:           {self.stats['failed']}")
        logger.info(f"Skipped:          {self.stats['skipped']}")
        logger.info(f"Duration:         {duration:.2f} seconds")
        
        if self.stats['processed'] > 0:
            success_rate = (self.stats['success'] / self.stats['processed']) * 100
            logger.info(f"Success rate:     {success_rate:.2f}%")
        
        # Output file info
        output_stats = self.output_writer.get_stats()
        logger.info(f"Output records:   {output_stats['success_count']}")
        logger.info(f"Failed records:   {output_stats['failed_count']}")
        
        # Progress info
        progress_stats = self.progress_tracker.get_stats()
        logger.info(f"Completed IDs:    {progress_stats['completed_count']}")
        logger.info("="*60)


def setup_logging(config: Dict[str, Any]) -> None:
    """
    Setup logging configuration.
    
    Args:
        config: Configuration dictionary
    """
    log_config = config.get("logging", {})
    log_dir = Path(log_config.get("directory", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Create log file name with timestamp
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"scraper_{timestamp}.log"
    
    # Configure logging
    log_level = getattr(logging, log_config.get("level", "INFO").upper())
    log_format = log_config.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    
    # Remove existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(log_level)
    file_handler.setFormatter(logging.Formatter(log_format))
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format))
    
    # Configure root logger
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    logger.info(f"Logging initialized. Log file: {log_file}")


def main():
    """Main entry point."""
    import sys
    
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    
    # Load config for logging setup
    config = load_config(config_path)
    setup_logging(config)
    
    # Create and run orchestrator
    orchestrator = ScraperOrchestrator(config_path)
    orchestrator.run()


if __name__ == "__main__":
    main()
