"""
Authentication module for cookie-based authentication.
Handles loading and applying cookies to Selenium WebDriver instances.
"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from selenium.webdriver.remote.webdriver import WebDriver

logger = logging.getLogger(__name__)


class CookieAuthentication:
    """Handles cookie-based authentication for web scraping."""
    
    def __init__(self, cookies_file: str):
        """
        Initialize cookie authentication.
        
        Args:
            cookies_file: Path to JSON file containing cookies
        """
        self.cookies_file = Path(cookies_file)
        self.cookies = self._load_cookies()
    
    def _load_cookies(self) -> List[Dict[str, Any]]:
        """
        Load cookies from JSON file.
        
        Returns:
            List of cookie dictionaries
            
        Raises:
            FileNotFoundError: If cookies file doesn't exist
            ValueError: If cookies file is invalid
        """
        if not self.cookies_file.exists():
            logger.warning(f"Cookies file not found: {self.cookies_file}")
            logger.warning("Proceeding without authentication cookies")
            return []
        
        try:
            with open(self.cookies_file, 'r') as f:
                cookies = json.load(f)
            
            if not isinstance(cookies, list):
                raise ValueError("Cookies must be a JSON array")
            
            logger.info(f"Loaded {len(cookies)} cookies from {self.cookies_file}")
            return cookies
            
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in cookies file: {e}")
            raise ValueError(f"Failed to parse cookies file: {e}")
        except Exception as e:
            logger.error(f"Failed to load cookies: {e}")
            raise
    
    def apply_cookies(self, driver: WebDriver, base_url: str) -> None:
        """
        Apply cookies to a WebDriver instance.
        
        Args:
            driver: Selenium WebDriver instance
            base_url: Base URL to navigate to before applying cookies
        """
        if not self.cookies:
            logger.debug("No cookies to apply")
            return
        
        try:
            # Must navigate to the domain first before adding cookies
            logger.debug(f"Navigating to {base_url} to set cookies")
            driver.get(base_url)
            
            # Add each cookie
            cookies_added = 0
            for cookie in self.cookies:
                try:
                    # Selenium requires specific cookie format
                    cookie_dict = {
                        "name": cookie.get("name"),
                        "value": cookie.get("value"),
                        "domain": cookie.get("domain", ""),
                        "path": cookie.get("path", "/"),
                    }
                    
                    # Add optional fields if present
                    if "expiry" in cookie:
                        cookie_dict["expiry"] = cookie["expiry"]
                    if "secure" in cookie:
                        cookie_dict["secure"] = cookie["secure"]
                    if "httpOnly" in cookie:
                        cookie_dict["httpOnly"] = cookie["httpOnly"]
                    if "sameSite" in cookie:
                        cookie_dict["sameSite"] = cookie["sameSite"]
                    
                    driver.add_cookie(cookie_dict)
                    cookies_added += 1
                    
                except Exception as e:
                    logger.warning(f"Failed to add cookie {cookie.get('name')}: {e}")
                    continue
            
            logger.info(f"Successfully applied {cookies_added}/{len(self.cookies)} cookies")
            
            # Refresh page to ensure cookies are active
            driver.refresh()
            
        except Exception as e:
            logger.error(f"Failed to apply cookies: {e}")
            raise
    
    def verify_authentication(self, driver: WebDriver, 
                            success_indicators: List[str] = None,
                            failure_indicators: List[str] = None) -> bool:
        """
        Verify that authentication was successful.
        
        Args:
            driver: Selenium WebDriver instance
            success_indicators: List of text strings that indicate successful auth
            failure_indicators: List of text strings that indicate auth failure
            
        Returns:
            True if authenticated, False otherwise
        """
        try:
            page_source = driver.page_source.lower()
            
            # Check for failure indicators
            if failure_indicators:
                for indicator in failure_indicators:
                    if indicator.lower() in page_source:
                        logger.error(f"Authentication failed: found '{indicator}'")
                        return False
            
            # Check for success indicators
            if success_indicators:
                for indicator in success_indicators:
                    if indicator.lower() in page_source:
                        logger.info(f"Authentication verified: found '{indicator}'")
                        return True
                
                logger.warning("No success indicators found in page")
                return False
            
            # If no indicators specified, assume success
            logger.info("Authentication verification skipped (no indicators specified)")
            return True
            
        except Exception as e:
            logger.error(f"Failed to verify authentication: {e}")
            return False
    
    def export_cookies(self, driver: WebDriver, output_file: str = None) -> None:
        """
        Export cookies from a WebDriver instance to a file.
        Useful for saving authenticated session cookies.
        
        Args:
            driver: Selenium WebDriver instance
            output_file: Path to save cookies (defaults to cookies_file)
        """
        if output_file is None:
            output_file = self.cookies_file
        else:
            output_file = Path(output_file)
        
        try:
            cookies = driver.get_cookies()
            
            with open(output_file, 'w') as f:
                json.dump(cookies, f, indent=2)
            
            logger.info(f"Exported {len(cookies)} cookies to {output_file}")
            
        except Exception as e:
            logger.error(f"Failed to export cookies: {e}")
            raise
    
    @staticmethod
    def create_from_browser_export(export_file: str, output_file: str = "cookies.json") -> 'CookieAuthentication':
        """
        Create CookieAuthentication from a browser cookie export.
        
        Many browser extensions export cookies in slightly different formats.
        This method attempts to normalize them.
        
        Args:
            export_file: Path to browser cookie export file
            output_file: Path to save normalized cookies
            
        Returns:
            CookieAuthentication instance with loaded cookies
        """
        try:
            with open(export_file, 'r') as f:
                raw_cookies = json.load(f)
            
            # Normalize cookie format
            normalized_cookies = []
            for cookie in raw_cookies:
                normalized = {
                    "name": cookie.get("name") or cookie.get("Name"),
                    "value": cookie.get("value") or cookie.get("Value"),
                    "domain": cookie.get("domain") or cookie.get("Domain"),
                    "path": cookie.get("path") or cookie.get("Path", "/"),
                }
                
                # Add optional fields
                if "expirationDate" in cookie:
                    normalized["expiry"] = int(cookie["expirationDate"])
                elif "expiry" in cookie:
                    normalized["expiry"] = cookie["expiry"]
                
                if "secure" in cookie or "Secure" in cookie:
                    normalized["secure"] = cookie.get("secure") or cookie.get("Secure")
                
                if "httpOnly" in cookie or "HttpOnly" in cookie:
                    normalized["httpOnly"] = cookie.get("httpOnly") or cookie.get("HttpOnly")
                
                if "sameSite" in cookie or "SameSite" in cookie:
                    normalized["sameSite"] = cookie.get("sameSite") or cookie.get("SameSite")
                
                normalized_cookies.append(normalized)
            
            # Save normalized cookies
            with open(output_file, 'w') as f:
                json.dump(normalized_cookies, f, indent=2)
            
            logger.info(f"Created {output_file} with {len(normalized_cookies)} cookies")
            
            return CookieAuthentication(output_file)
            
        except Exception as e:
            logger.error(f"Failed to create from browser export: {e}")
            raise
