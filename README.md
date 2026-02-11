# Acukwik Scraper

## Code Structure (Using Scrapy Framework)

```bash
my_scrapy_project/
├── scrapy.cfg          # Scrapy project configuration file
├── my_scrapy_project/  # Scrapy project directory
│   ├── __init__.p
│   ├── items.py        # Define your scraped items here
│   ├── middlewares.py  # Project middlewares
│   ├── pipelines.py    # Process scraped items here
│   ├── settings.py     # Project settings
│   └── spiders/        # Your spiders live here
│       ├── __init__.py
│       ├── airport_links_scraper.py  # scrapes all links and writes to csv
│       └── driver_scraper # uses selenium driver to scrape everything
│
├── requirements.txt    # Project dependencies
└──README.md           # This file
```

## Getting Started=

### 1. Create and Activate a Virtual Environment (Recommended)

```bash
python3 -m venv .venv       # Create a virtual environment (replace .venv with your preferred name)
source .venv/bin/activate  # Activate the virtual environment (Linux/macOS)
.venv\Scripts\activate    # Activate the virtual environment (Windows)
```

### 2. Install all pip packages using requirements.txt

```bash
pip install -r requirements.txt
```

### 3. Running the Scraper

#### a. Scraping links of the airport pages (Optional) (Skip if you want to use country_links.csv)

##### Outputs all links in different csv file with respect to country name and also appends country_links.csv if not already created

Before running the links scraper make sure to have these settings enabled / uncommented in settings.py

```python

FEED_EXPORT_ENCODING = "utf-8"
FEED_FORMAT = "csv"
FEED_URI = "country_links.csv"
```

Run the script

```bash
scrapy crawl airport_links
```

#### b. Scraping the airport pages

Before running the airport pages scraper make sure to remove or comment FEED_FORMAT and FEED_URI

```python
#settings.py


# remove or comment out FEED_FORMAT and FEED_URI

#FEED_FORMAT = "csv"
#FEED_URI = "country_links.csv"
```

Run the script for scraping an airport page

##### Set Mongodb link to connect to

```python
# Default value
NOSQL_URI = "mongodb://localhost:27017/"

# Usage
NOSQL_URI = "mongodb://your_mongodb_host:your_mongodb_port/"

# Usage for protected mongo user
NOSQL_URI = "mongodb://username:password@your_mongodb_host:your_mongodb_port/"

```

##### Main scraping page -> selenium_scraper.py

##### Data Pipeline page -> pipelines.py

```bash
scrapy crawl driver_scraper

# default values for all flags

scrapy crawl driver_scraper -a f="data.csv" -a tl=10 -a tp_min=3 -a tp_max=8 -a csv_row="Airport Link" -o output.json
```

##### Explanation of flags:

- `-a f="data.csv"` → Specifies the input file containing airport data. Absolute links can also be used
- `-a tl=10` → Sets the time delay after clicking all buttons to resolve all network requests.
- `-a tp_min=3` → Sets the minimum time (in seconds) between requests.
- `-a tp_max=8` → Sets the maximum time (in seconds) between requests.
- `-a csv_row="Airport Link"` → Filters data based on a specific row in the CSV.
- `-a max_links=5` → Limits the amount of links from the csv that need to be scraped
- `-o output.json` → Saves the scraped data in a JSON file instead of MongoDB.

##### To disable saving of output in mongodb database and only get a json output

```python
# pipeline.py
# comment out the function call self.store_in_nosql

def process_item(self, item, spider):
    cleaned_item = self.clean_data(item)


    # self.store_in_nosql(cleaned_item)

    return cleaned_item


```
