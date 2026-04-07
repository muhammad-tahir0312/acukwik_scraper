"""
Entity (organization) ETL: upserts normalized orgs and contacts, never overwrites claimed/user-edited data.
"""
from etl.models.entity import ORG_AIRPORT_LINK, CLEARANCE_UPSERT, NEARBY_AIRPORTS_UPSERT
from etl.models.airport import AIRPORT_CLEARANCE_LINK, AIRPORT_NEARBY_LINK
from etl.db import get_db_cursor
import logging


logger = logging.getLogger(__name__)


ORG_INSERT = """
INSERT INTO organizations (
    name, description, website, email, phone, address_id, distance_from_airport, price_range,
    sita_code, aftn_code, brand, frequency, phone_after_hours, fax, postal_code, label,
    url, scrape_status, external_id, observed_fields, missing_fields, contacts,
    roles, errors, extra
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;
"""

ORG_UPDATE_BY_ID = """
UPDATE organizations SET
    name = COALESCE(%s, name),
    description = COALESCE(%s, description),
    website = COALESCE(%s, website),
    email = COALESCE(%s, email),
    phone = COALESCE(%s, phone),
    address_id = COALESCE(%s, address_id),
    distance_from_airport = COALESCE(%s, distance_from_airport),
    price_range = COALESCE(%s, price_range),
    sita_code = COALESCE(%s, sita_code),
    aftn_code = COALESCE(%s, aftn_code),
    brand = COALESCE(%s, brand),
    frequency = COALESCE(%s, frequency),
    phone_after_hours = COALESCE(%s, phone_after_hours),
    fax = COALESCE(%s, fax),
    postal_code = COALESCE(%s, postal_code),
    label = COALESCE(%s, label),
    url = COALESCE(%s, url),
    scrape_status = COALESCE(%s, scrape_status),
    external_id = COALESCE(%s, external_id),
    observed_fields = COALESCE(%s, observed_fields),
    missing_fields = COALESCE(%s, missing_fields),
    contacts = COALESCE(%s, contacts),
    roles = COALESCE(%s, roles),
    errors = COALESCE(%s, errors),
    extra = COALESCE(%s, extra),
    updated_at = NOW()
WHERE id = %s
RETURNING id;
"""

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
    country = None
    city = None
    if address and isinstance(address, dict):
        postal_code = address.get('postal_code')
        country = address.get('country')
        city = address.get('city')
        # Match against normalized tables; do NOT create new country/city rows.
        from etl.services.location_lookup import match_country, match_city, match_state
        with get_db_cursor(commit=True) as cur:
            country_id, _ = match_country(country, cur)
            # If address provided state/region, try match_state for completeness
            state = address.get('state') or address.get('region')
            state_id = None
            if state:
                state_id, _ = match_state(state, country_id, cur)
            city_id, _ = match_city(city, country_id, state_id, cur)
            street = address.get('street')
            full_addr = address.get('full')
            cur.execute(
                """
                SELECT id FROM addresses
                WHERE city_id IS NOT DISTINCT FROM %s
                  AND country_id IS NOT DISTINCT FROM %s
                  AND street IS NOT DISTINCT FROM %s
                  AND full_address IS NOT DISTINCT FROM %s
                  AND postal_code IS NOT DISTINCT FROM %s
                LIMIT 1
                """,
                [city_id, country_id, street, full_addr, address.get('postal_code')]
            )
            existing_addr = cur.fetchone()
            if existing_addr:
                address_id = existing_addr['id']
            else:
                cur.execute(
                    "INSERT INTO addresses (city_id, country_id, street, full_address, postal_code) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    [city_id, country_id, street, full_addr, address.get('postal_code')]
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

    # Some scraped org rows arrive with empty associated_airports; derive from airport URL when available.
    if not associated_airports and url:
        import re
        m = re.search(r"/Airport-Info/([A-Za-z0-9]+)", url)
        if m:
            associated_airports = [m.group(1).upper()]

    def _normalize_url(value):
        if not value:
            return None
        return value.rstrip('/').lower()

    normalized_url = _normalize_url(url)

    # Disambiguate generic external IDs (e.g., ENTERPRISE/HERTZ) that can repeat across airports.
    if external_id and associated_airports:
        airport_hint = associated_airports[0]
        if not external_id.endswith(f"_{airport_hint}"):
            external_id = f"{external_id}_{airport_hint}"

    if not external_id:
        airport_hint = associated_airports[0] if associated_airports else None
        if airport_hint:
            external_id = f"org_{name}_{airport_hint}" if name else None
        elif normalized_url:
            external_id = f"org_{name}_{abs(hash(normalized_url))}" if name else None
        else:
            external_id = f"org_{name}" if name else None
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
        contacts_json = json.dumps(org.get('contacts')) if org.get('contacts') else None
        errors_json = json.dumps(org.get('errors')) if org.get('errors') else None
        extra_json = json.dumps(extra) if extra else '{}'

        org_id = None
        if external_id:
            cur.execute("SELECT id FROM organizations WHERE external_id=%s", [external_id])
            row = cur.fetchone()
            if row:
                org_id = row['id']

        if org_id:
            cur.execute(ORG_UPDATE_BY_ID, [
                name, description, website, email, phone,
                address_id,
                distance_from_airport, price_range, sita_code, aftn_code, brand, frequency, phone_after_hours,
                fax, postal_code, label, url, scrape_status, external_id,
                observed_fields, missing_fields,
                contacts_json,
                roles or [],
                errors_json,
                extra_json,
                org_id
            ])
            org_id = cur.fetchone()['id']
        else:
            cur.execute(ORG_INSERT, [
                name, description, website, email, phone,
                address_id,
                distance_from_airport, price_range, sita_code, aftn_code, brand, frequency, phone_after_hours,
                fax, postal_code, label, url, scrape_status, external_id,
                observed_fields, missing_fields,
                contacts_json,
                roles or [],
                errors_json,
                extra_json
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

        # Fallback: if no explicit associated_airports, link via shared airport page URL.
        if not associated_airports and normalized_url:
            cur.execute(
                "SELECT id FROM airports WHERE lower(regexp_replace(COALESCE(url, ''), '/+$', ''))=%s",
                [normalized_url]
            )
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


def backfill_association_links():
    """Backfill link tables for records that arrived before their related airport rows."""
    with get_db_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO organization_airports (organization_id, airport_id)
            SELECT o.id, a.id
            FROM organizations o
                        JOIN airports a
                            ON lower(regexp_replace(COALESCE(a.url, ''), '/+$', '')) = lower(regexp_replace(COALESCE(o.url, ''), '/+$', ''))
            ON CONFLICT DO NOTHING
            """
        )

        cur.execute(
            """
            INSERT INTO airport_clearances (airport_id, clearance_id)
            SELECT a.id, c.id
            FROM clearances c
            JOIN LATERAL unnest(COALESCE(c.associated_airports, ARRAY[]::text[])) AS assoc(icao) ON TRUE
            JOIN airports a ON a.icao = assoc.icao
            ON CONFLICT DO NOTHING
            """
        )

        cur.execute(
            """
            INSERT INTO airport_nearby_airports (airport_id, nearby_airport_id)
            SELECT DISTINCT src.id, dst.id
            FROM nearby_airports n
            JOIN LATERAL unnest(COALESCE(n.associated_airports, ARRAY[]::text[])) AS src_icao(icao) ON TRUE
            JOIN airports src ON src.icao = src_icao.icao
            JOIN LATERAL unnest(COALESCE(n.icaos, ARRAY[]::text[])) AS dst_icao(icao) ON TRUE
            JOIN airports dst ON dst.icao = dst_icao.icao
            ON CONFLICT DO NOTHING
            """
        )


def audit_unmapped_organizations(limit=25):
    """Return count and sample of organizations that are not linked to any airport."""
    with get_db_cursor(commit=False) as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM organizations o
            LEFT JOIN organization_airports oa ON oa.organization_id = o.id
            WHERE oa.organization_id IS NULL
            """
        )
        total = cur.fetchone()['cnt']

        cur.execute(
            """
            SELECT o.id, o.name, o.external_id, o.url, o.created_at
            FROM organizations o
            LEFT JOIN organization_airports oa ON oa.organization_id = o.id
            WHERE oa.organization_id IS NULL
            ORDER BY o.created_at DESC
            LIMIT %s
            """,
            [limit]
        )
        rows = cur.fetchall()

    if total:
        logger.warning(f"Unmapped organizations detected: {total}")
    return total, rows
