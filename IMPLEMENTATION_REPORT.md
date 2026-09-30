# AC-U-KWIK Scraper: Airport-Scoped Organization Roles

## Purpose of this report

This document is a technical handoff for another engineer or AI working on the AC-U-KWIK scraper and ETL repository. It explains the original problem, what was discovered, what was implemented, what was tested, and what remains blocked by external website access.

Repository branch: `dev-3`  
Remote branch: `origin/dev-3`

## Original problem

The application treated organization roles as global properties of an organization.

That model is incorrect for AC-U-KWIK because a company provides services at a particular airport. For example:

- Company A can be a handler at Airport A.
- The same company can be a caterer at Airport B.
- A company can perform multiple roles at the same airport.
- A company may be advertised under different names or service-specific listings at the same airport.

The required relationship is therefore:

```text
organization -> airport listing -> one or more roles
```

It must not be modeled only as:

```text
organization -> roles
organization -> airports
```

The old model could tell us that an organization served two airports and had two roles, but it could not tell us which role belonged to which airport. A cross-product interpretation could incorrectly claim that every role applied at every associated airport.

## Problems found in the previous implementation

### 1. Roles were stored globally

The old ETL populated:

- `organizations.roles`
- `organization_role_map`
- `organization_airports`

There was no authoritative organization + airport + role relationship.

### 2. Organization deduplication was unsafe

The schema had a unique normalized-name index, and the ETL could fall back to name-only matching. This could merge unrelated businesses that happened to share a name.

The old transformation script also grouped records by exact organization name and combined airports without reliably preserving the airport-to-role mapping.

### 3. Service categories were missing

The previous parser recognized only ten AC-U-KWIK service sections. It omitted at least:

- Charter
- Detailers
- Protection
- Stores

### 4. Unknown website fields were silently discarded

The parser only understood a hard-coded set of labels. If AC-U-KWIK added a field, or a listing used a category-specific field, that information could be lost before reaching the ETL.

### 5. Runtime and operational defects

Additional issues found during implementation included:

- Cookie validity was judged mainly by file modification time instead of the authenticated session cookie's expiry.
- Expired cookies caused HTTP 403 responses.
- The direct HTTP fetch path had no suitable fallback.
- Invalid or empty airport parses could still be marked complete.
- Strict validation could leave a partially written airport batch.
- A legacy organization branch referenced an undefined driver variable.
- Some fallback external IDs used Python's randomized `hash()`, making IDs unstable between processes.
- Nearby successful output did not include an explicit external ID.
- Clearance and nearby-page fields not mapped to normalized columns were not preserved through the ETL.
- Credentials were stored in the tracked YAML configuration.
- Cookie jars were tracked by Git.
- Airport email buttons were incorrectly sent to the ground-handler email endpoint.
- Static HTML parsing waited for browser-side DOM mutations that could never occur.
- AC-U-KWIK locations without ICAO/IATA/FAA codes were rejected, even though their
  `Airport-Info` path identifier is stable.
- Nearby rows could lose unrecognized columns and code-less airport identifiers.

## AC-U-KWIK service-role taxonomy

The maintained scraper now recognizes 14 current service categories:

| AC-U-KWIK section | Canonical role |
|---|---|
| FBOs | `FBO` |
| Handlers | `HANDLER` |
| Supervising Agents | `SUPERVISING_AGENT` |
| Fuel Only | `FUEL_SUPPLIER` |
| Flight Support Organizations | `FLIGHT_SUPPORT_ORGANIZATION` |
| Caterers | `CATERING` |
| Limo | `GROUND_TRANSPORTATION` |
| Maintenance | `MAINTENANCE` |
| Hotels | `HOTEL` |
| Car Rental | `CAR_RENTAL` |
| Charter | `CHARTER` |
| Detailers | `DETAILER` |
| Protection | `PROTECTION` |
| Stores | `STORE` |

Previously unseen service panels are retained as `OTHER`, together with their original source-section names. This avoids silently dropping future categories.

The central taxonomy is in:

```text
selenium_ingestion_final/roles.py
```

Both the parser and validator use this module, preventing their allowed-role lists from drifting apart.

## Implemented scraper changes

### Airport-scoped organization occurrences

Every organization record emitted by the scraper now includes:

- `associated_airports`
- `roles`
- `source_section`
- `source_sections`
- `source_listing_key`
- `source_listing_id`
- `source_profile_url`
- `source_identifiers`
- `display_name`

The occurrence external ID includes the listing, airport, and role. This prevents one role occurrence from overwriting a different role occurrence in raw JSONL output.

The role-independent `source_listing_key` lets the ETL recognize the same source listing when it appears in more than one service section at the same airport. Each role is then attached to that listing.

### Lossless field retention

Known fields are still normalized, but the scraper also retains source data so new or category-specific fields are not discarded.

Organization listings now include:

- `raw_fields`: all detected label/value pairs, including unknown labels.
- `attributes`: normalized keys for the detected fields.
- `links`: link text, URLs, and source attributes.
- `media`: image URLs and alt text.
- `raw_text`: all visible text for the listing.
- `source_identifiers`: AC-U-KWIK data attributes used by the source page.

Airport records include `airport_fields_raw` for all detected airport label/value rows.

Clearance records retain:

- `raw_fields`
- `raw_text`
- `links`

Nearby-airport records retain page-level raw text and links. Each nearby row also keeps its complete `raw_cells` array and links, so extra future columns are not lost.

### Stable external IDs

Randomized Python `hash()` fallbacks were replaced with deterministic SHA-256-derived identifiers. The same source data now produces the same identifier across processes and machines.

### Safer scrape completion

The orchestrator now rejects an airport result when:

- the parser returns no entities;
- the primary airport entity has failed;
- the airport has no name; or
- the airport has no ICAO, IATA, or FAA identifier.

All entities in an airport batch are validated before any are written. In strict mode, validation errors no longer leave a partially written batch.

### Authentication and HTTP handling

Cookie validity now checks the actual `.DOTNETNUKE` authentication-cookie expiry rather than relying only on the cookie file's modification date.

The normal retrieval strategy is:

```text
authenticated HTTP fetch -> authenticated browser fallback for 401/403/429
```

The scraper explicitly detects an interactive Cloudflare challenge and stops with an actionable message. It does not attempt to bypass the challenge.

The automatic login helper now respects `selenium.headless`; this permits an
authorized visible login when required. The fixed remote-debugging port was
removed so parallel or previously running Chrome processes do not collide.

Protected email buttons now use the endpoint implemented by the live site:

- airport `aEmail` buttons: `GetARPTEmail`;
- supplier `sEmail` buttons: `GetSupplierEmail`;
- handler `ghEmail` buttons: `GetGHEmail`.

The cookie-created browser user agent can be supplied with `USER_AGENT`. A
Cloudflare clearance cookie may be invalid when replayed with a different user
agent.

Credentials were removed from `config.yaml`. If an authorized cookie refresh is needed, credentials must be supplied through environment variables:

```bash
export AUTH_EMAIL='...'
export AUTH_PASSWORD='...'
```

No credentials should be committed.

## Implemented database model

### `organization_airport_listings`

This table stores the source-of-truth AC-U-KWIK listing at a particular airport. It includes:

- canonical organization ID;
- airport ID;
- stable listing key;
- source IDs and source profile URL;
- source section names;
- display-name aliases;
- contacts;
- address;
- normalized attributes;
- raw fields;
- links;
- media;
- raw listing text;
- scrape metadata and errors.

The uniqueness constraint is:

```text
(airport_id, listing_key)
```

### `organization_airport_listing_roles`

This table assigns one or more roles to the exact airport listing:

```text
(listing_id, role_id)
```

### `organization_airport_roles`

This table provides a convenient canonical relationship:

```text
(organization_id, airport_id, role_id)
```

This is the appropriate table for questions such as:

> Which roles does organization X perform at airport Y?

### Legacy compatibility fields

The following remain for compatibility and aggregate reporting:

- `organizations.roles`
- `organization_role_map`
- `organization_airports`

They are not the authoritative source for airport-specific roles.

### Name uniqueness was removed

The unique normalized organization-name index was replaced with a normal lookup index. Names are searchable but are no longer treated as identities.

## Canonical organization matching

Canonical organization matching is deliberately conservative.

The ETL prefers:

1. An already known canonical external ID derived from stable source identity.
2. An overlapping website.
3. An overlapping email address.
4. An overlapping phone number combined with conservative name-alias similarity.

Name-only matching was removed.

This reduces false merges while still allowing service-specific aliases belonging to the same company to resolve to one canonical organization when strong evidence exists.

Airport-specific addresses, remarks, SITA/AFTN codes, pricing, distance, brands, hours, and source fields are stored on the listing instead of being treated as globally valid company attributes.

## ETL behavior

For each organization occurrence, the ETL now:

1. Resolves or creates a canonical organization conservatively.
2. Resolves all associated airport IDs.
3. Maintains the legacy organization-airport link.
4. Upserts the airport listing using `(airport_id, listing_key)`.
5. Adds every listing role to `organization_airport_listing_roles`.
6. Adds every canonical airport role to `organization_airport_roles`.
7. Maintains aggregate global role fields only for compatibility.

The old name-merging transformation is now a validated pass-through. Canonical merging belongs in the ETL, where contact and source evidence are available.

## Schema and migration files

The base schema was updated:

```text
aero-data-etl-final/db/etl_schema.sql
```

The migration for an existing database is:

```text
aero-data-etl-final/db/migrations/002_airport_scoped_organization_roles.sql
aero-data-etl-final/db/migrations/003_airport_source_identifiers.sql
```

Apply it with:

```bash
psql "$DATABASE_URL" \
  -f aero-data-etl-final/db/migrations/002_airport_scoped_organization_roles.sql
psql "$DATABASE_URL" \
  -f aero-data-etl-final/db/migrations/003_airport_source_identifiers.sql
```

Migration `003` adds `airports.source_airport_id`, backfills it from existing
AC-U-KWIK URLs, creates stable external IDs for old rows, and adds the unique
external-ID index required for idempotent upserts. ETL linking now accepts
either an ICAO code or this source identifier for organizations, clearances,
and nearby airports.

Historical global role data cannot be safely backfilled into airport-specific roles because the original records no longer contain that relationship. Existing organizations should be re-scraped after applying the migration.

## Git and security changes

`.gitignore` now excludes:

- virtual environments and environment files;
- cookie jars and local credentials;
- scraper output JSONL files;
- progress state;
- cached HTML;
- error artifacts and screenshots;
- logs and test caches;
- graph-analysis artifacts;
- local database backups.

Previously tracked cookie jars were removed from Git tracking, but the local files were preserved for the user. Because those files existed in earlier commits, any live credentials that may have been stored in repository history should be considered exposed and rotated if still valid.

## Tests added

Tests are located in:

```text
selenium_ingestion_final/tests/
```

The suite verifies:

- all 14 known service categories are parsed;
- unknown organization fields are retained;
- airport unknown fields are retained;
- the same source listing can carry different roles at one airport;
- role occurrences receive distinct raw external IDs;
- clearance unknown fields are retained;
- nearby extra columns are retained;
- the new schema tables exist;
- the name-only organization lookup is absent;
- SQL placeholder counts match ETL parameters.

Latest local test result:

```text
8 passed
```

Command:

```bash
PYTHONPATH=selenium_ingestion_final \
  .venv/bin/python -m pytest -q selenium_ingestion_final/tests
```

Python compilation also completed successfully for the scraper and ETL packages.

## Headless Selenium script repair (2026-09-30)

The maintained script now uses headless Selenium by default for page retrieval,
with shared login/retrieval browser settings and a complete user agent derived
from the installed Chrome. Local credentials load from ignored `.env` files;
environment variables still take precedence. Login refresh retains saved site
cookies, email requests reuse the authenticated Selenium session, and `--limit`
supports bounded runs. Authentication failures now produce a failing exit status.

The live check deliberately removed the account authentication cookie while
retaining the site's other cookies. The script automatically logged in headlessly,
saved a new account session, and retrieved 20 airports without visible windows
or manual interaction. An entirely empty cookie jar still encountered a site
challenge; this verifies automatic account-session refresh with saved site
clearance, not unrestricted access from a new session.

Output verification also caught a truncated source ID for code-less airport
`ACKTNON`. The script now retains the complete source ID for clearance and nearby
URLs. A targeted headless retry replaced that airport's three records in the
verified deliverable.

Final result: 20 airports, 47 organizations, 20 clearance records, 20 nearby
records, 107 records total, and zero schema-validation errors. All 15 tests pass.
Artifacts are in `runs/airports20_20260930_114217/headless/verified/`.

## PostgreSQL ETL verification (2026-09-30)

The 107 verified records were imported into the separate local PostgreSQL 17
database `acukwik_etl_check_20260930`. Existing application databases were not
modified. The base schema and migrations `002` and `003` executed successfully
after moving referenced location/address tables before airports/organizations.

The database check exposed a scraper identity bug: serializing empty source
identifiers produced the truthy string `{}`, so unrelated hotels shared one
listing key. The parser now falls back to the hotel name, and the ETL separates
older identifier-free records and retires their superseded collision rows on
re-import. All 47 source listings are retained across 42 canonical organizations.

The ETL now finalizes `etl_runs` with processed/failed counts and a completion
time, returns a failing exit code for record errors, and preserves the raw input
data before adding normalized metadata. The launcher accepts explicit JSONL
paths from any directory, reuses the shared environment, and loads database
settings from `.env`/environment, including `DATABASE_URL`.

Final verified database contents:

- 20 fully scraped airports plus 41 nearby-airport stubs.
- 42 canonical organizations and 47 airport listings.
- 47 listing-role rows and 47 organization-airport-role rows.
- 20 clearance records and 20 nearby-page records.
- 20 airport-clearance links and 139 nearby-airport links.
- Zero orphaned organizations, failed input records, or SQL error logs.

Every source listing's contacts, raw fields, attributes, links, media, and roles
were compared with the database. All airport raw fields, clearance raw fields,
nearby rows, and the latest 107 raw input payloads were also checked. Re-importing
left normalized entity IDs and relationship rows unchanged; audit records append
by design. Results are in `runs/etl_check_20260930/validation_summary.json`.

All 18 tests pass: 16 scraper/schema tests and 2 real-PostgreSQL tests. The latter
exercise schema/migration installation, hotel collisions, airport-specific and
multi-role mappings, repeat imports, raw preservation, and failure exit codes in
disposable schemas. Production-database migration remains outside this check.

## Earlier 20-airport live validation

The normal scraper entry point was run first against 20 airports. All 20 direct
HTTP requests received `403 Forbidden`, and WebDriver was presented with
Cloudflare's interactive challenge. Replaying the fresh cookie jar and matching
the browser user agent did not make automated requests pass the challenge.

To validate the real pages without bypassing the challenge, the same 60 pages
(airport, clearance, and nearby page for each airport) were fetched through an
authorized, logged-in normal browser. All 60 returned HTTP 200. Those exact live
HTML documents were then processed with the production `HtmlDriver` and parser
classes. The 34 protected email requests found in the pages were also invoked
through that authorized browser and passed to the production email-resolution
path.

Result:

```text
Live pages fetched:                 60/60 (HTTP 200)
Airports parsed:                    20
Organizations parsed:              47
Clearance records parsed:           20
Nearby records parsed:              20
Total output records:               107
Validation errors:                  0
Airport emails resolved:            12/12 present buttons
Organization emails resolved:       22/22 present buttons
Nearby rows parsed:                 139
Nearby rows retaining raw cells:    139/139
Code-less airports retained:        1
```

The live data also directly confirmed the original relationship problem:

```text
MIXJET FLIGHT SUPPORT @ HCMI -> FLIGHT_SUPPORT_ORGANIZATION
MIXJET FLIGHT SUPPORT @ HCMM -> FUEL_SUPPLIER
```

This validates current live field layouts and the parsing/validation path. It is
not a claim that unattended Selenium can defeat the active Cloudflare challenge;
the scraper intentionally does not attempt to do that. Nearby-page pagination
still depends on interactive Selenium during a normal end-to-end run. The
browser-assisted fixture captured the initially rendered nearby page for each
airport.

## Git state

The previous two local commits were removed and replaced with one clean commit.

The repository's `origin` uses the configured personal SSH alias:

```text
git@github-personal:muhammad-tahir0312/acukwik_scraper.git
```

Personal SSH authentication was verified as GitHub user `muhammad-tahir0312`.

The airport-scoped implementation and its technical report were pushed to
`dev-3`. The follow-up live-validation fixes described here are intended for the
same branch.

## Important files for the next AI

Start with these files:

```text
README.md
selenium_ingestion_final/roles.py
selenium_ingestion_final/parsers.py
selenium_ingestion_final/scraper.py
selenium_ingestion_final/validators.py
selenium_ingestion_final/config_loader.py
selenium_ingestion_final/tests/test_airport_parser.py
selenium_ingestion_final/tests/test_schema_contract.py
selenium_ingestion_final/tests/test_scraper_runtime.py
aero-data-etl-final/db/etl_schema.sql
aero-data-etl-final/db/migrations/002_airport_scoped_organization_roles.sql
aero-data-etl-final/db/migrations/003_airport_source_identifiers.sql
aero-data-etl-final/etl/services/entity_etl.py
```

## Recommended next actions

1. Apply migrations `002` and `003` in a test PostgreSQL database.
2. Run the validated JSONL through the ETL and query all three scoped role tables.
3. Confirm two unrelated organizations with the same normalized name remain separate.
4. Review source panels periodically for categories not represented in the 14-role catalog.
5. Add sanitized live HTML fixtures for every new layout variation.
6. Re-run unattended live retrieval when AC-U-KWIK permits the authorized browser
   session to pass its automated challenge.

## Acceptance criteria for final production readiness

The work should only be considered fully production-validated when all of the following are true:

- A fresh unattended 20-airport retrieval completes without HTTP or authentication failures.
- Every airport produces a valid primary airport entity.
- Organization records contain airport scope, listing identity, and at least one role.
- No validation errors remain unexplained.
- All detected source labels are present in raw retained fields.
- The migration succeeds on a test copy of the existing database.
- ETL reruns are idempotent.
- Same-airport multi-role organizations create multiple role rows without duplicate listings.
- Different-airport roles remain distinct.
- Same-name unrelated organizations are not merged.

## Follow-up: source role reconciliation

The Aviation Index integration audit found that an ETL rerun added roles but did
not remove a role when a listing stopped appearing in that service section.
The ETL now collects the role union for each listing across a successful file
and replaces that listing's old roles after the file completes. It rebuilds the
flattened organization/airport role rows from all listings, including editor
listings, so manual roles remain intact. This reconciliation runs only when the
file includes a successfully processed airport record for that airport and has
no failed records. Organization-only imports remain additive because they do
not prove the full service list. A source listing absent from a complete airport
snapshot is removed, while manual editor listings remain intact.

The PostgreSQL integration test covers multi-role imports, removal on a later
complete snapshot, removal of vanished source listings, preservation of manual
roles, and additive partial imports.
