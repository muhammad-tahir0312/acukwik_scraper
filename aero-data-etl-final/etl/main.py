"""Stream JSONL into PostgreSQL and record the actual outcome of each ETL run."""
import argparse
import json
from pathlib import Path
import traceback

from etl.config import Config
from etl.utils.json_reader import stream_jsonl
from etl.utils.batching import batch_iterable
from etl.services.raw_ingest import insert_raw
from etl.services.airport_etl import upsert_airport
from etl.services.entity_etl import (
    upsert_entity, insert_contacts, upsert_clearance, upsert_nearby_airports,
    backfill_association_links, audit_unmapped_organizations, reconcile_listing_roles,
)
from etl.services.etl_logger import log_progress, log_error
from etl.db import get_db_cursor

ETL_RUN_INSERT = """
INSERT INTO etl_runs (started_at, entity_type, status)
VALUES (NOW(), %s, 'RUNNING') RETURNING id;
"""
ETL_RUN_FINISH = """
UPDATE etl_runs SET finished_at=NOW(), status=%s, records_processed=%s,
    records_failed=%s, error_summary=%s WHERE id=%s;
"""
APP_LOG_INSERT = """
INSERT INTO app_logs (level, message, context) VALUES ('ERROR', %s, %s);
"""


def process_record(record, listing_observer=None, airport_observer=None):
    """Preserve the raw payload before constructing normalized entity data."""
    if isinstance(record, dict) and record.get('scrape_status') == 'FAILED' and 'data' not in record:
        insert_raw(record)
        return
    if not isinstance(record, dict) or 'entity_type' not in record or not isinstance(record.get('data'), dict):
        raise ValueError('Missing entity_type or object data')
    entity_type = record['entity_type']
    if entity_type not in {'airport', 'organization', 'clearance', 'nearby_airports'}:
        raise ValueError(f'Unknown entity_type: {entity_type}')
    insert_raw(record)
    data = dict(record['data'])
    for field in ['url', 'scrape_status', 'external_id', 'observed_fields', 'missing_fields', 'errors']:
        if field in record:
            data[field] = record[field]
    if entity_type == 'airport':
        airport_id = upsert_airport(data)
        for org in data.get('organizations', []):
            entity_id, claimed = upsert_entity(org, airport_id, listing_observer)
            if not claimed:
                insert_contacts(entity_id, org)
        for clearance in data.get('clearances', []):
            upsert_clearance(clearance, airport_id)
        for nearby in data.get('nearby_airports', []):
            upsert_nearby_airports(nearby, airport_id)
        if airport_observer and record.get('scrape_status') == 'SUCCESS':
            airport_observer(airport_id)
    elif entity_type == 'organization':
        upsert_entity(data, listing_observer=listing_observer)
    elif entity_type == 'clearance':
        upsert_clearance(data)
    else:
        upsert_nearby_airports(data)


def main(input_path):
    if Config.BATCH_SIZE < 1 or Config.LOG_EVERY < 1:
        raise ValueError('BATCH_SIZE and LOG_EVERY must be positive')
    record_stream = stream_jsonl(input_path)
    try:
        first_record = next(record_stream)
    except StopIteration:
        print(f'Skipping empty file: {input_path}')
        return {'processed': 0, 'failed': 0, 'status': 'EMPTY'}
    entity_type = first_record.get('entity_type') if isinstance(first_record, dict) else None
    with get_db_cursor(commit=True) as cur:
        cur.execute(ETL_RUN_INSERT, [entity_type])
        run_id = cur.fetchone()['id']

    def full_stream():
        yield first_record
        yield from record_stream

    count = failed = 0
    fatal_error = None
    complete_airports = set()
    observed_listings = {}

    def observe_listing(listing_id, airport_id, roles, sections):
        entry = observed_listings.setdefault(listing_id, (airport_id, set(), set()))
        entry[1].update(roles)
        entry[2].update(sections)

    try:
        for batch in batch_iterable(full_stream(), Config.BATCH_SIZE):
            for record in batch:
                try:
                    process_record(record, observe_listing, complete_airports.add)
                    count += 1
                    if count % Config.LOG_EVERY == 0:
                        log_progress(count)
                except Exception as exc:
                    failed += 1
                    stack = traceback.format_exc()
                    log_error(str(exc), exc=stack)
                    with get_db_cursor(commit=True) as cur:
                        cur.execute(APP_LOG_INSERT, [str(exc), json.dumps({
                            'run_id': str(run_id), 'stack': stack, 'raw': record,
                        })])
            backfill_association_links()
        backfill_association_links()
        if not failed:
            reconcile_listing_roles({
                listing_id: (roles, sections)
                for listing_id, (airport_id, roles, sections) in observed_listings.items()
                if airport_id in complete_airports
            }, complete_airports)
        unmapped_count, unmapped_sample = audit_unmapped_organizations(limit=20)
        if unmapped_count:
            log_error(f'Unmapped organizations after processing: {unmapped_count}', raw={'sample': unmapped_sample})
    except BaseException as exc:
        fatal_error = str(exc) or type(exc).__name__
        raise
    finally:
        status = 'FAILED' if failed or fatal_error else 'SUCCESS'
        error_summary = fatal_error or (f'{failed} records failed' if failed else None)
        with get_db_cursor(commit=True) as cur:
            cur.execute(ETL_RUN_FINISH, [status, count + failed, failed, error_summary, run_id])
    summary = {'processed': count, 'failed': failed, 'status': status, 'run_id': str(run_id)}
    print(json.dumps(summary))
    if failed:
        raise RuntimeError(f'ETL failed for {failed} records in {input_path}; see app_logs')
    return summary


def cli():
    parser = argparse.ArgumentParser(description='Import AC-U-KWIK JSONL into PostgreSQL')
    parser.add_argument('inputs', nargs='*', type=Path, help='JSONL files to import')
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parent.parent / 'acukwik_data')
    args = parser.parse_args()
    paths = args.inputs or sorted(args.data_dir.glob('*.jsonl'))
    if not paths:
        parser.error(f'No .jsonl files found in {args.data_dir}; supply an input file')
    for path in paths:
        if not path.is_file():
            parser.error(f'Input file does not exist: {path}')
    failures = 0
    for path in paths:
        print(f'Processing: {path}')
        try:
            main(path)
        except Exception as exc:
            log_error(str(exc))
            failures += 1
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(cli())
