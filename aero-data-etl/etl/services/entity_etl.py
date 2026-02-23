"""
Entity (organization) ETL: upserts normalized orgs and contacts, never overwrites claimed/user-edited data.
"""
from etl.models.entity import ORG_UPSERT, ORG_AIRPORT_LINK
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
    scraped_at = org.get('scraped_at')
    url = org.get('url')
    scrape_status = org.get('scrape_status')
    scrape_duration_ms = org.get('scrape_duration_ms')
    external_id = org.get('external_id')
    observed_fields = org.get('observed_fields')
    missing_fields = org.get('missing_fields')
    errors = org.get('errors')
    # Store any extra fields not mapped above
    known_fields = {
        'name', 'description', 'website', 'email', 'phone', 'contacts', 'roles', 'associated_airports',
        'address', 'distance_from_airport', 'price_range', 'sita_code', 'brand', 'frequency', 'phone_after_hours',
        'fax', 'postal_code', 'label', 'scraped_at', 'url', 'scrape_status', 'scrape_duration_ms', 'external_id', 'observed_fields', 'missing_fields', 'errors'
    }
    extra = {k: v for k, v in org.items() if k not in known_fields}
    import json
    with get_db_cursor(commit=True) as cur:
        cur.execute(ORG_UPSERT, [
            name, description, website, email, phone,
            address_id,
            distance_from_airport, price_range, sita_code, brand, frequency, phone_after_hours,
            fax, postal_code, label, scraped_at, url, scrape_status, scrape_duration_ms, external_id,
            observed_fields, missing_fields, json.dumps(errors) if errors else None, json.dumps(extra) if extra else '{}'
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

def insert_contacts(entity_id, org):
    # Implement if airport_contacts table exists
    pass
