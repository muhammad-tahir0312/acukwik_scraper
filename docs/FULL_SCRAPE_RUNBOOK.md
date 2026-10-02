# AC-U-KWIK full scrape runbook

How to run a complete AC-U-KWIK scrape (every airport, every listing, every page)
and load it into PostgreSQL. Written after the 2026-10-01 run, which collected
25,149 airports and 47,536 organization listings in about 4 hours of scraping.

## Summary

1. Refresh `cookies.json` with `scripts/refresh_cookies.py`.
2. Regenerate the airport list with `scripts/generate_airport_list.py` and check its report.
3. Run the scraper in HTTP mode into a new `runs/full_YYYYMMDD/` directory.
4. Every ~30 minutes the cookies stop working (HTTP 403). Stop the scraper,
   refresh the cookies, and start it again with the same output and progress paths;
   it resumes where it stopped.
5. Run `scripts/discover_missing_airports.py` and scrape what it finds, until it finds nothing.
6. Load the output into a fresh database with `aero-data-etl-final/run_etl.sh`, verify it, and swap it in.

## How it works

The scraper (`selenium_ingestion_final/scraper.py`) downloads each airport's
pages over plain HTTP with the logged-in session cookies, saves them under
`html_cache/`, and parses the saved HTML locally (`html_driver.py`,
`parsers.py`). `fetch_mode: http` in `config.yaml` is the default. Selenium mode
still exists (`FETCH_MODE=selenium`) but is about 5x slower.

For each airport it fetches:

| Page | What it gives |
|---|---|
| `/Airport-Info/{ID}` | Airport fields, runways, restrictions/contacts table, customs panel, diagrams, and every organization listing (FBO, handler, fuel, caterer, hotel, car rental, charter, ...) |
| `/Clearance-Overview/{ID}` | Permits, visas, clearance contacts |
| `/Nearby/{ID}` + pages 2..N | Every nearby airport (pages are ASP.NET form postbacks, like clicking ">"), with coordinates from the page-1 map markers |
| `/Basic-Info/{ID}/{LISTING}` | Each organization's profile: hours, fuel types, accepted fuel/credit cards (`scrape_basic_info: true`) |

Organization emails behind "Show Email" come from the site's email API, and
Cloudflare-protected email addresses in page HTML are decoded the way the browser decodes them.

An airport is only marked completed in `progress.json` when all of its pages
succeeded. If any page fails, the airport is retried on the next run.

## Prerequisites

- `.venv` with `selenium_ingestion_final/requirements.txt` installed (see `README.md`).
- `.env` in the repository root with `AUTH_EMAIL` and `AUTH_PASSWORD` (never commit it).
- Chrome for Testing is downloaded automatically by Selenium the first time.
- Keep the Mac awake (System Settings, or `caffeinate`). Locking the screen is fine.
  Logging out, restarting or sleeping is not.

## Step 1: Refresh cookies

```bash
.venv/bin/python scripts/refresh_cookies.py
```

A Chrome window opens on acukwik.com. The script logs in through the header
Login popup with the `.env` credentials and saves `cookies.json`. If Cloudflare
shows "Verify you are human", click it; the script waits up to 6 hours.
Usually no click is needed.

Things learned the hard way:

- **The cookies last about 30 minutes.** The `__cf_bm` cookie expires 30 minutes
  after the login. After that every request gets HTTP 403 until you refresh.
  Check the deadline with:
  ```bash
  .venv/bin/python -c "import json,datetime;print([datetime.datetime.fromtimestamp(c['expiry']) for c in json.load(open('cookies.json')) if c['name']=='__cf_bm'])"
  ```
- **Don't copy cookies back from the scraper's sessions.** Saving rotated
  Cloudflare cookies from scraping sessions into `cookies.json` made Cloudflare
  challenge every request. Only the refresh script should write `cookies.json`.
- **The `/Login` page's button does not work in an automated browser.** The
  header popup does, which is what the script uses.
- **The user agent must match.** Requests use the same Chrome 154 user agent as the
  refresh window (the default in `scraper.py`). Override with `USER_AGENT` only together with the browser that created the cookies.

## Step 2: Regenerate the airport list

```bash
.venv/bin/python scripts/generate_airport_list.py \
  --output country_links_$(date +%Y%m%d).csv \
  --report runs/airport_list_$(date +%Y%m%d)/report.json \
  --debug-dir runs/airport_list_$(date +%Y%m%d)/debug
```

It reads every country link on `/Power-Search-Airports` (249 in 2026-10),
fetches each country page, and writes a new CSV plus a diff report against the
newest existing list (added / removed / renamed). It needs fresh cookies and takes about 5 minutes.

Expect the run to exit with "Validation checks FAILED" for a handful of
countries. That is a site limitation, not a script bug:

- Countries and regions with `/` in the name (Canary Islands, Azores, Madeira,
  Easter Island) return an empty country page. The script falls back to
  the search form, which returns them.
- Hyphenated names (Guinea-Bissau, Timor-Leste) return nothing either way.
  Their airports are found in step 5.
- Some regions are genuinely empty because their airports are filed under the parent
  country (Okinawa → Japan, Åland → Finland, Christmas Island → Australia).

Before switching to the new list:

- Spot-check a few "removed" airports. They should return 404 on the site.
- Check that the USA count roughly matches the site's "results found".

Then point `input.csv_paths` in `selenium_ingestion_final/config.yaml` at the new file.

## Step 3: Run the scraper

```bash
R=runs/full_$(date +%Y%m%d)
mkdir -p $R/output
OUTPUT_DIRECTORY=$PWD/$R/output PROGRESS_FILE=$PWD/$R/progress.json PARALLEL_WORKERS=22 \
  .venv/bin/python selenium_ingestion_final/scraper.py selenium_ingestion_final/config.yaml \
  > $R/run_$(date +%m%d_%H%M).log 2>&1 &
```

Before a long run, test with `--limit 2` (or `INPUT_CSV_PATH=` a small CSV) into a
separate directory and check that it scrapes 2/2 with no 403s.

**Workers.** Measured on 2026-10-01 (Mac mini, 16 GB):

| Workers | Airports/min |
|---|---|
| 8 | ~72 |
| 12 | ~94 |
| 16 | ~112 |
| 22 | ~120 |
| 30 | ~120 |

Above about 20 workers the site's response time is the limit. HTTP workers use
little memory. Selenium mode is different: 12 browsers were slower than 8, and
18 got challenged immediately.

**Monitoring.** Grep for `"403 Client Error"`, not `403`, because log timestamps
contain `403`.

```bash
L=$(ls -t $R/run_*.log | head -1)
grep -c "Successfully scraped" $L; grep -c "403 Client Error" $L
.venv/bin/python -c "import json;d=json.load(open('$R/progress.json'));print(len(d['completed_ids']),'done',len(d['failed_ids']),'failed')"
```

## Step 4: Every ~30 minutes

When `403 Client Error` lines start climbing:

```bash
pkill -INT -f "scraper.py selenium_ingestion_final/config.yaml"   # Ctrl-C; progress is saved
.venv/bin/python scripts/refresh_cookies.py
# then start step 3 again with the SAME OUTPUT_DIRECTORY and PROGRESS_FILE
```

The scraper skips completed airports and retries failed ones. Airports that
failed during the block are retried automatically. Output files are written
per record (`batch_size: 1`), so stopping loses nothing. A few airports may be
written twice across resumes; the ETL upserts, so that is harmless.

The 2026-10-01 run needed 7 refreshes.

## Step 5: Discovery passes

The country pages miss some airports. Every scraped nearby list names its
neighbours, so this finds airports referenced there but not yet scraped:

```bash
.venv/bin/python scripts/discover_missing_airports.py --output-dir $R/output \
  --input country_links_YYYYMMDD.csv --write $R/discovered_pass1.csv
INPUT_CSV_PATH=$PWD/$R/discovered_pass1.csv OUTPUT_DIRECTORY=$PWD/$R/output \
  PROGRESS_FILE=$PWD/$R/progress.json PARALLEL_WORKERS=5 \
  .venv/bin/python selenium_ingestion_final/scraper.py selenium_ingestion_final/config.yaml
# repeat with --input for every earlier discovered_passN.csv until "new_airports": 0
```

On 2026-10-01 this found 5 airports in pass 1 and 3 in pass 2 (all Guinea-Bissau
and Timor-Leste), then 0.

## Step 6: Check the output, then load it

Output is large (about 9 GB of JSONL). Use streaming checks, never load it all
into memory. The 2026-10-01 result:

| Record type | Count |
|---|---|
| airport | 25,149 (62 `PARTIAL` = code-less airports with no ICAO/IATA/FAA; expected) |
| clearance | 25,149 |
| nearby_airports | 25,149 (4.78 M rows; 19,206 multi-page; 0 row-count mismatches vs map markers) |
| organization | 47,536 (20,690 with a Basic-Info profile; 0 with errors) |

Then load it into a **fresh database** and swap it in once verified. Loading on
top of an existing database leaves stale rows from earlier loads (old listings,
links to since-merged airports), so build a new one with the same structure:

```bash
createdb av-new-build
pg_dump --schema-only --no-owner av-new | psql -q -d av-new-build
psql -d av-new-build -f aero-data-etl-final/db/migrations/005_airport_source_id_index.sql
DATABASE_URL=postgresql://localhost/av-new-build ./aero-data-etl-final/run_etl.sh \
  $R/output/scraped_records_*.jsonl > $R/etl/etl_build.log 2>&1
```

The ETL streams the files. The 2026-10-02 load of all 13 output files
(123,864 records) took 59 minutes, about 430 airports/min, with 0 failures.
(Before the ETL fixes below it slowed to under 10 airports/min.)

Verify before swapping:

| Check | 2026-10-02 result |
|---|---|
| `etl_runs` | 13 × `SUCCESS`, 123,864 processed, 0 failed; `app_logs` empty |
| airports | 25,149, all scraped (0 stubs left after the CYMX merge below) |
| clearances / `airport_clearances` | 25,149 / 25,149 |
| `airport_nearby_airports` | 4,782,482 = nearby rows in the scrape |
| organizations / listings / listing roles | 24,330 / 47,456 / 47,535 |
| listings on stub airports, organizations without an airport | 0 / 0 |
| spot checks | EGLL 60 listings + 79 nearby; KTEB 45 listings |

Listing roles are one fewer than organization records because AC-U-KWIK lists
"Hotel Granduca" at LIRS twice ("Via Sanese"/"Via Senese" 170); the ETL merges
hotels by airport + name.

Then swap (no connections may be open to either database), keeping the old one
until the new one is confirmed in the app:

```bash
psql -d postgres -c 'ALTER DATABASE "av-new" RENAME TO "av-new-old-YYYYMMDD"' \
                 -c 'ALTER DATABASE "av-new-build" RENAME TO "av-new"'
```

### ETL fixes made on 2026-10-02 (do not undo)

- **Backfill once per file.** `backfill_association_links()` scans whole tables;
  it used to run every 100 records, which made large imports quadratic. Its
  joins use one code→id set instead of `icao = x OR source_airport_id = x`.
- **Index `airports.source_airport_id`** (migration `005`).
- **Deterministic airport lookup** (`find_airport_id`). A code can match the
  scraped airport and a stub created from another airport's nearby list; the
  scraped row wins. Before, about 12% of organizations landed on stubs.
- **No duplicate stub airports.** An airport upsert adopts a matching stub
  (same source ID, or a stub whose ICAO equals the source ID), and nearby rows
  reuse an existing airport. FAA-only airports used to be stored twice
  (`acukwik_faa_X` and `acukwik_source_X`), doubling their nearby links.
- If a stub still duplicates a scraped airport after a load (as CYMX did, before
  the ICAO rule was added), merge it by repointing `airport_clearances`,
  `airport_nearby_airports` (both columns), `organization_airports`,
  `organization_airport_roles` and `organization_airport_listings` to the real
  row, then deleting the stub.

## Site quirks handled in code (do not undo)

- **Two listings in one row.** Two-column service rows hold two listings; each
  `data-anchor-id` column is a separate organization.
- **FBO panel title.** The FBO panel is titled "FBOs Fuel Info" and maps to FBO,
  not OTHER.
- **Challenge script on normal pages.** Real pages embed Cloudflare's
  `challenge-platform` script. Only a "Just a moment" `<title>` means a challenge.
- **Listing names with `#`.** Names can contain `#` (e.g. "ATLANTA #300"). It
  must be sent as `%23` in Basic-Info URLs.
- **Code-less airports.** Their Basic-Info links have an empty airport segment
  (`/Basic-Info//NAME`); the airport's source ID is filled in.
- **Restrictions table.** Its cells are read from direct children only; nested
  "Phone" labels used to shift the Email/Website column.

## Files

| File | Purpose |
|---|---|
| `scripts/refresh_cookies.py` | Visible-Chrome login; writes `cookies.json` |
| `scripts/generate_airport_list.py` | Regenerates the airport list + diff report |
| `scripts/discover_missing_airports.py` | Finds airports referenced by nearby lists but not scraped |
| `selenium_ingestion_final/basic_info.py` | Parses organization profile pages |
| `selenium_ingestion_final/tests/` | Offline tests against saved live pages (`fixtures/`) |
| `runs/full_YYYYMMDD/` | Output, progress and logs of a run (git-ignored) |
