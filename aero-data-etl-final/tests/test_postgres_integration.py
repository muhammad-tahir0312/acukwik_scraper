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
