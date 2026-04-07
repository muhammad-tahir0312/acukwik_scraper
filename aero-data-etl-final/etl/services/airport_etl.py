"""
Airport ETL: upserts normalized airport data.
"""
from etl.models.airport import AIRPORT_UPSERT
from etl.db import get_db_cursor
from etl.services.location_lookup import match_country, match_state, match_city
import json

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

# Note: We DO NOT insert new countries/states/cities. Match against authoritative tables only.

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
    pcn = data.get('pcn')
    url = data.get('url')
    scrape_status = data.get('scrape_status')
    external_id = data.get('external_id')
    observed_fields = data.get('observed_fields')
    missing_fields = data.get('missing_fields')
    errors = data.get('errors')
    afs_aftn = data.get('afs_aftn')
    airport_general_remarks = data.get('airport_general_remarks')
    airport_hours = data.get('airport_hours')
    airport_light_intensity = data.get('airport_light_intensity')
    airport_manager_phone = data.get('airport_manager_phone')
    airport_email = data.get('airport_email')
    airport_of_entry = data.get('airport_of_entry')
    airport_of_entry_remarks = data.get('airport_of_entry_remarks')
    airport_ownership = data.get('airport_ownership')
    airport_website = data.get('airport_website')
    atis_frequency = data.get('atis_frequency')
    control_tower_hours = data.get('control_tower_hours')
    ctaf_frequency = data.get('ctaf_frequency')
    customs = data.get('customs')
    distance_from_city = data.get('distance_from_city')
    dst = data.get('dst')
    faa_id = data.get('faa_id')
    facility_use = data.get('facility_use')
    fire_category = data.get('fire_category')
    fire_category_remarks = data.get('fire_category_remarks')
    handling_mandatory = data.get('handling_mandatory')
    local_standard_time = data.get('local_standard_time')
    open_24h = data.get('open_24h')
    slots_required = data.get('slots_required')
    sunrise = data.get('sunrise')
    sunset = data.get('sunset')
    tower_frequency = data.get('tower_frequency')
    us_customs_pre_clearance = data.get('us_customs_pre_clearance')
    variation = data.get('variation')

    # Store any extra fields not mapped above
    known_fields = {
        'icao', 'iata', 'name', 'airport_type', 'city', 'country', 'coordinates_raw', 'elevation_raw',
        'fuel_available', 'approaches', 'runway_surface', 'longest_runway_raw', 'pcn', 'utc_offset',
        'external_id', 'url', 'scrape_status', 'observed_fields', 'missing_fields', 'errors',
        'afs_aftn', 'airport_general_remarks', 'airport_hours', 'airport_light_intensity', 'airport_manager_phone', 'airport_email',
        'airport_of_entry', 'airport_of_entry_remarks', 'airport_ownership', 'airport_website', 'atis_frequency',
        'control_tower_hours', 'ctaf_frequency', 'customs', 'distance_from_city', 'dst', 'faa_id', 'facility_use',
        'fire_category', 'fire_category_remarks', 'handling_mandatory', 'local_standard_time', 'open_24h',
        'slots_required', 'sunrise', 'sunset', 'tower_frequency', 'us_customs_pre_clearance', 'variation'
    }
    extra = {k: v for k, v in data.items() if k not in known_fields}
    import json
    raw_city = city
    raw_state = data.get('state') or data.get('region')
    raw_country = country
    with get_db_cursor(commit=True) as cur:
        # Match country -> state -> city. Do not create new normalized rows.
        country_id, country_candidates = match_country(raw_country, cur)
        state_id, state_candidates = (None, [])
        city_id, city_candidates = (None, [])
        if raw_state:
            state_id, state_candidates = match_state(raw_state, country_id, cur)
        if raw_city:
            city_id, city_candidates = match_city(raw_city, country_id, state_id, cur)

        # If any of the lookups produced ambiguous or empty results, insert a review row
        candidates = {}
        if country_candidates:
            candidates['country'] = country_candidates
        if state_candidates:
            candidates['state'] = state_candidates
        if city_candidates:
            candidates['city'] = city_candidates
        if candidates:
            cur.execute(
                "INSERT INTO airport_location_review (external_id, icao, raw_country, raw_state, raw_city, candidates) VALUES (%s, %s, %s, %s, %s, %s)",
                [external_id, icao, raw_country, raw_state, raw_city, json.dumps(candidates)]
            )

        cur.execute(AIRPORT_UPSERT, [
            icao, iata, name, airport_type, city_id, country_id, state_id, lat_deg, lon_deg, elevation_ft,
            fuel_available, approaches, runway_surface, length_ft, width_ft, ident, utc_offset,
            pcn, url, scrape_status, external_id, observed_fields, missing_fields,
            afs_aftn, airport_general_remarks, airport_hours, airport_light_intensity, airport_manager_phone,
            airport_email,
            airport_of_entry, airport_of_entry_remarks, airport_ownership, airport_website, atis_frequency,
            control_tower_hours, coord_raw, ctaf_frequency, customs, distance_from_city, dst, data.get('elevation_raw'),
            faa_id, facility_use, fire_category, fire_category_remarks, handling_mandatory, local_standard_time,
            longest_runway_raw, open_24h, slots_required, sunrise, sunset, tower_frequency, us_customs_pre_clearance, variation,
            raw_city, raw_state, raw_country,
            json.dumps(errors) if errors else None, json.dumps(extra) if extra else '{}'
        ])
        return cur.fetchone()['id']
