# Aviation Data Ingestion Layer

A clean, production-ready Selenium-based scraper designed to feed structured data into an ETL pipeline. This is **ingestion only** - no database writes, no joins, just clean JSON output ready for the next stage.

## 🎯 Philosophy

```
Selenium Scraper → Structured JSON → raw_scraped_records → ETL Pipeline
```

- **Trust nothing at scrape time** - just capture data
- **Fail loudly** - log everything, never guess
- **Be resumable** - support interruptions and retries
- **Stay modular** - easy to extend for new sources

## 📦 Architecture

```
selenium_ingestion_refactored/
├── auth.py              # Cookie-based authentication
├── config_loader.py     # Configuration management
├── parsers.py           # Entity parsers (Airport, Organization)
├── validators.py        # Data validation rules
├── output.py            # JSON output writer
├── progress.py          # Progress tracking for resumability
├── scraper.py           # Main orchestrator
├── config.yaml          # Configuration file
└── README.md            # This file
```

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install selenium pyyaml jsonschema
```

### 2. Setup Selenium Driver

**Chrome:**
```bash
# Ubuntu/Debian
sudo apt-get install chromium-chromedriver

# macOS
brew install --cask chromedriver
```

**Firefox:**
```bash
# Ubuntu/Debian
sudo apt-get install firefox-geckodriver

# macOS
brew install geckodriver
```

### 3. Configure Cookies (if authentication required)

Export cookies from your browser using a cookie extension and save to `cookies.json`:

```json
[
  {
    "name": "session_id",
    "value": "your_session_value",
    "domain": ".acukwik.com",
    "path": "/"
  }
]
```

### 4. Prepare Input CSV

Create a CSV file with URLs to scrape:

```csv
url,entity_type,airport_icao
https://www.acukwik.com/airport/KJFK,airport,
https://www.acukwik.com/fbo/12345,organization,KJFK
```

**Required columns:**
- `url` or `link`: URL to scrape
- `entity_type`: "airport" or "organization" (default: organization)
- `airport_icao` (optional): Associated airport for organizations

### 5. Configure and Run

Edit `config.yaml`:

```yaml
input:
  csv_paths:
    - "your_input_file.csv"

scraping:
  parallel_workers: 4
  max_retries: 3
```

Run the scraper:

```bash
python scraper.py
```

Or specify a custom config:

```bash
python scraper.py my_config.yaml
```

## 📄 Output Format

### Successful Records

Output: `output/scraped_records_YYYYMMDD_HHMMSS.jsonl`

Each line is a JSON object:

```json
{
  "source": "acukwik",
  "entity_type": "organization",
  "external_id": "acukwik_FBO_12345",
  "scraped_at": "2026-02-10T12:34:56Z",
  "url": "https://www.acukwik.com/fbo/12345",
  "data": {
    "name": "Example FBO Services",
    "roles": ["FBO", "FUEL"],
    "website": "https://example.com",
    "email": "ops@example.com",
    "phone": "+1-555-1234",
    "services": ["JET_A1", "GROUND_HANDLING"],
    "associated_airports": ["KJFK", "KLAX"],
    "address": "123 Airport Rd, New York, NY",
    "hours": "24/7"
  },
  "validation_errors": []
}
```

### Failed Records

Output: `output/failed_records_YYYYMMDD_HHMMSS.jsonl`

```json
{
  "external_id": "acukwik_123",
  "entity_type": "organization",
  "url": "https://www.acukwik.com/fbo/123",
  "scrape_status": "FAILED",
  "error": "Timeout while loading page",
  "scraped_at": "2026-02-10T12:34:56Z"
}
```

## 🔄 Resume Capability

The scraper maintains progress in `progress/scraping_progress.json`:

```json
{
  "completed_ids": ["acukwik_123", "acukwik_456"],
  "failed_ids": {"acukwik_789": 2},
  "metadata": {
    "completed_count": 2,
    "failed_count": 1,
    "last_updated": "2026-02-10T12:34:56Z"
  }
}
```

**To resume:** Just run the scraper again with the same config. It will:
- Skip completed IDs
- Retry failed IDs (up to max_retries)
- Continue where it left off

**To start fresh:** Delete the progress file before running.

## 🔧 Configuration Reference

### Source Configuration
```yaml
source:
  name: "acukwik"  # Used in external_id generation
```

### Input Configuration
```yaml
input:
  csv_paths:
    - "file1.csv"
    - "file2.csv"  # Multiple files supported
```

### Scraping Configuration
```yaml
scraping:
  parallel_workers: 4      # Workers for parallel execution (1 = sequential)
  max_retries: 3          # Retry attempts per URL
  retry_backoff: 5        # Base backoff seconds (exponential)
  delay_between_requests: 2  # Delay between requests
```

### Selenium Configuration
```yaml
selenium:
  browser: "chrome"       # chrome or firefox
  headless: true         # Headless mode
  page_load_timeout: 30  # Page load timeout
  implicit_wait: 10      # Implicit wait timeout
```

### Authentication Configuration
```yaml
authentication:
  enabled: true
  cookies_file: "cookies.json"
  base_url: "https://www.acukwik.com"
```

### Validation Configuration
```yaml
validation:
  strict_mode: false  # Reject invalid records if true
  required_fields:
    airport:
      - "name"
      - "icao"
    organization:
      - "name"
```

## 🧪 Entity Types

### Airport Entity

**Fields:**
- `icao`: ICAO code (4 letters, uppercase)
- `iata`: IATA code (3 letters, uppercase, optional)
- `name`: Airport name
- `city`: City name
- `country`: Country name
- `latitude`: Latitude (float)
- `longitude`: Longitude (float)
- `elevation`: Elevation in feet (integer)
- `timezone`: Timezone string
- `runways`: Array of runway objects
- `frequencies`: Array of frequency objects

### Organization Entity

**Fields:**
- `name`: Organization name
- `description`: Description text
- `website`: Website URL
- `email`: Email address (lowercase)
- `phone`: Phone number
- `services`: Array of service strings
- `roles`: Array of role strings (FBO, FUEL, HANDLER, HOTEL, etc.)
- `associated_airports`: Array of ICAO codes
- `address`: Physical address
- `hours`: Operating hours

**Role Detection:**
Roles are automatically detected based on page content and URL:
- FBO: Fixed Base Operator
- FUEL: Fuel supplier
- HANDLER: Ground handler
- HOTEL: Hotel/Accommodation
- CATERING: Catering service
- MAINTENANCE: Maintenance/MRO
- CUSTOMS: Customs/Immigration

## 🛠 Extending the Scraper

### Add a New Entity Type

1. **Create parser in `parsers.py`:**

```python
class NewEntityParser(BaseParser):
    def parse(self, url: str) -> Dict[str, Any]:
        data = {
            "field1": self._extract_field1(),
            "field2": self._extract_field2(),
        }
        
        return {
            "source": self.source,
            "entity_type": "new_entity",
            "external_id": self._extract_id(url),
            "scraped_at": datetime.utcnow().isoformat() + "Z",
            "url": url,
            "data": data
        }
```

2. **Add validation schema in `validators.py`:**

```python
VALIDATION_SCHEMAS = {
    "new_entity": {
        "type": "object",
        "properties": {
            "field1": {"type": "string"},
            "field2": {"type": "number"},
        },
        "required": ["field1"]
    }
}
```

3. **Update scraper.py to use new parser:**

```python
elif entity_type == "new_entity":
    parser = NewEntityParser(driver, self.source)
    scraped_data = parser.parse(url)
```

### Add a New Data Source

1. **Create new config file** (e.g., `config_source2.yaml`)
2. **Update source name:**
   ```yaml
   source:
     name: "source2"
   ```
3. **Adjust parser selectors** in `parsers.py` if needed
4. **Run with new config:**
   ```bash
   python scraper.py config_source2.yaml
   ```

## 📊 Monitoring and Logs

### Log Files

Logs are written to `logs/scraper_YYYYMMDD_HHMMSS.log`:

- Start/end times
- Success/failure for each URL
- Validation errors
- Retry attempts
- Final statistics

### Real-time Monitoring

The scraper logs progress every 10 records:

```
Progress: 50 processed, 45 success, 3 failed, 2 skipped
```

### Final Statistics

At completion:

```
SCRAPING COMPLETED
Total processed:  100
Successful:       92
Failed:           5
Skipped:          3
Duration:         450.23 seconds
Success rate:     92.00%
Output records:   92
```

## 🐛 Troubleshooting

### Issue: Selenium driver not found

**Solution:**
```bash
# Chrome
sudo apt-get install chromium-chromedriver
# or
brew install --cask chromedriver

# Firefox
sudo apt-get install firefox-geckodriver
# or
brew install geckodriver
```

### Issue: Authentication fails

**Solution:**
1. Export fresh cookies from browser
2. Ensure cookies.json format is correct
3. Check base_url matches cookie domain
4. Try disabling authentication temporarily:
   ```yaml
   authentication:
     enabled: false
   ```

### Issue: Timeouts

**Solution:**
Increase timeouts in config:
```yaml
selenium:
  page_load_timeout: 60
  implicit_wait: 20
scraping:
  delay_between_requests: 5
```

### Issue: High failure rate

**Solution:**
1. Run with `parallel_workers: 1` to debug
2. Set `headless: false` to see browser
3. Check logs for specific errors
4. Increase retry_backoff:
   ```yaml
   scraping:
     retry_backoff: 10
   ```

## 🚫 Anti-Patterns (What NOT to Do)

❌ **Don't write to production database** - Output JSON only  
❌ **Don't perform joins** - Keep entities independent  
❌ **Don't deduplicate at scrape time** - Let ETL handle it  
❌ **Don't hardcode paths** - Use config  
❌ **Don't guess data** - Log and mark as missing  
❌ **Don't silence errors** - Fail loudly  

## ✅ Best Practices

✅ **Test with small batches first** - Use 10-20 records  
✅ **Monitor logs in real-time** - `tail -f logs/scraper_*.log`  
✅ **Save progress frequently** - Happens automatically  
✅ **Use headless mode in production** - Faster and more stable  
✅ **Validate output** - Check for validation_errors field  
✅ **Keep cookies fresh** - Re-export if authentication fails  

## 📝 Example Workflow

1. **Prepare input:**
   ```bash
   # Create CSV with URLs
   echo "url,entity_type" > input.csv
   echo "https://example.com/airport/KJFK,airport" >> input.csv
   ```

2. **Test with small batch:**
   ```bash
   # Edit config.yaml - set parallel_workers: 1
   # Take first 10 lines of input
   head -11 input.csv > test_input.csv
   ```

3. **Run test:**
   ```bash
   python scraper.py
   ```

4. **Check output:**
   ```bash
   # View results
   cat output/scraped_records_*.jsonl | jq .
   
   # Count records
   wc -l output/scraped_records_*.jsonl
   ```

5. **Run full batch:**
   ```bash
   # Update config for full input and parallel execution
   # parallel_workers: 4
   python scraper.py
   ```

6. **Load into database (separate ETL process):**
   ```sql
   COPY raw_scraped_records(source, entity_type, external_id, scraped_at, data)
   FROM '/path/to/scraped_records.jsonl';
   ```

## 🤝 Integration with ETL Pipeline

The output JSONL files are designed to be directly loaded into `raw_scraped_records` table:

```sql
CREATE TABLE raw_scraped_records (
    id SERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    entity_type VARCHAR(50) NOT NULL,
    external_id VARCHAR(255) NOT NULL,
    scraped_at TIMESTAMP NOT NULL,
    url TEXT,
    data JSONB NOT NULL,
    validation_errors JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Load data
COPY raw_scraped_records(source, entity_type, external_id, scraped_at, url, data, validation_errors)
FROM '/path/to/scraped_records.jsonl' 
FORMAT CSV 
DELIMITER E'\t' 
QUOTE E'\b';
```

Then your ETL can:
1. Read from `raw_scraped_records`
2. Deduplicate based on business logic
3. Normalize and transform
4. Write to production tables

## 📞 Support

For issues or questions:
1. Check logs in `logs/` directory
2. Review failed records in `output/failed_records_*.jsonl`
3. Check progress in `progress/scraping_progress.json`
4. Run with `parallel_workers: 1` and `headless: false` to debug

## 📜 License

This is an internal tool for aviation data ingestion.
