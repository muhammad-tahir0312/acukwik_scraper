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

from config_loader import load_config
from auth import CookieAuthentication
from parsers import AirportPageParser, AirportParser, OrganizationParser
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
        time.sleep(2)
        
        # Handle cookie consent popup if it appears
        logger.info("Checking for cookie consent popup...")
        try:
            cookie_accept = driver.find_element(By.XPATH, "//button[contains(text(), 'Accept all')]")
            cookie_accept.click()
            logger.info("Dismissed cookie consent popup")
            time.sleep(1)
        except:
            logger.info("No cookie consent popup found")
        
        # Wait longer for modal CONTENT to load (it loads via AJAX)
        logger.info("Waiting for modal form to load via AJAX...")
        time.sleep(5)
        
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
        
        # Try to find email field with multiple strategies
        logger.info("Looking for email field in modal...")
        email_field = None
        
        # Wait explicitly for email field to appear
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        
        wait = WebDriverWait(driver, 20)
        selectors_to_try = [
            # (By.ID, "UserName"),
            # (By.NAME, "UserName"),
            (By.CSS_SELECTOR, "input[type='email']"),
            (By.CSS_SELECTOR, "input[placeholder*='mail' i]"),
            (By.CSS_SELECTOR, ".dnnLoginService input[type='text']"),
            (By.XPATH, "//input[@type='text' or @type='email']")
        ]
        
        for by_type, selector_value in selectors_to_try:
            try:
                logger.info(f"Trying selector: {by_type} = {selector_value}")
                email_field = wait.until(
                    EC.presence_of_element_located((by_type, selector_value))
                )
                logger.info(f"Found email field: {by_type} = {selector_value}")
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
        logger.info("Email entered")
        
        # Find and fill password field
        logger.info("Looking for password field...")
        password_field = None
        
        password_selectors = [
            (By.ID, "Password"),
            (By.NAME, "Password"),
            (By.CSS_SELECTOR, "input[type='password']")
        ]
        
        for by_type, selector_value in password_selectors:
            try:
                logger.info(f"Trying password selector: {by_type} = {selector_value}")
                password_field = wait.until(
                    EC.presence_of_element_located((by_type, selector_value))
                )
                logger.info(f"Found password field: {by_type} = {selector_value}")
                break
            except:
                continue
        
        if not password_field:
            logger.error("Could not find password field in modal")
            return False
        
        password_field.clear()
        password_field.send_keys(password)
        logger.info("Password entered")
        
        # Find and click submit button
        logger.info("Looking for submit button...")
        login_submit = None
        for selector in ["button[type='submit']", "input[type='submit']", "button:contains('Login')", ".btn-login"]:
            try:
                login_submit = driver.find_element(By.CSS_SELECTOR, selector)
                logger.info(f"Found submit button: {selector}")
                break
            except:
                continue
        
        if login_submit:
            # Use ActionChains or JavaScript fallback
            try:
                actions = ActionChains(driver)
                actions.move_to_element(login_submit).click().perform()
                logger.info("Clicked Login submit button via ActionChains")
            except Exception as e:
                logger.warning(f"ActionChains failed, using JavaScript: {e}")
                driver.execute_script("arguments[0].click();", login_submit)
                logger.info("Clicked Login submit button via JavaScript")
        else:
            logger.info("No submit button found, trying Enter key")
            password_field.send_keys(Keys.RETURN)
        
        # Wait for login to complete
        logger.info("Waiting for login to complete...")
        time.sleep(5)
        
        # Switch back to main content if we were in an iframe
        try:
            driver.switch_to.default_content()
            logger.info("Switched back to main content")
        except:
            pass
        
        # Get and save cookies
        cookies = driver.get_cookies()
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
            batch_size=self.config["output"].get("batch_size", 100)
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
            
            driver = webdriver.Chrome(options=options)
            
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
        
        base_url = auth_config.get("base_url", "https://www.acukwik.com")
        
        auth = CookieAuthentication(cookies_file)
        auth.apply_cookies(driver, base_url)
        
        logger.info("Driver authenticated successfully")
    
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
                
                # Start timing
                scrape_start = time.time()
                
                try:
                    driver.get(url)
                except TimeoutException:
                    logger.warning(f"Page load timeout for {url}, continuing anyway...")
                    # Page might have partially loaded, try to parse
                
                # Wait for page load
                time.sleep(self.config["scraping"].get("delay_between_requests", 2))
                
                # Parse based on entity type
                entities = []
                if entity_type == "airport":
                    # Use new AirportPageParser that returns multiple entities
                    parser = AirportPageParser(driver, self.source)
                    entities = parser.parse(url)  # Returns list of entities (airport + orgs)
                    
                    # Use ICAO from CSV if not found on page (for first entity - airport)
                    if entities and not entities[0].get("data", {}).get("icao") and record.get("ICAO"):
                        entities[0]["data"]["icao"] = record["ICAO"].upper()
                    
                else:
                    # Legacy organization scraping (single entity)
                    parser = OrganizationParser(driver, self.source)
                    associated_airport = record.get("airport_icao")
                    single_entity = parser.parse(url, associated_airport)
                    entities = [single_entity]
                
                # Calculate scrape duration
                scrape_duration_ms = int((time.time() - scrape_start) * 1000)
                
                # Add timing to all entities
                for entity in entities:
                    entity["scrape_duration_ms"] = scrape_duration_ms
                
                # Validate and write all entities
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
                
                logger.info(f"Successfully scraped {len(entities)} entities from: {external_id} (took {scrape_duration_ms}ms)")
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
