"""
Airport ETL: upserts normalized airport data.
"""
from etl.models.airport import AIRPORT_UPSERT
from etl.db import get_db_cursor

def parse_latlong(coord_raw):
    # Example: 'N55-07.6/E030-21.0'
    import re
    lat_match = re.search(r'N(\d+)-(\d+\.\d+)', coord_raw)
    lon_match = re.search(r'E(\d+)-(\d+\.\d+)', coord_raw)
    if lat_match and lon_match:
        lat_deg = float(lat_match.group(1)) + float(lat_match.group(2))/60
        lon_deg = float(lon_match.group(1)) + float(lon_match.group(2))/60
        return lat_deg, lon_deg
    return None, None

def parse_runway(runway_raw):
    # Example: '8550 x 138, 05/23'
    import re
    match = re.match(r'(\d+) x (\d+), ([^,]+)', runway_raw)
    if match:
        length = int(match.group(1))
        width = int(match.group(2))
        ident = match.group(3)
        return length, width, ident
    return None, None, None

def get_city_id(city, cur):
    # city: str or (city, country_id) tuple
    if not city:
        return None
    if isinstance(city, tuple):
        city_name, country_id = city
    else:
        city_name, country_id = city, None
    if country_id:
        cur.execute("SELECT id, country_id FROM cities WHERE name=%s", [city_name])
        row = cur.fetchone()
        if row:
            if not row['country_id']:
                cur.execute("UPDATE cities SET country_id=%s WHERE id=%s", [country_id, row['id']])
            return row['id']
        cur.execute("INSERT INTO cities (name, country_id) VALUES (%s, %s) RETURNING id", [city_name, country_id])
        return cur.fetchone()['id']
    else:
        cur.execute("SELECT id FROM cities WHERE name=%s", [city_name])
        row = cur.fetchone()
        if row:
            return row['id']
        cur.execute("INSERT INTO cities (name) VALUES (%s) RETURNING id", [city_name])
        return cur.fetchone()['id']

def get_country_id(country, cur):
    if not country:
        return None
    cur.execute("SELECT id FROM countries WHERE name=%s", [country])
    row = cur.fetchone()
    if row:
        return row['id']
    cur.execute("INSERT INTO countries (name) VALUES (%s) RETURNING id", [country])
    return cur.fetchone()['id']

def upsert_airport(data):
    icao = data.get('icao')
    iata = data.get('iata')
    name = data.get('name')
    airport_type = data.get('airport_type')
    city = data.get('city')
    country = data.get('country')
    coord_raw = data.get('coordinates_raw')
    lat_deg, lon_deg = parse_latlong(coord_raw) if coord_raw else (None, None)
    elevation_ft = int(data.get('elevation_raw')) if data.get('elevation_raw') else None
    fuel_available = data.get('fuel_available')
    approaches = data.get('approaches')
    runway_surface = data.get('runway_surface')
    longest_runway_raw = data.get('longest_runway_raw')
    length_ft, width_ft, ident = parse_runway(longest_runway_raw) if longest_runway_raw else (None, None, None)
    utc_offset = data.get('utc_offset')
    source = data.get('source')
    source_external_id = data.get('external_id')
    pcn = data.get('pcn')
    scraped_at = data.get('scraped_at')
    url = data.get('url')
    scrape_status = data.get('scrape_status')
    scrape_duration_ms = data.get('scrape_duration_ms')
    external_id = data.get('external_id')
    observed_fields = data.get('observed_fields')
    missing_fields = data.get('missing_fields')
    errors = data.get('errors')

    # Store any extra fields not mapped above
    known_fields = {
        'icao', 'iata', 'name', 'airport_type', 'city', 'country', 'coordinates_raw', 'elevation_raw',
        'fuel_available', 'approaches', 'runway_surface', 'longest_runway_raw', 'pcn', 'utc_offset',
        'source', 'external_id', 'scraped_at', 'url', 'scrape_status', 'scrape_duration_ms', 'observed_fields', 'missing_fields', 'errors'
    }
    extra = {k: v for k, v in data.items() if k not in known_fields}
    import json
    with get_db_cursor(commit=True) as cur:
        country_id = get_country_id(country, cur)
        city_id = get_city_id((city, country_id), cur)
        cur.execute(AIRPORT_UPSERT, [
            icao, iata, name, airport_type, city_id, country_id, lat_deg, lon_deg, elevation_ft,
            fuel_available, approaches, runway_surface, length_ft, width_ft, ident, utc_offset,
            source, source_external_id, pcn, scraped_at, url, scrape_status, scrape_duration_ms,
            external_id, observed_fields, missing_fields,
            json.dumps(errors) if errors else None, json.dumps(extra) if extra else '{}'
        ])
        return cur.fetchone()['id']
