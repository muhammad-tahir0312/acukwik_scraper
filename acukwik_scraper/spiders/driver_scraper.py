# spiders/airports_spider.py

import os
import json
import csv
import time
import logging
import random
import shutil
import tempfile
from urllib.parse import urljoin, urlparse
import requests # For downloading images

import scrapy
from scrapy import signals
from scrapy.http import HtmlResponse
from scrapy_selenium import SeleniumRequest
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager # Handles driver installation
import tenacity # For robust retry logic

# --- Configuration ---
LOGIN_URL = "https://acukwik.com/Login?returnurl=/"
ACCOUNT_URL = "https://acukwik.com/Account"
COOKIES_FILE = 'cookies.json'
DEFAULT_CSV_FILE = "country_links.csv"
DEFAULT_CSV_COLUMN = "Airport Link"
DEFAULT_IMAGE_DIR = "downloaded_images"
DEFAULT_TIMEOUT = 3
DEFAULT_IMPLICIT_WAIT = 3
DEFAULT_RETRY_STOP = tenacity.stop_after_attempt(3)
DEFAULT_RETRY_WAIT = tenacity.wait_fixed(2)

# --- Logging Setup ---
logger = logging.getLogger(__name__)

class AirportItem(scrapy.Item):
    # Define your item fields here if needed for pipelines
    # For simplicity in this example, we'll yield dicts directly
    pass

class AirportsScraperSpider(scrapy.Spider):
    name = "driver_scraper"
    allowed_domains = ["acukwik.com"]
    custom_settings = {
        'ITEM_PIPELINES': {
            "acukwik_scraper.pipelines.DataCleaningAndStoringPipeline": 100,
            'acukwik_scraper.pipelines.ImageDownloadingPipeline': 300, # Enable image pipeline
        }
    }

    def __init__(self, f=None, tl=5, tp_min=3, tp_max=5, csv_row=None, max_links=None, scrape_images=None, *args, **kwargs):
        super(AirportsScraperSpider, self).__init__(*args, **kwargs)

        # --- Load command line arguments ---
        self.csv_file = f if f else DEFAULT_CSV_FILE
        self.timeout_after_clicking = float(tl)
        self.timeout_between_pages_min = float(tp_min)
        self.timeout_between_pages_max = float(tp_max)
        self.max_links = int(max_links) if max_links else None
        self.csv_row = csv_row if csv_row else DEFAULT_CSV_COLUMN
        self.image_dir = DEFAULT_IMAGE_DIR
        self.scrape_images = scrape_images.lower() in ('true', '1', 'yes') if scrape_images else False

        # --- Internal state ---
        self.driver = None
        self.temp_user_data_dir = None
        self.session = requests.Session() # For image downloads
        self.session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})

        # --- Create directories ---
        if not os.path.exists(self.image_dir):
            os.makedirs(self.image_dir)


    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        # --- Call parent's from_crawler first ---
        spider = super(AirportsScraperSpider, cls).from_crawler(crawler, *args, **kwargs)
        
        # --- Now the spider instance has the crawler attribute ---
        spider.crawler = crawler # This line might be redundant as super() should set it, but ensures it.
        
        # --- Connect the signal correctly here ---
        crawler.signals.connect(spider.spider_closed, signal=signals.spider_closed)
        
        return spider

    def spider_closed(self, spider):
        logger.info("Spider closing...")
        if self.driver:
            try:
                self.driver.quit()
                logger.info("WebDriver quit successfully.")
            except Exception as e:
                logger.error(f"Error quitting WebDriver: {e}")
        if self.temp_user_data_dir and os.path.exists(self.temp_user_data_dir):
            try:
                shutil.rmtree(self.temp_user_data_dir)
                logger.info(f"Deleted temporary user data directory: {self.temp_user_data_dir}")
            except Exception as e:
                logger.warning(f"Failed to delete temporary user data directory {self.temp_user_data_dir}: {e}")
        logger.info("Spider closed.")

    def start_requests(self):
        logger.info(f"Starting spider with parameters: "
                    f"csv_file={self.csv_file}, timeout_after_clicking={self.timeout_after_clicking}, "
                    f"tp_min={self.timeout_between_pages_min}, tp_max={self.timeout_between_pages_max}, "
                    f"max_links={self.max_links}, csv_row={self.csv_row}, scrape_images={self.scrape_images}")

        # --- Initialize WebDriver ---
        if not self._initialize_driver():
            logger.error("Failed to initialize WebDriver. Stopping spider.")
            return # Stop the spider

        # --- Check for existing cookies ---
        if os.path.exists(COOKIES_FILE):
            logger.info("Found existing cookies file.")
            yield scrapy.Request(url=ACCOUNT_URL, callback=self.verify_login, dont_filter=True, meta={'handle_httpstatus_list': [401, 200]})
        else:
            logger.info("No cookies file found. Initiating login.")
            yield scrapy.Request(url=LOGIN_URL, callback=self.selenium_login, dont_filter=True)

    def _initialize_driver(self):
        """Initializes the Selenium WebDriver with retry logic."""
        @tenacity.retry(stop=DEFAULT_RETRY_STOP, wait=DEFAULT_RETRY_WAIT, reraise=True)
        def _attempt_init():
            try:
                options = Options()
                # Create a temporary directory for user data
                self.temp_user_data_dir = tempfile.mkdtemp()
                options.add_argument(f'--user-data-dir={self.temp_user_data_dir}')
                options.add_argument("--no-sandbox")
                options.add_argument("--disable-dev-shm-usage")
                options.add_argument("--disable-gpu") # Often helpful
                options.add_argument("--window-size=1920,1080") # Consistent window size

                # Automatically manage ChromeDriver
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
                self.driver.implicitly_wait(DEFAULT_IMPLICIT_WAIT)
                logger.info("WebDriver initialized successfully.")
                return True
            except Exception as e:
                logger.error(f"Failed to initialize WebDriver (attempt will be retried): {e}")
                raise # Re-raise to trigger retry

        try:
            return _attempt_init()
        except Exception as e:
            logger.critical(f"WebDriver initialization failed after retries: {e}")
            return False

    def selenium_login(self, response):
        """Handles the Selenium-based login process."""
        logger.info("Starting Selenium login process.")
        try:
            self.driver.get(LOGIN_URL)
            logger.debug(f"Navigated to login page: {LOGIN_URL}")

            # Wait for critical elements
            wait = WebDriverWait(self.driver, DEFAULT_TIMEOUT)
            username_field = wait.until(EC.presence_of_element_located((By.NAME, "dnn$ctr572$Login$Login_DNN$txtUsername")))
            password_field = self.driver.find_element(By.NAME, "dnn$ctr572$Login$Login_DNN$txtPassword")
            remember_me_checkbox = self.driver.find_element(By.NAME, "dnn$ctr572$Login$Login_DNN$chkCookie")
            login_button = self.driver.find_element(By.ID, "dnn_ctr572_Login_Login_DNN_cmdLogin")

            # Fill credentials (Replace 'masked' with actual credentials or load from secure source)
            username_field.send_keys("ops@aeroworld.pk")
            password_field.send_keys("Mfb+4283")
            logger.debug("Filled username and password.")

            # Check 'Remember Me' if not already checked
            # if not remember_me_checkbox.is_selected():
            #     remember_me_checkbox.click()
            #     logger.debug("Checked 'Remember Me'.")
            #
            # # Click login button
            login_button.click()
            logger.debug("Clicked login button.")

            # Wait for login to complete (URL change or specific element on account page)
            # WebDriverWait(self.driver, DEFAULT_TIMEOUT).until(
            #     EC.url_contains("/Account") # Adjust condition if needed
            # )
            time.sleep(2)
            logger.info("Login successful.")

            # Save cookies
            selenium_cookies = self.driver.get_cookies()
            with open(COOKIES_FILE, 'w') as f:
                json.dump(selenium_cookies, f)
            logger.info("Saved cookies after successful login.")

            # Proceed to verify login via Scrapy request
            yield scrapy.Request(url=ACCOUNT_URL, callback=self.verify_login, dont_filter=True, meta={'handle_httpstatus_list': [401, 200]})

        except (TimeoutException, NoSuchElementException, WebDriverException) as e:
            logger.error(f"Selenium login failed due to WebDriver issue: {e}")
            # Consider retrying or stopping the spider
        except Exception as e:
            logger.error(f"Unexpected error during Selenium login: {e}")

    def verify_login(self, response):
        """Verifies login status using Scrapy requests with cookies."""
        logger.info(f"Verifying login status. Response status: {response.status}")

        if response.status == 200 and "Log Out" in response.text: # Adjust check based on actual page content
            logger.info("Login verification successful. Proceeding to scrape airport links.")
            # Load cookies for Selenium if needed later (though they should persist)
            self._load_cookies_to_driver()

            # Read airport links from CSV
            airport_links = []
            try:
                with open(self.csv_file, 'r', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        link = row.get(self.csv_row)
                        if link:
                            # Ensure link is absolute and stripped
                            if not link.startswith('http'):
                                link = urljoin(response.url, link)
                            airport_links.append(link.strip())
                logger.info(f"Loaded {len(airport_links)} links from CSV.")
            except FileNotFoundError:
                logger.error(f"CSV file not found: {self.csv_file}")
                return
            except Exception as e:
                logger.error(f"Error reading CSV file: {e}")
                return

            # Limit links if requested
            links_to_scrape = airport_links[:self.max_links] if self.max_links else airport_links
            if self.max_links:
                logger.info(f"Limited scraping to first {self.max_links} links.")

            # Yield requests for each airport link
            for airport_link in links_to_scrape:
                random_timeout = random.uniform(self.timeout_between_pages_min, self.timeout_between_pages_max)
                logger.debug(f"Waiting {random_timeout:.2f}s before scraping {airport_link}")
                time.sleep(random_timeout)

                yield SeleniumRequest(
                    url=airport_link,
                    callback=self.selenium_click_emails,
                    meta={'link': airport_link}, # Pass the link for reference
                    wait_time=DEFAULT_TIMEOUT,
                    dont_filter=True # Might be needed if scraping same domain
                )
        else:
            logger.warning("Login verification failed. Status code or page content indicates not logged in.")
            # Optionally, trigger re-login or stop spider
            # For now, we stop
            return

    def _load_cookies_to_driver(self):
        """Loads cookies from the file into the Selenium driver."""
        if not self.driver:
            logger.warning("Cannot load cookies: WebDriver not initialized.")
            return
        try:
            with open(COOKIES_FILE, 'r') as f:
                cookies = json.load(f)
            self.driver.get("https://acukwik.com/") # Navigate to domain first
            for cookie in cookies:
                # Handle potential issues with cookie attributes
                cookie_to_add = {k: v for k, v in cookie.items() if k not in ['sameSite', 'expiry']} # Remove problematic keys if needed
                try:
                    self.driver.add_cookie(cookie_to_add)
                    logger.debug(f"Added cookie: {cookie_to_add.get('name', 'Unknown')}")
                except Exception as e:
                    logger.warning(f"Failed to add cookie {cookie.get('name', 'Unknown')}: {e}")
            logger.info("Cookies loaded into Selenium driver.")
        except FileNotFoundError:
            logger.warning("Cookies file not found when trying to load into driver.")
        except json.JSONDecodeError as e:
            logger.error(f"Error decoding cookies JSON: {e}")
        except Exception as e:
            logger.error(f"Unexpected error loading cookies into driver: {e}")

    def selenium_click_emails(self, response):
        """Clicks email buttons on the airport page using Selenium."""
        link = response.meta.get('link')
        logger.info(f"Processing airport page: {link}")

        try:
            # Ensure driver is at the correct URL
            if self.driver.current_url != response.url:
                self.driver.get(response.url)
                logger.debug(f"Navigated driver to {response.url}")

            # Click potential modal close button
            try:
                modal_close = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, "bluePanelTitle")) # Adjust selector if needed
                )
                modal_close.click()
                logger.debug("Clicked modal close button.")
                time.sleep(1) # Brief pause after modal interaction
            except TimeoutException:
                logger.debug("No modal close button found or not clickable.")
            except Exception as e:
                 logger.warning(f"Error clicking modal close button: {e}")

            # Click 'ghEmail' buttons
            self._click_buttons_by_class("ghEmail")

            # Click 'sEmail' buttons
            self._click_buttons_by_class("sEmail")

            # Wait for potential page updates after clicks
            logger.debug(f"Waiting {self.timeout_after_clicking}s after clicks.")
            time.sleep(self.timeout_after_clicking)

            # Get updated page source
            updated_html = self.driver.page_source
            # Create a new Scrapy response object from the updated HTML
            updated_response = HtmlResponse(url=self.driver.current_url, body=updated_html, encoding='utf-8')

            # Parse the updated page
            yield from self.parse_airport(updated_response, link)

        except WebDriverException as e:
            logger.error(f"WebDriver error while processing {link}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error while processing {link}: {e}")

    def _click_buttons_by_class(self, class_name):
        """Helper to find and click buttons by class name."""
        try:
            buttons = WebDriverWait(self.driver, 3).until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, class_name))
            )
            logger.info(f"Found {len(buttons)} '{class_name}' buttons.")
            for i, button in enumerate(buttons):
                try:
                    # Prefer standard click
                    button.click()
                    logger.debug(f"Clicked '{class_name}' button {i+1}/{len(buttons)} (standard click).")
                    self.driver.implicitly_wait(1) # Brief implicit wait
                except WebDriverException:
                    # Fallback to JavaScript click
                    try:
                        self.driver.execute_script("arguments[0].click();", button)
                        logger.debug(f"Clicked '{class_name}' button {i+1}/{len(buttons)} (JS click).")
                        self.driver.implicitly_wait(1)
                    except WebDriverException as js_e:
                        logger.warning(f"Failed to click '{class_name}' button {i+1} (both standard and JS): {js_e}")
        except TimeoutException:
            logger.debug(f"No '{class_name}' buttons found or clickable within timeout.")
        except Exception as e:
            logger.warning(f"Error finding/clicking '{class_name}' buttons: {e}")


     # Inside your AirportsScraperSpider class

    def parse_airport(self, response, link):
        """Parses the airport data from the response."""
        logger.info(f"Parsing data for airport: {link}")
        
        # --- Initialize data structures ---
        airport_details = {}
        additional_airport_data = {}
        airport_info = {}
        contact_info = []
        fbo_data = []
        handler_data = [] # Note: Keeping the correct spelling here, but yielding with typo
        caterers_list = []
        hotels_list = []
        car_rentals_list = []
        
        # Runway Diagram placeholders
        runway_diagram_url = None
        runway_diagram_local_path = None
        runway_diagram_name = None
        referer_link = link # Used for Runway Diagram

        try:
            # --- Airport Details (h1, h2, h3) ---
            airport_name = response.css('h1::text').get()
            if airport_name:
                airport_details['Name'] = airport_name.strip()
            else:
                logger.warning("Airport name (h1) not found.")

            city_country_text = response.css('h2::text').get()
            if city_country_text:
                parts = city_country_text.split(',', 2) # Split max 2 times
                airport_details['City'] = parts[0].strip() if len(parts) > 0 else 'N/A'
                airport_details['Country'] = parts[1].strip() if len(parts) > 1 else 'N/A'
            else:
                logger.warning("City/Country (h2) not found.")
            
            # Add Link to airport_details
            airport_details['Link'] = link

            codes_text = response.css('h3::text').get()
            if codes_text:
                # Safer parsing for ICAO/IATA
                icao_match = "ICAO -" in codes_text
                iata_match = "IATA -" in codes_text
                if icao_match:
                    icao_start = codes_text.find("ICAO -") + len("ICAO - ")
                    icao_end = codes_text.find(",", icao_start) if codes_text.find(",", icao_start) != -1 else len(codes_text)
                    airport_details['ICAO'] = codes_text[icao_start:icao_end].strip()
                if iata_match:
                    iata_start = codes_text.find("IATA -") + len("IATA - ")
                    airport_details['IATA'] = codes_text[iata_start:].strip() # Assume IATA is last
            else:
                logger.warning("Airport codes (h3) not found.")


            # --- Additional Airport Data (.clearfix.mb20px.mainInfo .table) ---
            for entry in response.css(".clearfix.mb20px.mainInfo .table"):
                key = entry.css(".bold::text").get()
                value = entry.css(".w45p::text, .w38p::text").get() # Combine selectors
                if key and value:
                    additional_airport_data[key.strip()] = value.strip()
                elif key:
                     logger.debug(f"Found key '{key}' but no value in Additional Airport Data.")


            # --- Airport Information (div.clearboth.p3xp.bold + div.clearboth.p3px) ---
            keys = response.css('div.clearboth.p3xp.bold::text').getall()
            values = response.css('div.clearboth.p3px::text').getall()
            # Use zip_longest in case lists are slightly mismatched
            from itertools import zip_longest
            for key, value in zip_longest(keys, values, fillvalue=''):
                if key: # Only add if key exists
                    clean_key = key.strip().replace("\xa0", "")
                    clean_value = value.strip().replace("\xa0", "") if value else 'N/A'
                    airport_info[clean_key] = clean_value


            # --- Contact Information ---
            for contact in response.css('div.results-content div.clearfix.result'):
                # Get email_href first
                email_href_raw = contact.css('div.w22p.fl.p3px div a::attr(href)').get()
                email_clean = 'N/A'
                if email_href_raw:
                    if email_href_raw.startswith('mailto:'):
                        email_clean = email_href_raw[7:] # Remove 'mailto:'
                    else:
                        email_clean = email_href_raw

                contact_info.append({
                    'ContactName': contact.css('div.w31p.fl.bold::text').get(default='N/A').strip(),
                    # Frequency is tricky in the HTML, often inside a div, not just text
                    'Frequency': contact.css('div.w17p.fl.p3px div::text').get(default='N/A').strip(), # Adjusted selector
                    'Phone': contact.css('div.w30p.fl.p3px div.clearfix:contains("Phone") + div::text').get(default='N/A').strip(),
                    'Fax': contact.css('div.w30p.fl.p3px div.clearfix:contains("Fax") + div::text').get(default='N/A').strip(),
                    # Email link (processed)
                    'Email': email_clean,
                    'Website': contact.css('div.w22p.fl.p3px a[href^="http"]::attr(href)').get(default='N/A'),
                })

            # --- FBO Information ---
            for fbo in response.css('div.fbo div.vendor'):
                 # Safer email extraction for FBO
                 fbo_email_href_raw = fbo.css('div.w35p.bold:contains("Email") + div a::attr(href)').get()
                 fbo_email_clean = 'N/A'
                 if fbo_email_href_raw and fbo_email_href_raw.startswith('mailto:'):
                     fbo_email_clean = fbo_email_href_raw[7:]
                 elif fbo_email_href_raw:
                     fbo_email_clean = fbo_email_href_raw

                 fbo_data.append({
                    'Name': fbo.css('div.vendorName a strong::text').get(default='N/A').strip(),
                    'Address': ' '.join(fbo.css('div.w35p.bold:contains("Address") + div::text').getall()).strip() or 'N/A', # Join lines
                    'Phone': fbo.css('div.w35p.bold:contains("Phone") + div::text').get(default='N/A').strip(),
                    'Tel': fbo.css('div.w35p.bold:contains("Tel After Hours") + div::text').get(default='N/A').strip(),
                    'Email': fbo_email_clean, # Use processed email
                    'Website': fbo.css('div.w35p.bold:contains("Website") + div a::attr(href)').get(default='N/A'),
                    'Aftn': fbo.css('div.w30p.fl strong:contains("AFTN") + div::text').get(default='N/A').strip(),
                    'Sita': fbo.css('div.w30p.fl strong:contains("SITA") + div::text').get(default='N/A').strip()
                })

            # --- Handler Information ---
            for handler in response.css('div.bluePanelContent div.vendor'):
                 # Safer email extraction for Handler
                 handler_email_href_raw = handler.css('div.w35p.bold:contains("Email") + div a::attr(href)').get()
                 handler_email_clean = 'N/A'
                 if handler_email_href_raw and handler_email_href_raw.startswith('mailto:'):
                     handler_email_clean = handler_email_href_raw[7:]
                 elif handler_email_href_raw:
                     handler_email_clean = handler_email_href_raw

                 handler_data.append({ # Keep correct internal name
                    "Name": handler.css('div.vendorName a strong::text').get(default='N/A').strip(),
                    "Address": ' '.join(handler.css('div.w35p.bold:contains("Address") + div::text').getall()).strip() or 'N/A', # Join lines
                    "Phone": handler.css('div.w35p.bold:contains("Phone") + div::text').get(default='N/A').strip(),
                    "Fax": handler.css('div.w35p.bold:contains("Fax") + div::text').get(default='N/A').strip(),
                    "Email": handler_email_clean, # Use processed email
                    "Website": handler.css('div.w35p.bold:contains("Website") + div a::attr(href)').get(default='N/A')
                })

            # --- Caterers Information ---
            for caterer in response.css('div.bluePanelRow.clearfix.mbGrey'):
                # Safer email extraction for Caterer
                caterer_email_href_raw = caterer.css('div.fl.w35p.bold:contains("Email") a::attr(href)').get()
                caterer_email_clean = 'N/A'
                if caterer_email_href_raw and caterer_email_href_raw.startswith('mailto:'):
                    caterer_email_clean = caterer_email_href_raw[7:]
                elif caterer_email_href_raw:
                    caterer_email_clean = caterer_email_href_raw

                caterers_list.append({
                    "Name": caterer.css('div.clearboth a strong.fs18px::text').get(default='N/A').strip(),
                    "Phone": caterer.css('div.fl.w35p.bold:contains("Phone") + div::text').get(default='N/A').strip(),
                    "Fax": caterer.css('div.fl.w35p.bold:contains("Fax") + div::text').get(default='N/A').strip(),
                    # Use processed email
                    "Email": caterer_email_clean,
                    "Website": caterer.css('div.fl.w35p.bold:contains("Website") + div a::attr(href)').get(default='N/A'),
                    "SITA": caterer.css('div.fl.w35p.bold:contains("SITA") + div::text').get(default='N/A').strip()
                })

            # --- Hotel Info ---
            for hotel in response.css('.bluePanelRow.clearfix.mbGrey.noPaddingLR'):
                info = hotel.css('.w20p .fl.w100p::text').extract()
                address_lines = hotel.css('.w25p .fl.w100p::text').getall()
                hotels_list.append({
                    'Name': hotel.css('.fs18px.bold::text').get(default='N/A').strip(),
                    'Phone': hotel.css('div:contains("Phone") + div::text').get(default='N/A').strip(),
                    'Fax': hotel.css('div:contains("Fax") + div::text').get(default='N/A').strip(),
                    'Website': hotel.css('div:contains("Website") + div a::attr(href)').get(default='N/A'),
                    'Address': ', '.join([line.strip() for line in address_lines if line.strip()]) or 'N/A', # Join address lines cleanly
                    'Distance': info[0].strip() if len(info) > 0 and info[0].strip() else 'N/A',
                    'PriceRange': info[1].strip() if len(info) > 1 and info[1].strip() else 'N/A',
                })

            # --- Car Rental Info ---
            for rental in response.css('div.bluePanelContent'): # This is very broad
                 # Only parse if it looks like a car rental entry (has specific elements)
                 if rental.css('div.w30p.fs18px.bold::text').get() or rental.css('div.w35p div.fl.w65p::text').get():
                    # Safer email extraction for Car Rental
                    rental_email_href_raw = rental.css('div.fl.w35p div.fl.w65p + div a::attr(href)').get()
                    rental_email_clean = 'N/A'
                    if rental_email_href_raw and rental_email_href_raw.startswith('mailto:'):
                        rental_email_clean = rental_email_href_raw[7:]
                    elif rental_email_href_raw:
                        rental_email_clean = rental_email_href_raw

                    car_rentals_list.append({
                        'SupplierId': rental.css('input[type="hidden"]::attr(value)').get(default='N/A'),
                        'CompanyName': rental.css('div.w30p.fs18px.bold::text').get(default='N/A').strip(),
                        'Phone': rental.css('div.w35p div.fl.w65p::text').get(default='N/A').strip(),
                        # Use processed email
                        'Email': rental_email_clean,
                        'Website': rental.css('div.w35p a::attr(href)').get(default='N/A')
                    })


            # --- Runway Diagram ---
            # This part is handled by the ImageDownloadPipeline, but we prepare the data here.
            # Only process if scrape_images is True (matching old behavior for yielding)
            if getattr(self, 'scrape_images', False): # Safely get attribute, default False
                try:
                    # The ID selector seems specific enough. Let's try it.
                    runway_diagram_div = self.driver.find_element(By.ID, "dnn_ctr422_VDC_ctl00_divRunwayDiagram")
                    image_element = runway_diagram_div.find_element(By.TAG_NAME, "img")
                    src = image_element.get_attribute("src")
                    if src:
                         # Make sure it's an absolute URL
                         runway_diagram_url = urljoin(self.driver.current_url, src)
                         logger.debug(f"Found runway diagram URL: {runway_diagram_url}")
                         # Prepare path and name if URL is found
                         icao_code = airport_details.get('ICAO', 'UNKNOWN')
                         runway_diagram_name = f"{icao_code}_runway_diagram.jpg"
                         runway_diagram_local_path = os.path.join(self.image_dir, runway_diagram_name)
                    else:
                        logger.warning("Runway diagram image element found, but 'src' attribute is empty.")
                except NoSuchElementException:
                    logger.info("Runway diagram section or image not found on the page.")
                except Exception as e:
                     logger.warning(f"Error finding runway diagram image: {e}")

        except Exception as e:
            logger.error(f"Error parsing airport data for {link}: {e}", exc_info=True) # Log full traceback

        # --- Yield data matching the OLD structure ---
        item_to_yield = {
            "Airport Details": airport_details,
            "Additional Airport Details": additional_airport_data,
            "Airport Information": airport_info,
            "Contact Information": contact_info,
            "FBO Information": fbo_data,
            # Yield Handler data with the OLD TYPO to match DB expectations
            "Handler Inforamtion": handler_data,
            "Caterers Information": caterers_list,
            "Hotel Info": hotels_list,
            "Car Rental Info": car_rentals_list,
        }

        # Conditionally add Runway Diagram fields ONLY if scrape_images is True
        # This matches the explicit conditional logic in the OLD code's yield statement
        if getattr(self, 'scrape_images', False):
             item_to_yield.update({
                "Runway Diagram Url": runway_diagram_url,
                "Runway Diagram Local Path": runway_diagram_local_path,
                "Referer Url": referer_link, # Already set to link
                "Runway Diagram Image Name": runway_diagram_name,
             })
        else:
            # Explicitly set them to None if not scraping images
            # (Though not strictly necessary if the DB pipeline ignores missing keys)
             item_to_yield.update({
                "Runway Diagram Url": None,
                "Runway Diagram Local Path": None,
                "Referer Url": None,
                "Runway Diagram Image Name": None,
             })

        logger.debug(f"Yielding item for {link}")
        yield item_to_yield

