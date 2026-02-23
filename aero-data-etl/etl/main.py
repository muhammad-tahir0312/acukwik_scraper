"""
Main ETL entrypoint. Streams JSONL, processes each record, crash-safe, logs progress and errors.
"""
import os
import json
import traceback
from etl.config import Config
from etl.utils.json_reader import stream_jsonl
from etl.utils.batching import batch_iterable
from etl.services.raw_ingest import insert_raw
from etl.services.airport_etl import upsert_airport
from etl.services.entity_etl import upsert_entity, insert_contacts
from etl.services.etl_logger import log_progress, log_error
from etl.db import get_db_cursor

ETL_RUN_INSERT = """
INSERT INTO etl_runs (started_at, source, entity_type, status)
VALUES (NOW(), %s, %s, 'RUNNING')
RETURNING id;
"""
APP_LOG_INSERT = """
INSERT INTO app_logs (level, message, context)
VALUES ('ERROR', %s, %s);
"""

def main(input_path):
    etl_run_id = None
    count = 0
    # Try to infer source/entity_type from first record
    first_record = next(stream_jsonl(input_path))
    source = first_record.get('source') if isinstance(first_record, dict) else None
    entity_type = first_record.get('entity_type') if isinstance(first_record, dict) else None
    with get_db_cursor(commit=True) as cur:
        cur.execute(ETL_RUN_INSERT, [source, entity_type])
        etl_run_id = cur.fetchone()['id']

    # Rewind file for full processing
    def full_stream():
        yield first_record
        for r in stream_jsonl(input_path):
            yield r

    for batch in batch_iterable(full_stream(), Config.BATCH_SIZE):
        for record in batch:
            try:
                # Validate minimal identity fields
                if not isinstance(record, dict) or 'entity_type' not in record or 'data' not in record:
                    raise ValueError("Missing entity_type or data")
                entity_type = record['entity_type']
                data = record['data']
                if entity_type == 'airport':
                    # ICAO is optional; log a warning if missing, but continue
                    # icao = data.get('icao')
                    # if not icao:
                    #     log_error("Missing ICAO for airport", raw=record)
                    insert_raw(record)
                    # Merge top-level fields into data
                    for field in ["url", "scrape_status", "scrape_duration_ms", "external_id", "scraped_at", "observed_fields", "missing_fields", "errors", "source"]:
                        if field in record:
                            data[field] = record[field]
                    airport_id = upsert_airport(data)
                    # Organizations inside airport data
                    for org in data.get('organizations', []):
                        entity_id, claimed = upsert_entity(org, airport_id)
                        if not claimed:
                            insert_contacts(entity_id, org)
                elif entity_type == 'organization':
                    insert_raw(record)
                    # Merge top-level fields into data
                    for field in ["url", "scrape_status", "scrape_duration_ms", "external_id", "scraped_at", "observed_fields", "missing_fields", "errors", "source"]:
                        if field in record:
                            data[field] = record[field]
                    upsert_entity(data)
                else:
                    raise ValueError(f"Unknt('contacts', []):own entity_type: {entity_type}")
                count += 1
                if count % Config.LOG_EVERY == 0:
                    log_progress(count)
            except Exception as e:
                stack = traceback.format_exc()
                log_error(str(e), exc=stack, raw=record)
                with get_db_cursor(commit=True) as cur:
                    cur.execute(APP_LOG_INSERT, [str(e), json.dumps({'stack': stack, 'raw': record})])
    log_progress(count)

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python -m etl.main <input_jsonl>")
        exit(1)
    main(sys.argv[1])
