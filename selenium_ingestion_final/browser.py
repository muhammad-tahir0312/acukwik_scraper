"""Shared Selenium configuration for login and authenticated retrieval."""
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions


def create_driver(config):
    settings = config["selenium"]
    browser = settings.get("browser", "chrome").lower()
    if browser == "chrome":
        options = ChromeOptions()
        if settings.get("headless", True):
            options.add_argument("--headless=new")
        for argument in (
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            "--window-size=1920,1080", "--disable-blink-features=AutomationControlled",
        ):
            options.add_argument(argument)
        # Retain the Chrome settings used by the original Selenium scraper.
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        driver = webdriver.Chrome(options=options)
        try:
            # Use a complete UA for the installed Chrome, consistently for login
            # and retrieval, avoiding the old incomplete Windows UA.
            user_agent = settings.get("user_agent") or driver.execute_script(
                "return navigator.userAgent"
            ).replace("HeadlessChrome", "Chrome")
            driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": user_agent})
        except Exception:
            driver.quit()
            raise
    elif browser == "firefox":
        options = FirefoxOptions()
        if settings.get("headless", True):
            options.add_argument("--headless")
        if settings.get("user_agent"):
            options.set_preference("general.useragent.override", settings["user_agent"])
        driver = webdriver.Firefox(options=options)
    else:
        raise ValueError(f"Unsupported browser: {browser}")
    driver.set_page_load_timeout(settings.get("page_load_timeout", 100))
    driver.set_script_timeout(45)
    driver.implicitly_wait(settings.get("implicit_wait", 0))
    return driver
