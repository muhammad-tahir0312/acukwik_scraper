# AC-U-KWIK scraper and ETL

The maintained pipeline is split into:

- `selenium_ingestion_final/`: authenticated AC-U-KWIK scraper, parsers, validation, and JSONL output.
- `aero-data-etl-final/`: PostgreSQL schema and ETL.

The older Scrapy and `*_refactored` directories are retained for history; they are not the current pipeline.

## Organization and role model

An organization does not have one global role. The source of truth is:

```text
organization -> airport listing -> one or more roles
```

For example, the same canonical company may be a handler at one airport and a caterer at another. It may also perform multiple roles at the same airport. The scraper therefore emits the airport and role on every organization occurrence. The ETL stores those facts in:

- `organization_airport_listings`: lossless listing data for one airport.
- `organization_airport_listing_roles`: roles on the exact source listing.
- `organization_airport_roles`: convenient canonical organization + airport + role relationship.

`organizations.roles` and `organization_role_map` remain aggregate compatibility fields and must not be used to answer airport-specific questions.

The 14 currently recognized AC-U-KWIK service types are:

`FBO`, `HANDLER`, `SUPERVISING_AGENT`, `FUEL_SUPPLIER`, `FLIGHT_SUPPORT_ORGANIZATION`, `CATERING`, `GROUND_TRANSPORTATION`, `MAINTENANCE`, `HOTEL`, `CAR_RENTAL`, `CHARTER`, `DETAILER`, `PROTECTION`, and `STORE`.

Unknown future service panels are retained as `OTHER` with the original section name.

## Field coverage

Known fields are normalized for normal use, but the scraper also keeps a lossless source snapshot:

- `raw_fields`: every detected label/value pair, including duplicate and unknown labels.
- `attributes`: normalized keys for all detected labels.
- `links`: link text, URL, and source identifiers.
- `media`: image URL and alt text.
- `raw_text`: the complete visible listing text.
- `source_identifiers`, `source_profile_url`, `source_section`, and `source_listing_key`.

Airport pages similarly retain all label/value pairs in `airport_fields_raw`. This prevents newly introduced AC-U-KWIK fields from being silently discarded before a normalized database column exists.

## Setup

Python 3.9 or newer is supported.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r selenium_ingestion_final/requirements.txt
```

Authentication uses an exported `cookies.json`. Credentials are never stored in configuration. If cookie refresh is needed, supply them through the environment:

```bash
export AUTH_EMAIL='your-email'
export AUTH_PASSWORD='your-password'
```

AC-U-KWIK may present an interactive Cloudflare challenge. The scraper does not bypass it. If that happens, refresh/export cookies through a normal authorized browser session and rerun.

## Run the scraper

The default input is `country_links.csv`:

```bash
.venv/bin/python selenium_ingestion_final/scraper.py selenium_ingestion_final/config.yaml
```

Paths are resolved relative to the config file. Useful environment overrides are:

```bash
INPUT_CSV_PATH=/absolute/path/airports.csv \
OUTPUT_DIRECTORY=/absolute/path/output \
PROGRESS_FILE=/absolute/path/progress.json \
PARALLEL_WORKERS=4 \
.venv/bin/python selenium_ingestion_final/scraper.py selenium_ingestion_final/config.yaml
```

The input CSV must contain `Airport Link`, `url`, or `link`; an `ICAO` column is recommended.

## Tests

```bash
PYTHONPATH=selenium_ingestion_final .venv/bin/python -m pytest -q selenium_ingestion_final/tests
```

The suite checks all service categories, unknown-field retention, same-airport multi-role behavior, and the airport-scoped database contract.

## Database migration and ETL

Apply the base schema for a new database. For an existing database, apply:

```bash
psql "$DATABASE_URL" -f aero-data-etl-final/db/migrations/002_airport_scoped_organization_roles.sql
```

Then place scraper JSONL files in `aero-data-etl-final/acukwik_data/` and run:

```bash
cd aero-data-etl-final
./run_etl.sh
```

Canonical organization matching is conservative: stable source profiles and strong shared contacts are preferred, and name-only merging is prohibited.
