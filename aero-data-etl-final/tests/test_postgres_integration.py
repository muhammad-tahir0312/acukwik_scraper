"""Real PostgreSQL regression checks, isolated in a disposable schema.

Run with ETL_TEST_DATABASE_URL pointing to an authorized test database.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import make_dsn

from etl.config import Config
from etl.db import get_db_cursor
from etl.main import main
from etl.services import location_lookup

ETL_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(monkeypatch):
    dsn = os.getenv('ETL_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('Set ETL_TEST_DATABASE_URL to run PostgreSQL checks')
    schema = 'etl_test_' + uuid.uuid4().hex
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            # Qualify the production schema/migrations inside an isolated namespace.
            for source in [ETL_ROOT / 'db/etl_schema.sql', *sorted((ETL_ROOT / 'db/migrations').glob('*.sql'))]:
                cur.execute(source.read_text().replace('public.', schema + '.'))
        scoped_dsn = make_dsn(dsn, options=f'-c search_path={schema},public')
        monkeypatch.setattr(Config, 'DATABASE_URL', scoped_dsn)
        for cache in location_lookup._cache.values():
            cache.clear()
        yield scoped_dsn
    finally:
        with conn.cursor() as cur:
            cur.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        conn.close()
        for cache in location_lookup._cache.values():
            cache.clear()


def record(kind, external_id, data, airport='KAAA'):
    return {'entity_type': kind, 'external_id': external_id, 'scrape_status': 'SUCCESS',
            'url': f'https://acukwik.com/Airport-Info/{airport}', 'data': data,
            'observed_fields': ['name'], 'missing_fields': [], 'errors': []}


def write_input(tmp_path, records):
    path = tmp_path / 'input.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in records))
    return path


def scalar(query):
    with get_db_cursor() as cur:
        cur.execute(query)
        return next(iter(cur.fetchone().values()))


def test_listings_roles_raw_preservation_and_rerun(database, tmp_path):
    airports = [record('airport', f'acukwik_{code}', {'name': code, 'icao': code,
                'source_airport_id': code, 'country': 'Testland'}, code) for code in ['KAAA', 'KBBB']]
    hotels = [record('organization', 'old_colliding_id', {
        'name': name, 'roles': ['HOTEL'], 'associated_airports': ['KAAA'],
        'source_listing_key': 'old_shared_key', 'source_identifiers': {},
        'source_section': 'Hotels', 'contacts': [{'type': 'phone', 'value': phone}],
    }) for name, phone in [('First Hotel', '+15550100'), ('Second Hotel', '+15550200')]]
    company = [record('organization', f'company_{airport}_{role}', {
        'name': 'Shared Company', 'roles': [role], 'associated_airports': [airport],
        'source_profile_url': 'https://acukwik.com/Basic-Info/shared-company',
        'source_listing_key': f'shared_{airport}', 'source_section': role,
        'frequency': '121.90' if role == 'HANDLER' else None,
        'price_range': '$$$' if role == 'CATERING' else None,
        'raw_fields': [{'label': 'Role marker', 'value': role}],
        'attributes': {'role marker': role},
    }, airport) for airport, role in [('KAAA', 'HANDLER'), ('KAAA', 'CATERING'), ('KBBB', 'FUEL_SUPPLIER')]]
    records = airports + hotels + company
    path = write_input(tmp_path, records)
    original = copy.deepcopy(records)
    for iteration in [1, 2]:
        result = main(path)
        assert result['status'] == 'SUCCESS'
        assert result['processed'] == 7
        assert scalar('SELECT count(*) FROM airports') == 2
        assert scalar('SELECT count(*) FROM organizations') == 3
        assert scalar('SELECT count(*) FROM organization_airport_listings') == 4
        assert scalar('SELECT count(*) FROM organization_airport_roles') == 5
        assert scalar('SELECT count(*) FROM organization_airport_listing_roles') == 5
        with get_db_cursor() as cur:
            cur.execute('''SELECT r.name, lr.details FROM organization_airport_listing_roles lr
                           JOIN organization_roles r ON r.id=lr.role_id
                           JOIN organization_airport_listings l ON l.id=lr.listing_id
                           JOIN airports a ON a.id=l.airport_id
                           WHERE a.icao='KAAA' AND l.listing_key='shared_KAAA' ''')
            details = {row['name']: row['details'] for row in cur.fetchall()}
            assert details['HANDLER']['fields']['frequency'] == '121.90'
            assert details['CATERING']['fields']['price_range'] == '$$$'
            assert details['HANDLER']['attributes']['role marker'] == 'HANDLER'
            assert details['CATERING']['attributes']['role marker'] == 'CATERING'
        assert scalar('SELECT count(*) FROM scraped_records') == 7 * iteration  # append-only audit
        with get_db_cursor() as cur:
            cur.execute('SELECT data FROM scraped_records ORDER BY id LIMIT 7')
            assert [row['data'] for row in cur.fetchall()] == [row['data'] for row in original]
    assert scalar("SELECT count(*) FROM etl_runs WHERE status='SUCCESS' AND records_processed=7 AND records_failed=0 AND finished_at IS NOT NULL") == 2
    assert scalar('SELECT count(*) FROM app_logs') == 0
    with get_db_cursor() as cur:
        cur.execute('''SELECT a.icao, r.name FROM organization_airport_roles ar
                       JOIN airports a ON a.id=ar.airport_id
                       JOIN organizations o ON o.id=ar.organization_id
                       JOIN organization_roles r ON r.id=ar.role_id
                       WHERE o.name='Shared Company' ''')
        assert {(r['icao'], r['name']) for r in cur.fetchall()} == {
            ('KAAA', 'HANDLER'), ('KAAA', 'CATERING'), ('KBBB', 'FUEL_SUPPLIER')}


def test_bad_record_marks_run_failed_and_cli_exits_nonzero(database, tmp_path):
    path = write_input(tmp_path, [record('airport', 'acukwik_KAAA', {'name': 'Test', 'icao': 'KAAA'}),
                                  {'broken': 'record'}])
    result = subprocess.run([sys.executable, '-m', 'etl.main', str(path)],
        env={**os.environ, 'DATABASE_URL': database, 'PYTHONPATH': str(ETL_ROOT)},
        capture_output=True, text=True)
    assert result.returncode == 1, result.stdout + result.stderr
    assert scalar('SELECT count(*) FROM airports') == 1
    assert scalar("SELECT count(*) FROM etl_runs WHERE status='FAILED' AND records_processed=2 AND records_failed=1 AND finished_at IS NOT NULL") == 1
    assert scalar('SELECT count(*) FROM app_logs') == 1


def test_complete_snapshot_removes_stale_listing_role_without_touching_partial_import(database, tmp_path):
    airport = record('airport', 'acukwik_KAAA', {
        'name': 'Test Airport', 'icao': 'KAAA', 'source_airport_id': 'KAAA',
    })
    def occurrence(role):
        return record('organization', f'company_KAAA_{role}', {
            'name': 'Shared Company', 'roles': [role], 'associated_airports': ['KAAA'],
            'source_profile_url': 'https://acukwik.com/Basic-Info/shared-company',
            'source_listing_key': 'shared_KAAA', 'source_section': role,
        })

    assert main(write_input(tmp_path, [airport, occurrence('HANDLER'), occurrence('CATERING')]))['status'] == 'SUCCESS'
    assert scalar('SELECT count(*) FROM organization_airport_listing_roles') == 2

    assert main(write_input(tmp_path, [airport, occurrence('HANDLER')]))['status'] == 'SUCCESS'
    assert scalar('SELECT count(*) FROM organization_airport_listing_roles') == 1
    assert scalar('SELECT count(*) FROM organization_airport_roles') == 1
    with get_db_cursor() as cur:
        cur.execute('SELECT source_sections FROM organization_airport_listings')
        assert cur.fetchone()['source_sections'] == ['HANDLER']

    with get_db_cursor(commit=True) as cur:
        cur.execute('SELECT organization_id, airport_id FROM organization_airport_listings LIMIT 1')
        pair = cur.fetchone()
        cur.execute("INSERT INTO organization_roles (name) VALUES ('STORE') ON CONFLICT DO NOTHING")
        cur.execute('''INSERT INTO organization_airport_listings
                       (organization_id, airport_id, listing_key, source_section, display_name)
                       VALUES (%s, %s, %s, 'Manual', 'Shared Company') RETURNING id''',
                    [pair['organization_id'], pair['airport_id'], f"manual:{pair['organization_id']}"])
        manual_id = cur.fetchone()['id']
        cur.execute('''INSERT INTO organization_airport_listing_roles (listing_id, role_id)
                       SELECT %s, id FROM organization_roles WHERE name='STORE' ''', [manual_id])

    assert main(write_input(tmp_path, [airport, occurrence('HANDLER')]))['status'] == 'SUCCESS'
    assert scalar('SELECT count(*) FROM organization_airport_listing_roles') == 2
    assert scalar('SELECT count(*) FROM organization_airport_roles') == 2

    # An organization-only file does not prove the complete set of roles.
    assert main(write_input(tmp_path, [occurrence('CATERING')]))['status'] == 'SUCCESS'
    assert scalar('SELECT count(*) FROM organization_airport_listing_roles') == 3

    # A failed airport scrape with partial data is not a complete snapshot.
    failed_airport = {**airport, 'scrape_status': 'FAILED'}
    assert main(write_input(tmp_path, [failed_airport]))['status'] == 'SUCCESS'
    assert scalar('SELECT count(*) FROM organization_airport_listing_roles') == 3

    # An empty but complete airport snapshot removes vanished source listings.
    assert main(write_input(tmp_path, [airport]))['status'] == 'SUCCESS'
    assert scalar('SELECT count(*) FROM organization_airport_listings') == 1
    assert scalar('SELECT count(*) FROM organization_airport_listing_roles') == 1
    assert scalar('SELECT count(*) FROM organization_airport_roles') == 1
    with get_db_cursor() as cur:
        cur.execute('SELECT listing_key FROM organization_airport_listings')
        assert cur.fetchone()['listing_key'] == f"manual:{pair['organization_id']}"
