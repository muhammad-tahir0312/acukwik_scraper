"""
Raw ingest service: inserts verbatim scraped JSON into raw tables.
"""
import json
from etl.models.raw_sources import RAW_SCRAPED_INSERT
from etl.db import get_db_cursor

def insert_raw(record):
    source = record.get('source')
    entity_type = record.get('entity_type')
    external_id = record.get('external_id')
    scraped_at = record.get('scraped_at')
    url = record.get('url')
    data = record.get('data', {})
    scrape_status = record.get('scrape_status', 'SUCCESS')
    scrape_duration_ms = record.get('scrape_duration_ms')
    observed_fields = record.get('observed_fields', [])
    missing_fields = record.get('missing_fields', [])
    validation_errors = record.get('validation_errors', [])
    errors = json.dumps(record.get('errors', []))
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            RAW_SCRAPED_INSERT,
            [
                source, entity_type, external_id, scraped_at, url, scrape_status, json.dumps(data),
                scrape_duration_ms, observed_fields, missing_fields, validation_errors, errors
            ]
        )
        return cur.fetchone()['id']
