# AC-U-KWIK Scraper: Airport-Scoped Organization Roles

## Purpose of this report

This document is a technical handoff for another engineer or AI working on the AC-U-KWIK scraper and ETL repository. It explains the original problem, what was discovered, what was implemented, what was tested, and what remains blocked by external website access.

Repository branch: `dev-3`  
Implementation commit: `c794000`  
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
```

Apply it with:

```bash
psql "$DATABASE_URL" \
  -f aero-data-etl-final/db/migrations/002_airport_scoped_organization_roles.sql
```

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
6 passed
```

Command:

```bash
PYTHONPATH=selenium_ingestion_final \
  .venv/bin/python -m pytest -q selenium_ingestion_final/tests
```

Python compilation also completed successfully for the scraper and ETL packages.

## Requested 20-airport live validation

A live run was attempted against 20 airports from multiple regions, including airports used to investigate multi-role behavior.

Result:

```text
Airports attempted: 20
Successful live airport fetches: 0
HTTP failures: 20
HTTP status: 403 Forbidden
```

The existing cookie file had a recent filesystem timestamp, but its actual authenticated `.DOTNETNUKE` cookie had expired. An automatic browser refresh was then attempted. AC-U-KWIK returned an interactive Cloudflare `Just a moment...` challenge in headless Chrome, so the login page could not be reached.

This is an external access blocker, not a passing live validation. Do not report the scraper as successfully tested against 20 current live airport pages.

To complete the live validation:

1. Open AC-U-KWIK in a normal authorized browser.
2. Complete any Cloudflare challenge normally.
3. Sign in with the authorized account.
4. Export a fresh `cookies.json` in Selenium-compatible format.
5. Place it at the configured local cookie path. It will remain ignored by Git.
6. Create a 20-airport CSV or use the existing temporary sample if still available.
7. Run the scraper with a fresh progress file.
8. Inspect JSONL for failures, validation errors, role coverage, empty listings, and raw-field coverage.
9. Fix any parser issues found and rerun until all 20 airports complete successfully.

Example run:

```bash
INPUT_CSV_PATH=/absolute/path/live-20.csv \
OUTPUT_DIRECTORY=/absolute/path/live-20-output \
PROGRESS_FILE=/absolute/path/live-20-progress.json \
PARALLEL_WORKERS=4 \
.venv/bin/python selenium_ingestion_final/scraper.py \
  selenium_ingestion_final/config.yaml
```

## Git state

The previous two local commits were removed and replaced with one clean commit.

The repository's `origin` uses the configured personal SSH alias:

```text
git@github-personal:muhammad-tahir0312/acukwik_scraper.git
```

Personal SSH authentication was verified as GitHub user `muhammad-tahir0312`.

The implementation was successfully pushed:

```text
branch: dev-3
commit: c794000
```

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
aero-data-etl-final/db/etl_schema.sql
aero-data-etl-final/db/migrations/002_airport_scoped_organization_roles.sql
aero-data-etl-final/etl/services/entity_etl.py
```

## Recommended next actions

1. Obtain a fresh authorized browser cookie export and finish the 20-airport live run.
2. Apply the database migration in a test PostgreSQL database.
3. Run a scraper JSONL file through the ETL and query all three scoped tables.
4. Confirm a known multi-role company produces multiple airport-scoped roles without global-role ambiguity.
5. Confirm two unrelated organizations with the same normalized name remain separate.
6. Review source panels for any service categories not represented in the 14-role catalog.
7. Add live HTML fixtures, with sensitive data removed, for every layout variation discovered during the live run.

## Acceptance criteria for final production readiness

The work should only be considered fully production-validated when all of the following are true:

- A fresh 20-airport live run completes without HTTP or authentication failures.
- Every airport produces a valid primary airport entity.
- Organization records contain airport scope, listing identity, and at least one role.
- No validation errors remain unexplained.
- All detected source labels are present in raw retained fields.
- The migration succeeds on a test copy of the existing database.
- ETL reruns are idempotent.
- Same-airport multi-role organizations create multiple role rows without duplicate listings.
- Different-airport roles remain distinct.
- Same-name unrelated organizations are not merged.
