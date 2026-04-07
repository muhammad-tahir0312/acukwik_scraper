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
from etl.services.entity_etl import (
    upsert_entity,
    insert_contacts,
    upsert_clearance,
    upsert_nearby_airports,
    backfill_association_links,
    audit_unmapped_organizations,
)
from etl.services.etl_logger import log_progress, log_error
from etl.db import get_db_cursor

ETL_RUN_INSERT = """
INSERT INTO etl_runs (started_at, entity_type, status)
VALUES (NOW(), %s, 'RUNNING')
RETURNING id;
"""
APP_LOG_INSERT = """
INSERT INTO app_logs (level, message, context)
VALUES ('ERROR', %s, %s);
"""

def main(input_path):
    count = 0
    # Try to infer source/entity_type from first record
    record_stream = stream_jsonl(input_path)
    try:
        first_record = next(record_stream)
    except StopIteration:
        print(f"Skipping empty file: {input_path}")
        return
    entity_type = first_record.get('entity_type') if isinstance(first_record, dict) else None
    with get_db_cursor(commit=True) as cur:
        cur.execute(ETL_RUN_INSERT, [entity_type])

    # Rewind file for full processing
    def full_stream():
        yield first_record
        for r in record_stream:
            yield r

    for batch in batch_iterable(full_stream(), Config.BATCH_SIZE):
        for record in batch:
            try:
                # Keep failed/raw records even when structured data is absent.
                if isinstance(record, dict) and record.get("scrape_status") == "FAILED" and "data" not in record:
                    insert_raw(record)
                    count += 1
                    continue

                # Validate minimal identity fields
                if not isinstance(record, dict) or 'entity_type' not in record or 'data' not in record:
                    raise ValueError("Missing entity_type or data")
                entity_type = record['entity_type']
                data = record['data']
                # Merge top-level fields into data
                for field in ["url", "scrape_status", "external_id", "observed_fields", "missing_fields", "errors"]:
                    if field in record:
                        data[field] = record[field]
                if entity_type == 'airport':
                    insert_raw(record)
                    airport_id = upsert_airport(data)
                    for org in data.get('organizations', []):
                        entity_id, claimed = upsert_entity(org, airport_id)
                        if not claimed:
                            insert_contacts(entity_id, org)
                    for clearance in data.get('clearances', []):
                        upsert_clearance(clearance, airport_id)
                    for nearby in data.get('nearby_airports', []):
                        upsert_nearby_airports(nearby, airport_id)
                elif entity_type == 'organization':
                    insert_raw(record)
                    upsert_entity(data)
                elif entity_type == 'clearance':
                    insert_raw(record)
                    upsert_clearance(data)
                elif entity_type == 'nearby_airports':
                    insert_raw(record)
                    upsert_nearby_airports(data)
                else:
                    raise ValueError(f"Unknown entity_type: {entity_type}")
                count += 1
                if count % Config.LOG_EVERY == 0:
                    log_progress(count)
            except Exception as e:
                stack = traceback.format_exc()
                log_error(str(e), exc=stack )
                with get_db_cursor(commit=True) as cur:
                    cur.execute(APP_LOG_INSERT, [str(e), json.dumps({'stack': stack, 'raw': record})])
        # Repair any links that could not be established due to out-of-order records.
        backfill_association_links()
    backfill_association_links()
    unmapped_count, unmapped_sample = audit_unmapped_organizations(limit=20)
    if unmapped_count:
        log_error(
            f"Unmapped organizations after processing: {unmapped_count}",
            raw={"sample": unmapped_sample}
        )
    log_progress(count)

if __name__ == "__main__":
    # Automatically detect .jsonl files in acukwik_data folder
    data_dir = os.path.join(os.path.dirname(__file__), '../acukwik_data')
    # Sort by filename so part files run in a stable, predictable order.
    jsonl_files = [
        os.path.join(data_dir, f)
        for f in sorted(os.listdir(data_dir))
        if f.endswith('.jsonl')
    ]
    if not jsonl_files:
        print("No .jsonl files found in acukwik_data folder.")
        exit(1)
    for input_path in jsonl_files:
        print(f"Processing: {input_path}")
        main(input_path)
