"""
Entity (organization) ETL: upserts normalized orgs and contacts, never overwrites claimed/user-edited data.
"""
from etl.models.entity import ORG_UPSERT, ORG_AIRPORT_LINK, CLEARANCE_UPSERT, NEARBY_AIRPORTS_UPSERT
from etl.models.airport import AIRPORT_CLEARANCE_LINK, AIRPORT_NEARBY_LINK
from etl.db import get_db_cursor

def extract_contacts(org):
    phone_list, fax_list, email_list, website = [], [], [], None
    contacts = org.get('contacts')
    if not contacts:
        contacts = []
    for contact in contacts:
        if contact['type'] == 'phone':
            phone_list.append(contact['value'])
        elif contact['type'] == 'fax':
            fax_list.append(contact['value'])
        elif contact['type'] == 'email':
            email_list.append(contact['value'])
        elif contact['type'] == 'website':
            website = contact['value']
    return phone_list, fax_list, email_list, website

def upsert_roles(org_id, roles, cur):
    for role in roles:
        # Upsert role
        cur.execute("SELECT id FROM organization_roles WHERE name=%s", [role])
        row = cur.fetchone()
        if row:
            role_id = row['id']
        else:
            cur.execute("INSERT INTO organization_roles (name) VALUES (%s) RETURNING id", [role])
            role_id = cur.fetchone()['id']
        # Link org to role
        cur.execute("INSERT INTO organization_role_map (organization_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", [org_id, role_id])

def upsert_entity(org, airport_id=None):
    name = org.get('name')
    description = org.get('description')
    phone_list, fax_list, email_list, website = extract_contacts(org)
    email = email_list[0] if email_list else None
    phone = phone_list[0] if phone_list else None
    fax = fax_list[0] if fax_list else None
    roles = org.get('roles', [])
    associated_airports = org.get('associated_airports', [])
    address = org.get('address')
    distance_from_airport = org.get('distance_from_airport')
    price_range = org.get('price_range')
    sita_code = org.get('sita_code')
    aftn_code = org.get('aftn_code')
    brand = org.get('brand')
    frequency = org.get('frequency')
    phone_after_hours = org.get('phone_after_hours')
    postal_code = None
    label = None
    # Extract postal_code and label from address/contacts if present
    address_id = None
    if address and isinstance(address, dict):
        postal_code = address.get('postal_code')
        country = address.get('country')
        city = address.get('city')
        with get_db_cursor(commit=True) as cur:
            country_id = None
            city_id = None
            if country:
                cur.execute("SELECT id FROM countries WHERE name=%s", [country])
                row = cur.fetchone()
                if row:
                    country_id = row['id']
                else:
                    cur.execute("INSERT INTO countries (name) VALUES (%s) RETURNING id", [country])
                    country_id = cur.fetchone()['id']
            if city:
                cur.execute("SELECT id FROM cities WHERE name=%s AND country_id=%s", [city, country_id])
                row = cur.fetchone()
                if row:
                    city_id = row['id']
                else:
                    cur.execute("INSERT INTO cities (name, country_id) VALUES (%s, %s) RETURNING id", [city, country_id])
                    city_id = cur.fetchone()['id']
            cur.execute(
                "INSERT INTO addresses (city_id, country_id, street, full_address, postal_code) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                [city_id, country_id, address.get('street'), address.get('full'), address.get('postal_code')]
            )
            address_id = cur.fetchone()['id']
    contacts = org.get('contacts') or []
    for contact in contacts:
        if contact.get('label'):
            label = contact['label']
            break
    url = org.get('url')
    scrape_status = org.get('scrape_status')
    external_id = org.get('external_id')
    observed_fields = org.get('observed_fields')
    missing_fields = org.get('missing_fields')
    # Store any extra fields not mapped above
    known_fields = {
        'name', 'description', 'website', 'email', 'phone', 'contacts', 'roles', 'associated_airports',
        'address', 'distance_from_airport', 'price_range', 'sita_code', 'aftn_code', 'brand', 'frequency', 'phone_after_hours',
        'fax', 'postal_code', 'label', 'url', 'scrape_status', 'external_id', 'observed_fields', 'missing_fields', 'errors'
    }
    extra = {k: v for k, v in org.items() if k not in known_fields}
    import json
    with get_db_cursor(commit=True) as cur:
        cur.execute(ORG_UPSERT, [
            name, description, website, email, phone,
            address_id,
            distance_from_airport, price_range, sita_code, aftn_code, brand, frequency, phone_after_hours,
            fax, postal_code, label, url, scrape_status, external_id,
            observed_fields, missing_fields,
            json.dumps(org.get('contacts')) if org.get('contacts') else None,
            org.get('associated_airports') or [],
            org.get('roles') or [],
            json.dumps(org.get('errors')) if org.get('errors') else None,
            json.dumps(extra) if extra else '{}'
        ])
        org_id = cur.fetchone()['id']
        # Link to airport if provided
        if airport_id:
            cur.execute(ORG_AIRPORT_LINK, [org_id, airport_id])
        # Link to associated airports
        for assoc_icao in associated_airports:
            cur.execute("SELECT id FROM airports WHERE icao=%s", [assoc_icao])
            airport_row = cur.fetchone()
            if airport_row:
                cur.execute(ORG_AIRPORT_LINK, [org_id, airport_row['id']])
        # Upsert roles and link
        upsert_roles(org_id, roles, cur)
    return org_id, False

def upsert_clearance(clearance, airport_id=None):
    import json
    with get_db_cursor(commit=True) as cur:
        cur.execute(CLEARANCE_UPSERT, [
            clearance.get('country'), clearance.get('country_phone_code'), clearance.get('currency'), clearance.get('exchange_guide'),
            clearance.get('time_zone'), clearance.get('general_information'), clearance.get('wgs84'), clearance.get('visa'),
            clearance.get('documentation'), clearance.get('application_format'), clearance.get('comments'),
            json.dumps(clearance.get('clearance_contacts')) if clearance.get('clearance_contacts') else None,
            clearance.get('url'), clearance.get('scrape_status'),
            clearance.get('external_id'), clearance.get('observed_fields'), clearance.get('missing_fields'),
            json.dumps(clearance.get('errors')) if clearance.get('errors') else None,
            clearance.get('associated_airports') or [],
            json.dumps(clearance.get('extra')) if clearance.get('extra') else '{}'
        ])
        clearance_id = cur.fetchone()['id']
        # Link to explicitly provided airport
        if airport_id:
            cur.execute(AIRPORT_CLEARANCE_LINK, [airport_id, clearance_id])
        else:
            # Standalone clearance record: link via associated_airports ICAO codes
            for icao in (clearance.get('associated_airports') or []):
                cur.execute("SELECT id FROM airports WHERE icao = %s", [icao])
                row = cur.fetchone()
                if row:
                    cur.execute(AIRPORT_CLEARANCE_LINK, [row['id'], clearance_id])
        return clearance_id

def upsert_nearby_airports(nearby, airport_id=None):
    import json

    UPSERT_MINIMAL_AIRPORT = """
        INSERT INTO airports (icao, name, airport_type, url)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (icao) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, airports.name),
            airport_type = COALESCE(EXCLUDED.airport_type, airports.airport_type)
        RETURNING id;
    """

    # Case 1: individual nearby airport object from airport's nested list
    # Shape: {icao, name, url, primary_runway, airport_type, city}
    if 'icao' in nearby:
        with get_db_cursor(commit=True) as cur:
            cur.execute(UPSERT_MINIMAL_AIRPORT, [
                nearby.get('icao'), nearby.get('name'),
                nearby.get('airport_type'), nearby.get('url')
            ])
            nearby_airport_id = cur.fetchone()['id']
            if airport_id:
                cur.execute(AIRPORT_NEARBY_LINK, [airport_id, nearby_airport_id])
        return nearby_airport_id

    # Case 2: standalone nearby_airports scrape record
    # Shape: {associated_airports, nearby_airports: [...], url, scrape_status, ...}
    url = nearby.get('url', '')
    external_id = nearby.get('external_id') or (
        f"nearby_{url.rstrip('/').split('/')[-1]}" if url else None
    )
    items = nearby.get('nearby_airports') or []
    nearby_icaos = [item.get('icao') for item in items]
    nearby_names = [item.get('name') for item in items]
    nearby_urls = [item.get('url') for item in items]
    nearby_primary_runways = [item.get('primary_runway') for item in items]
    nearby_airport_types = [item.get('airport_type') for item in items]
    nearby_cities = [item.get('city') for item in items]
    with get_db_cursor(commit=True) as cur:
        cur.execute(NEARBY_AIRPORTS_UPSERT, [
            nearby.get('associated_airports') or [],
            nearby_icaos,
            nearby_names,
            nearby_urls,
            nearby_primary_runways,
            nearby_airport_types,
            nearby_cities,
            url, nearby.get('scrape_status'),
            external_id, nearby.get('observed_fields'), nearby.get('missing_fields'),
            json.dumps(nearby.get('errors')) if nearby.get('errors') else None,
            json.dumps(nearby.get('extra')) if nearby.get('extra') else '{}'
        ])
        nearby_record_id = cur.fetchone()['id']

        # Upsert each nearby airport into airports table and create links
        for item in (nearby.get('nearby_airports') or []):
            if not item.get('icao'):
                continue
            cur.execute(UPSERT_MINIMAL_AIRPORT, [
                item.get('icao'), item.get('name'),
                item.get('airport_type'), item.get('url')
            ])
            item_airport_id = cur.fetchone()['id']

            if airport_id:
                cur.execute(AIRPORT_NEARBY_LINK, [airport_id, item_airport_id])
            else:
                for icao in (nearby.get('associated_airports') or []):
                    cur.execute("SELECT id FROM airports WHERE icao = %s", [icao])
                    row = cur.fetchone()
                    if row:
                        cur.execute(AIRPORT_NEARBY_LINK, [row['id'], item_airport_id])

        return nearby_record_id

def insert_contacts(entity_id, org):
    # Implement if airport_contacts table exists
    pass
