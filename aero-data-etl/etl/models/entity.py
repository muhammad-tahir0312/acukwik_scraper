"""
Organization/entity upsert and lookup queries.
"""
ORG_UPSERT = """
INSERT INTO organizations (
    name, description, website, email, phone, address_id, distance_from_airport, price_range, sita_code, brand, frequency, phone_after_hours, fax, postal_code, label, scraped_at, url, scrape_status, scrape_duration_ms, external_id, observed_fields, missing_fields, errors, extra
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (name) DO UPDATE SET
    description = COALESCE(EXCLUDED.description, organizations.description),
    website = COALESCE(EXCLUDED.website, organizations.website),
    email = COALESCE(EXCLUDED.email, organizations.email),
    phone = COALESCE(EXCLUDED.phone, organizations.phone),
    address_id = COALESCE(EXCLUDED.address_id, organizations.address_id),
    distance_from_airport = COALESCE(EXCLUDED.distance_from_airport, organizations.distance_from_airport),
    price_range = COALESCE(EXCLUDED.price_range, organizations.price_range),
    sita_code = COALESCE(EXCLUDED.sita_code, organizations.sita_code),
    brand = COALESCE(EXCLUDED.brand, organizations.brand),
    frequency = COALESCE(EXCLUDED.frequency, organizations.frequency),
    phone_after_hours = COALESCE(EXCLUDED.phone_after_hours, organizations.phone_after_hours),
    fax = COALESCE(EXCLUDED.fax, organizations.fax),
    postal_code = COALESCE(EXCLUDED.postal_code, organizations.postal_code),
    label = COALESCE(EXCLUDED.label, organizations.label),
    scraped_at = COALESCE(EXCLUDED.scraped_at, organizations.scraped_at),
    url = COALESCE(EXCLUDED.url, organizations.url),
    scrape_status = COALESCE(EXCLUDED.scrape_status, organizations.scrape_status),
    scrape_duration_ms = COALESCE(EXCLUDED.scrape_duration_ms, organizations.scrape_duration_ms),
    external_id = COALESCE(EXCLUDED.external_id, organizations.external_id),
    observed_fields = COALESCE(EXCLUDED.observed_fields, organizations.observed_fields),
    missing_fields = COALESCE(EXCLUDED.missing_fields, organizations.missing_fields),
    errors = COALESCE(EXCLUDED.errors, organizations.errors),
    extra = COALESCE(EXCLUDED.extra, organizations.extra)
RETURNING id;
"""
ORG_AIRPORT_LINK = """
INSERT INTO organization_airports (organization_id, airport_id)
VALUES (%s, %s)
ON CONFLICT DO NOTHING;
"""
