"""
Raw ingest service: inserts verbatim scraped JSON into raw tables.
"""
import json
from etl.models.raw_sources import RAW_SCRAPED_INSERT
from etl.db import get_db_cursor

def insert_raw(record):
    entity_type = record.get('entity_type')
    url = record.get('url', '')
    # Derive a fallback external_id from entity_type + URL slug when missing
    external_id = record.get('external_id') or (
        f"{entity_type}_{url.rstrip('/').split('/')[-1]}" if url else None
    )
    data = record.get('data', {})
    scrape_status = record.get('scrape_status', 'SUCCESS')
    observed_fields = record.get('observed_fields', [])
    missing_fields = record.get('missing_fields', [])
    validation_errors = record.get('validation_errors', [])
    errors = json.dumps(record.get('errors', []))
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            RAW_SCRAPED_INSERT,
            [
                entity_type, external_id, url, scrape_status, json.dumps(data),
                observed_fields, missing_fields, validation_errors, errors
            ]
        )
        return cur.fetchone()['id']
