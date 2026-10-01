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

The scraper logs in automatically and saves `cookies.json`. Put credentials in
the ignored project-root `.env` file (see `.env.example`), or supply them through
the environment. Environment variables take precedence:

```bash
export AUTH_EMAIL='your-email'
export AUTH_PASSWORD='your-password'
```

Login and retrieval both use headless Selenium with the same complete browser
user agent. During account re-login, existing site cookies are retained. An
entirely new session without valid Cloudflare clearance may still be challenged;
the script reports that failure and exits unsuccessfully instead of claiming a
successful scrape.

## Run the scraper

For a complete scrape (cookie refreshes, airport-list regeneration, discovery
passes, ETL), follow [docs/FULL_SCRAPE_RUNBOOK.md](docs/FULL_SCRAPE_RUNBOOK.md).

The input is the list in `config.yaml` (`country_links_20261001.csv`, regenerated
with `scripts/generate_airport_list.py`). Refresh `cookies.json` first:

```bash
.venv/bin/python scripts/refresh_cookies.py
.venv/bin/python selenium_ingestion_final/scraper.py selenium_ingestion_final/config.yaml
```

To process at most 20 input airports:

```bash
.venv/bin/python selenium_ingestion_final/scraper.py --limit 20
```

The default `fetch_mode: http` downloads the airport, clearance, every nearby
page, and each organization's Basic-Info profile with the saved cookies, then
parses the saved HTML locally. `FETCH_MODE=selenium` (or `auto`) drives a
headless browser instead, at roughly a fifth of the speed. The cookies stop
working after about 30 minutes; requests then fail with HTTP 403 until
`scripts/refresh_cookies.py` is run again. A rerun with the same progress file
resumes where it stopped.

Paths are resolved relative to the config file. Useful environment overrides are:

```bash
INPUT_CSV_PATH=/absolute/path/airports.csv \
OUTPUT_DIRECTORY=/absolute/path/output \
PROGRESS_FILE=/absolute/path/progress.json \
PARALLEL_WORKERS=22 \
.venv/bin/python selenium_ingestion_final/scraper.py selenium_ingestion_final/config.yaml
```

HTTP requests use the Chrome 154 user agent that `scripts/refresh_cookies.py`
logs in with; set `USER_AGENT` only together with the browser that created the
cookies.

The input CSV must contain `Airport Link`, `url`, or `link`; an `ICAO` column is recommended.

## Tests

```bash
PYTHONPATH=selenium_ingestion_final .venv/bin/python -m pytest -q selenium_ingestion_final/tests
```

The suite checks all service categories, unknown-field retention, same-airport multi-role behavior, and the airport-scoped database contract.

## Database migration and ETL

Configure `DATABASE_URL`, or `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and
`DB_PASSWORD` in the ignored root/ETL `.env` file or the environment. Exported
variables take precedence. `DATABASE_URL`, when set, takes precedence over the
individual connection fields. Choose an existing PostgreSQL database and role.

For a new database, apply the base schema (the following `psql` commands use an
exported `DATABASE_URL`):

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -1 -f aero-data-etl-final/db/etl_schema.sql
```

For an existing database, apply:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f aero-data-etl-final/db/migrations/002_airport_scoped_organization_roles.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f aero-data-etl-final/db/migrations/003_airport_source_identifiers.sql
```

Pass scraper output directly to the launcher from the repository root:

```bash
./aero-data-etl-final/run_etl.sh /absolute/path/to/scraped_records.jsonl
```

Without file arguments, it reads `aero-data-etl-final/acukwik_data/*.jsonl`.
The launcher uses the shared `.venv` and accepts `--data-dir` for another input
directory. Completed imports record their counts and status in `etl_runs`;
failed records are logged in `app_logs` and cause a nonzero exit code. Normalized
entities and relationships are updated on reruns; raw audit records append.

Canonical organization matching is conservative: stable source profiles and strong shared contacts are preferred, and name-only merging is prohibited. Identifier-free listings are distinguished by airport and name, including older hotel records with colliding source keys.

PostgreSQL regression checks use disposable schemas inside a test database:

```bash
ETL_TEST_DATABASE_URL="$DATABASE_URL" PYTHONPATH=aero-data-etl-final \
  .venv/bin/python -m pytest -q aero-data-etl-final/tests
```
