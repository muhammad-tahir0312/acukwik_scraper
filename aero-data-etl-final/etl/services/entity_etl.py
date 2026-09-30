"""
Entity (organization) ETL: upserts normalized orgs and contacts, never overwrites claimed/user-edited data.
"""
from etl.models.entity import ORG_AIRPORT_LINK, CLEARANCE_UPSERT, NEARBY_AIRPORTS_UPSERT
from etl.models.airport import AIRPORT_CLEARANCE_LINK, AIRPORT_NEARBY_LINK
from etl.db import get_db_cursor
import logging
import hashlib
import json
import re


logger = logging.getLogger(__name__)


ORG_INSERT = """
INSERT INTO organizations (
    name, description, website, email, phone, address_id, distance_from_airport, price_range,
    sita_code, aftn_code, brand, frequency, toll_free, remarks, phone_after_hours, fax, postal_code, label,
    url, scrape_status, external_id, observed_fields, missing_fields,
    roles, errors, extra
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    toll_free = COALESCE(%s, toll_free),
    remarks = COALESCE(%s, remarks),
    phone_after_hours = COALESCE(%s, phone_after_hours),
    fax = COALESCE(%s, fax),
    postal_code = COALESCE(%s, postal_code),
    label = COALESCE(%s, label),
    url = COALESCE(%s, url),
    scrape_status = COALESCE(%s, scrape_status),
    external_id = COALESCE(%s, external_id),
    observed_fields = COALESCE(%s, observed_fields),
    missing_fields = COALESCE(%s, missing_fields),
    roles = COALESCE(%s, roles),
    errors = COALESCE(%s, errors),
    extra = COALESCE(%s, extra),
    updated_at = NOW()
WHERE id = %s
RETURNING id;
"""

def _normalize_contact_value(contact_type, value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if contact_type == 'website':
        return text.lower().rstrip('/')
    if contact_type == 'email':
        return text.lower()
    if contact_type in {'phone', 'fax', 'phone_after_hours'}:
        import re
        text = re.sub(r'[^\d+]', '', text)
        return text or None
    return text


def _unique_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        if value is None:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _merge_lists(existing_values, new_values):
    return _unique_preserve_order((existing_values or []) + (new_values or []))


def _as_text_array(value):
    if value is None:
        return []

    values = value if isinstance(value, list) else [value]
    normalized = []
    for item in values:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            normalized.append(text)

    return _unique_preserve_order(normalized)


def extract_contact_arrays(org):
    arrays = {
        'website': [],
        'email': [],
        'phone': [],
        'fax': [],
        'phone_after_hours': [],
    }

    def add_value(contact_type, value):
        normalized = _normalize_contact_value(contact_type, value)
        if normalized:
            arrays[contact_type].append(normalized)

    for contact in org.get('contacts') or []:
        contact_type = str(contact.get('type', '')).strip().lower()
        if contact_type in arrays:
            add_value(contact_type, contact.get('value'))

    for field_name in ['website', 'email', 'phone', 'fax', 'phone_after_hours']:
        field_value = org.get(field_name)
        if isinstance(field_value, list):
            for value in field_value:
                add_value(field_name, value)
        else:
            add_value(field_name, field_value)

    return {key: _unique_preserve_order(values) for key, values in arrays.items()}


def get_or_create_role(role, cur):
    """Return the role id for a canonical role name."""
    cur.execute("SELECT id FROM organization_roles WHERE name=%s", [role])
    row = cur.fetchone()
    if row:
        return row['id']
    cur.execute("INSERT INTO organization_roles (name) VALUES (%s) RETURNING id", [role])
    return cur.fetchone()['id']


def upsert_roles(org_id, roles, cur):
    """Maintain the deprecated organization-wide aggregate for compatibility."""
    for role in roles:
        role_id = get_or_create_role(role, cur)
        cur.execute("INSERT INTO organization_role_map (organization_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", [org_id, role_id])


def canonical_organization_external_id(org, contact_arrays=None):
    """Build a conservative company identity without using airport or role."""
    contacts = contact_arrays or extract_contact_arrays(org)
    profile_url = (org.get('source_profile_url') or '').strip().lower().rstrip('/')
    normalized_name = re.sub(r'[^a-z0-9]+', ' ', org.get('name', '').lower()).strip()
    strong_identifiers = sorted({
        *contacts.get('website', []),
        *contacts.get('email', []),
        *contacts.get('phone', []),
    })
    if profile_url:
        material = f"profile|{profile_url}"
    elif strong_identifiers:
        material = f"contact|{normalized_name}|{'|'.join(strong_identifiers)}"
    else:
        source_id = org.get('source_listing_id') or org.get('source_listing_key') or ''
        material = f"listing|{normalized_name}|{source_id}"
    digest = hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]
    return f"acukwik_company_{digest}"


def organization_names_are_aliases(left, right):
    """Conservative name similarity used only when a phone already matches."""
    def normalize(value):
        return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()

    left_name = normalize(left or '')
    right_name = normalize(right or '')
    if not left_name or not right_name:
        return False
    if left_name == right_name or left_name in right_name or right_name in left_name:
        return True
    ignored = {'aviation', 'airport', 'international', 'services', 'service', 'handling', 'catering'}
    left_tokens = {token for token in left_name.split() if len(token) >= 4 and token not in ignored}
    right_tokens = {token for token in right_name.split() if len(token) >= 4 and token not in ignored}
    return bool(left_tokens & right_tokens)


def upsert_airport_listing(cur, org_id, airport_id, org, roles):
    """Upsert the lossless airport-specific listing and its scoped roles."""
    listing_key = org.get('source_listing_key')
    # Older scrapes hashed an empty identifier object as "{}", giving every
    # unlinked hotel at an airport the same key. Derive a name-specific key
    # when no source identifier exists, for old and new input alike.
    has_source_identity = (
        org.get('source_profile_url') or org.get('source_listing_id')
        or org.get('source_identifiers')
    )
    if not listing_key or not has_source_identity:
        identity = '|'.join([
            str(airport_id),
            org.get('source_profile_url') or '',
            org.get('source_listing_id') or '',
            org.get('name') or '',
        ])
        listing_key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]

    cur.execute(
        """
        INSERT INTO organization_airport_listings (
            organization_id, airport_id, listing_key, source_listing_id,
            source_profile_url, source_section, source_sections, display_name,
            display_names, contacts, address,
            attributes, raw_fields, links, media, raw_text, source_identifiers,
            url, scrape_status, observed_fields, missing_fields, errors
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
            %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s,
            %s, %s, %s::jsonb
        )
        ON CONFLICT (airport_id, listing_key) DO UPDATE SET
            organization_id = EXCLUDED.organization_id,
            source_listing_id = COALESCE(EXCLUDED.source_listing_id, organization_airport_listings.source_listing_id),
            source_profile_url = COALESCE(EXCLUDED.source_profile_url, organization_airport_listings.source_profile_url),
            source_section = EXCLUDED.source_section,
            source_sections = ARRAY(
                SELECT DISTINCT value FROM unnest(
                    organization_airport_listings.source_sections || EXCLUDED.source_sections
                ) AS value
            ),
            display_name = EXCLUDED.display_name,
            display_names = ARRAY(
                SELECT DISTINCT value FROM unnest(
                    organization_airport_listings.display_names || EXCLUDED.display_names
                ) AS value
            ),
            contacts = EXCLUDED.contacts,
            address = COALESCE(EXCLUDED.address, organization_airport_listings.address),
            attributes = EXCLUDED.attributes,
            raw_fields = EXCLUDED.raw_fields,
            links = EXCLUDED.links,
            media = EXCLUDED.media,
            raw_text = EXCLUDED.raw_text,
            source_identifiers = EXCLUDED.source_identifiers,
            url = EXCLUDED.url,
            scrape_status = EXCLUDED.scrape_status,
            observed_fields = EXCLUDED.observed_fields,
            missing_fields = EXCLUDED.missing_fields,
            errors = EXCLUDED.errors,
            updated_at = NOW()
        RETURNING id
        """,
        [
            org_id, airport_id, listing_key, org.get('source_listing_id'),
            org.get('source_profile_url'), org.get('source_section') or 'Unknown',
            org.get('source_sections') or [org.get('source_section') or 'Unknown'],
            org.get('display_name') or org.get('name'),
            org.get('display_names') or [org.get('display_name') or org.get('name')],
            json.dumps(org.get('contacts') or []),
            json.dumps(org.get('address')) if org.get('address') else None,
            json.dumps(org.get('attributes') or {}), json.dumps(org.get('raw_fields') or []),
            json.dumps(org.get('links') or []), json.dumps(org.get('media') or []),
            org.get('raw_text'), json.dumps(org.get('source_identifiers') or {}),
            org.get('url'), org.get('scrape_status'), org.get('observed_fields'),
            org.get('missing_fields'), json.dumps(org.get('errors') or []),
        ]
    )
    listing_id = cur.fetchone()['id']
    for role in roles:
        role_id = get_or_create_role(role, cur)
        cur.execute(
            "INSERT INTO organization_airport_listing_roles (listing_id, role_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [listing_id, role_id]
        )
        cur.execute(
            "INSERT INTO organization_airport_roles (organization_id, airport_id, role_id) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [org_id, airport_id, role_id]
        )
    # Retire this organization's old, identifier-free collision row only after
    # its replacement has been written. Raw imports remain available for audit.
    old_key = org.get('source_listing_key')
    if old_key and old_key != listing_key and not has_source_identity:
        cur.execute(
            """DELETE FROM organization_airport_listings
               WHERE airport_id=%s AND organization_id=%s AND listing_key=%s
                 AND source_profile_url IS NULL AND source_listing_id IS NULL
                 AND source_identifiers='{}'::jsonb""",
            [airport_id, org_id, old_key],
        )
    return listing_id


def upsert_entity(org, airport_id=None):
    name = org.get('name')
    description = org.get('description')
    contact_arrays = extract_contact_arrays(org)
    website = contact_arrays['website']
    email = contact_arrays['email']
    phone = contact_arrays['phone']
    fax = contact_arrays['fax']
    phone_after_hours = contact_arrays['phone_after_hours']
    roles = _as_text_array(org.get('roles', []) or [])
    associated_airports = set(org.get('associated_airports', []) or [])
    address = org.get('address')
    # These values are intentionally stored on organization_airport_listings.
    distance_from_airport = None
    price_range = None
    sita_code = None
    aftn_code = None
    contacts = org.get('contacts') or []

    brand = []
    frequency = None
    toll_free = []
    remarks = None
    postal_code = None
    label = []
    address_id = None
    country = None
    city = None

    # Addresses and service details belong to the airport listing.  Only an
    # explicitly supplied canonical_address may populate the company record.
    canonical_address = org.get('canonical_address')
    if canonical_address and isinstance(canonical_address, dict):
        address = canonical_address
        postal_code = address.get('postal_code')
        country = address.get('country')
        city = address.get('city')
        from etl.services.location_lookup import match_country, match_city, match_state
        with get_db_cursor(commit=True) as cur:
            country_id, _ = match_country(country, cur)
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

    for contact in org.get('contacts') or []:
        if contact.get('label'):
            label = _as_text_array(contact['label'])
            break

    url = org.get('source_profile_url')
    scrape_status = org.get('scrape_status')
    external_id = canonical_organization_external_id(org, contact_arrays)

    if not associated_airports and url:
        match = re.search(r"/Airport-Info/([A-Za-z0-9]+)", url)
        if match:
            associated_airports.add(match.group(1).upper())

    def _normalize_url(value):
        if not value:
            return None
        return value.rstrip('/').lower()

    normalized_url = _normalize_url(url)

    observed_fields = org.get('observed_fields')
    missing_fields = org.get('missing_fields')
    known_fields = {
        'name', 'description', 'website', 'email', 'phone', 'fax', 'phone_after_hours', 'contacts', 'roles',
        'associated_airports', 'address', 'distance_from_airport', 'price_range', 'sita_code', 'aftn_code',
        'brand', 'frequency', 'toll_free', 'remarks', 'postal_code', 'label', 'url', 'scrape_status', 'external_id', 'observed_fields',
        'missing_fields', 'errors'
        , 'display_name', 'display_names', 'source_section', 'source_sections', 'source_listing_key', 'source_listing_id',
        'source_profile_url', 'source_identifiers', 'raw_fields', 'attributes',
        'links', 'media', 'raw_text', 'canonical_address'
    }
    extra = {k: v for k, v in org.items() if k not in known_fields}
    errors_json = json.dumps(org.get('errors')) if org.get('errors') else None
    extra_json = json.dumps(extra) if extra else '{}'

    with get_db_cursor(commit=True) as cur:
        row = None
        if external_id:
            cur.execute("SELECT * FROM organizations WHERE external_id=%s LIMIT 1", [external_id])
            row = cur.fetchone()
        if not row and website:
            cur.execute(
                """
                SELECT * FROM organizations
                WHERE COALESCE(website, ARRAY[]::text[]) && %s::text[]
                LIMIT 1
                """,
                [website]
            )
            row = cur.fetchone()
        if not row and email:
            cur.execute(
                """
                SELECT * FROM organizations
                WHERE COALESCE(email, ARRAY[]::text[]) && %s::text[]
                LIMIT 1
                """,
                [email]
            )
            row = cur.fetchone()
        if not row and name and phone:
            cur.execute(
                """
                SELECT * FROM organizations
                WHERE COALESCE(phone, ARRAY[]::text[]) && %s::text[]
                LIMIT 20
                """,
                [phone]
            )
            candidates = cur.fetchall()
            row = next(
                (candidate for candidate in candidates if organization_names_are_aliases(name, candidate['name'])),
                None,
            )
        if row:
            org_id = row['id']
            merged_website = _merge_lists(row['website'], website)
            merged_email = _merge_lists(row['email'], email)
            merged_phone = _merge_lists(row['phone'], phone)
            merged_phone_after_hours = _merge_lists(row['phone_after_hours'], phone_after_hours)
            merged_fax = _merge_lists(row['fax'], fax)
            merged_roles = _merge_lists(row['roles'], roles)
            associated_airports = set(row.get('associated_airports', []) or []).union(associated_airports)

            cur.execute(ORG_UPDATE_BY_ID, [
                name, description, merged_website, merged_email, merged_phone,
                address_id, distance_from_airport, price_range, sita_code, aftn_code, brand, frequency, toll_free, remarks,
                merged_phone_after_hours, merged_fax, postal_code, label, url, scrape_status, row['external_id'],
                observed_fields, missing_fields, merged_roles, errors_json, extra_json, org_id
            ])
            org_id = cur.fetchone()['id']
        else:
            cur.execute(ORG_INSERT, [
                name, description, website, email, phone,
                address_id, distance_from_airport, price_range, sita_code, aftn_code, brand, frequency, toll_free, remarks,
                phone_after_hours, fax, postal_code, label, url, scrape_status, external_id,
                observed_fields, missing_fields, roles, errors_json, extra_json
            ])
            org_id = cur.fetchone()['id']

        linked_airport_ids = set()
        if airport_id:
            linked_airport_ids.add(airport_id)

        for assoc_icao in associated_airports:
            cur.execute(
                "SELECT id FROM airports WHERE icao=%s OR source_airport_id=%s",
                [assoc_icao, assoc_icao],
            )
            airport_row = cur.fetchone()
            if airport_row:
                linked_airport_ids.add(airport_row['id'])

        if not associated_airports and normalized_url:
            cur.execute(
                "SELECT id FROM airports WHERE lower(regexp_replace(COALESCE(url, ''), '/+$', ''))=%s",
                [normalized_url]
            )
            airport_row = cur.fetchone()
            if airport_row:
                linked_airport_ids.add(airport_row['id'])

        for linked_airport_id in linked_airport_ids:
            cur.execute(ORG_AIRPORT_LINK, [org_id, linked_airport_id])
            upsert_airport_listing(cur, org_id, linked_airport_id, org, roles)

        # Compatibility aggregate only; authoritative roles are above.
        upsert_roles(org_id, roles, cur)

    return org_id, False

def upsert_clearance(clearance, airport_id=None):
    known_fields = {
        'country', 'country_phone_code', 'currency', 'exchange_guide', 'time_zone',
        'general_information', 'wgs84', 'visa', 'documentation', 'application_format',
        'comments', 'clearance_contacts', 'url', 'scrape_status', 'external_id',
        'observed_fields', 'missing_fields', 'errors', 'associated_airports', 'extra',
    }
    extra = dict(clearance.get('extra') or {})
    extra.update({key: value for key, value in clearance.items() if key not in known_fields})
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
            json.dumps(extra)
        ])
        clearance_id = cur.fetchone()['id']
        # Link to explicitly provided airport
        if airport_id:
            cur.execute(AIRPORT_CLEARANCE_LINK, [airport_id, clearance_id])
        else:
            # Standalone record: references can be ICAO codes or AC-U-KWIK path IDs.
            for airport_reference in (clearance.get('associated_airports') or []):
                cur.execute(
                    "SELECT id FROM airports WHERE icao=%s OR source_airport_id=%s",
                    [airport_reference, airport_reference],
                )
                row = cur.fetchone()
                if row:
                    cur.execute(AIRPORT_CLEARANCE_LINK, [row['id'], clearance_id])
        return clearance_id

def upsert_nearby_airports(nearby, airport_id=None):
    UPSERT_MINIMAL_AIRPORT = """
        INSERT INTO airports (icao, source_airport_id, external_id, name, airport_type, url)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (external_id) WHERE external_id IS NOT NULL DO UPDATE SET
            name = COALESCE(EXCLUDED.name, airports.name),
            airport_type = COALESCE(EXCLUDED.airport_type, airports.airport_type),
            url = COALESCE(EXCLUDED.url, airports.url),
            source_airport_id = COALESCE(EXCLUDED.source_airport_id, airports.source_airport_id)
        RETURNING id;
    """

    def airport_identity(item):
        icao = item.get('icao')
        source_airport_id = item.get('source_airport_id')
        external_id = (
            f"acukwik_{icao}" if icao
            else f"acukwik_source_{source_airport_id}" if source_airport_id
            else None
        )
        return icao, source_airport_id, external_id

    # Case 1: individual nearby airport object from airport's nested list
    # Shape: {icao, name, url, primary_runway, airport_type, city}
    if nearby.get('icao') or nearby.get('source_airport_id'):
        icao, source_airport_id, airport_external_id = airport_identity(nearby)
        with get_db_cursor(commit=True) as cur:
            cur.execute(UPSERT_MINIMAL_AIRPORT, [
                icao, source_airport_id, airport_external_id, nearby.get('name'),
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
    nearby_known_fields = {
        'associated_airports', 'url', 'scrape_status', 'external_id',
        'observed_fields', 'missing_fields', 'errors', 'extra',
    }
    nearby_extra = dict(nearby.get('extra') or {})
    nearby_extra.update({key: value for key, value in nearby.items() if key not in nearby_known_fields})
    # Keep legacy column compatibility while retaining code-less AC-U-KWIK IDs.
    nearby_icaos = [item.get('icao') or item.get('source_airport_id') for item in items]
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
            json.dumps(nearby_extra)
        ])
        nearby_record_id = cur.fetchone()['id']

        # Upsert each nearby airport into airports table and create links
        for item in (nearby.get('nearby_airports') or []):
            icao, source_airport_id, airport_external_id = airport_identity(item)
            if not airport_external_id:
                continue
            cur.execute(UPSERT_MINIMAL_AIRPORT, [
                icao, source_airport_id, airport_external_id, item.get('name'),
                item.get('airport_type'), item.get('url')
            ])
            item_airport_id = cur.fetchone()['id']

            if airport_id:
                cur.execute(AIRPORT_NEARBY_LINK, [airport_id, item_airport_id])
            else:
                for icao in (nearby.get('associated_airports') or []):
                    cur.execute(
                        "SELECT id FROM airports WHERE icao=%s OR source_airport_id=%s",
                        [icao, icao],
                    )
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
            JOIN airports a ON a.icao = assoc.icao OR a.source_airport_id = assoc.icao
            ON CONFLICT DO NOTHING
            """
        )

        cur.execute(
            """
            INSERT INTO airport_nearby_airports (airport_id, nearby_airport_id)
            SELECT DISTINCT src.id, dst.id
            FROM nearby_airports n
            JOIN LATERAL unnest(COALESCE(n.associated_airports, ARRAY[]::text[])) AS src_icao(icao) ON TRUE
            JOIN airports src ON src.icao = src_icao.icao OR src.source_airport_id = src_icao.icao
            JOIN LATERAL unnest(COALESCE(n.icaos, ARRAY[]::text[])) AS dst_icao(icao) ON TRUE
            JOIN airports dst ON dst.icao = dst_icao.icao OR dst.source_airport_id = dst_icao.icao
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
