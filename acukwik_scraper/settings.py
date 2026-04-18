from shutil import which
  
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = "acukwik_scraper"

SPIDER_MODULES = ["acukwik_scraper.spiders"]
NEWSPIDER_MODULE = "acukwik_scraper.spiders"


# Crawl responsibly by identifying yourself (and your website) on the user-agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Obey robots.txt rules
ROBOTSTXT_OBEY = False

# Configure maximum concurrent requests performed by Scrapy (default: 16)
CONCURRENT_REQUESTS = 3
CONCURRENT_REQUESTS_PER_DOMAIN = 1
# CONCURRENT_REQUESTS_PER_IP is deprecated in newer Scrapy versions

COOKIES_ENABLED = True

NOSQL_URI = "mongodb://localhost:27017/"
  
DOWNLOADER_MIDDLEWARES = {
     'scrapy_selenium.SeleniumMiddleware': 800
     }

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    # Disabled for airport-links regeneration; re-enable for normal scraping.
    # "acukwik_scraper.pipelines.DataCleaningAndStoringPipeline": 100,
    # "acukwik_scraper.pipelines.ImageDownloadingPipeline": 1,
}

MEDIA_ALLOW_REDIRECTS = True
IMAGES_STORE = 'images'

# Set settings whose default value is deprecated to a future-proof value
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"

FEED_EXPORT_ENCODING = "utf-8"
FEED_FORMAT = "csv"
FEED_URI = "country_links.csv"
