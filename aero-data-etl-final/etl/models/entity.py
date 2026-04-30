"""
Organization/entity upsert and lookup queries.
"""
ORG_UPSERT = """
INSERT INTO organizations (
    name, description, website, email, phone, address_id, distance_from_airport, price_range, sita_code, aftn_code, brand, frequency, toll_free, remarks, phone_after_hours, fax, postal_code, label, url, scrape_status, external_id, observed_fields, missing_fields, roles, errors, extra
)
Values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (external_id) DO UPDATE SET
    description = COALESCE(EXCLUDED.description, organizations.description),
    website = COALESCE(EXCLUDED.website, organizations.website),
    email = COALESCE(EXCLUDED.email, organizations.email),
    phone = COALESCE(EXCLUDED.phone, organizations.phone),
    address_id = COALESCE(EXCLUDED.address_id, organizations.address_id),
    distance_from_airport = COALESCE(EXCLUDED.distance_from_airport, organizations.distance_from_airport),
    price_range = COALESCE(EXCLUDED.price_range, organizations.price_range),
    sita_code = COALESCE(EXCLUDED.sita_code, organizations.sita_code),
    aftn_code = COALESCE(EXCLUDED.aftn_code, organizations.aftn_code),
    brand = COALESCE(EXCLUDED.brand, organizations.brand),
    frequency = COALESCE(EXCLUDED.frequency, organizations.frequency),
    toll_free = COALESCE(EXCLUDED.toll_free, organizations.toll_free),
    remarks = COALESCE(EXCLUDED.remarks, organizations.remarks),
    phone_after_hours = COALESCE(EXCLUDED.phone_after_hours, organizations.phone_after_hours),
    fax = COALESCE(EXCLUDED.fax, organizations.fax),
    postal_code = COALESCE(EXCLUDED.postal_code, organizations.postal_code),
    label = COALESCE(EXCLUDED.label, organizations.label),
    url = COALESCE(EXCLUDED.url, organizations.url),
    scrape_status = COALESCE(EXCLUDED.scrape_status, organizations.scrape_status),
    external_id = COALESCE(EXCLUDED.external_id, organizations.external_id),
    observed_fields = COALESCE(EXCLUDED.observed_fields, organizations.observed_fields),
    missing_fields = COALESCE(EXCLUDED.missing_fields, organizations.missing_fields),
    roles = COALESCE(EXCLUDED.roles, organizations.roles),
    errors = COALESCE(EXCLUDED.errors, organizations.errors),
    extra = COALESCE(EXCLUDED.extra, organizations.extra)
RETURNING id;
"""
ORG_AIRPORT_LINK = """
INSERT INTO organization_airports (organization_id, airport_id)
VALUES (%s, %s)
ON CONFLICT DO NOTHING;
"""

# Clearance upsert and link
CLEARANCE_UPSERT = """
INSERT INTO clearances (
    country, country_phone_code, currency, exchange_guide, time_zone, general_information, wgs84, visa, documentation, application_format, comments, clearance_contacts, url, scrape_status, external_id, observed_fields, missing_fields, errors, associated_airports, extra
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (external_id) DO UPDATE SET
    country = COALESCE(EXCLUDED.country, clearances.country),
    country_phone_code = COALESCE(EXCLUDED.country_phone_code, clearances.country_phone_code),
    currency = COALESCE(EXCLUDED.currency, clearances.currency),
    exchange_guide = COALESCE(EXCLUDED.exchange_guide, clearances.exchange_guide),
    time_zone = COALESCE(EXCLUDED.time_zone, clearances.time_zone),
    general_information = COALESCE(EXCLUDED.general_information, clearances.general_information),
    wgs84 = COALESCE(EXCLUDED.wgs84, clearances.wgs84),
    visa = COALESCE(EXCLUDED.visa, clearances.visa),
    documentation = COALESCE(EXCLUDED.documentation, clearances.documentation),
    application_format = COALESCE(EXCLUDED.application_format, clearances.application_format),
    comments = COALESCE(EXCLUDED.comments, clearances.comments),
    clearance_contacts = COALESCE(EXCLUDED.clearance_contacts, clearances.clearance_contacts),
    url = COALESCE(EXCLUDED.url, clearances.url),
    scrape_status = COALESCE(EXCLUDED.scrape_status, clearances.scrape_status),
    external_id = COALESCE(EXCLUDED.external_id, clearances.external_id),
    observed_fields = COALESCE(EXCLUDED.observed_fields, clearances.observed_fields),
    missing_fields = COALESCE(EXCLUDED.missing_fields, clearances.missing_fields),
    errors = COALESCE(EXCLUDED.errors, clearances.errors),
    associated_airports = COALESCE(EXCLUDED.associated_airports, clearances.associated_airports),
    extra = COALESCE(EXCLUDED.extra, clearances.extra)
RETURNING id;
"""

# Nearby airports upsert and link
NEARBY_AIRPORTS_UPSERT = """
INSERT INTO nearby_airports (
    associated_airports, icaos, names, urls, primary_runways, airport_types, cities,
    url, scrape_status, external_id, observed_fields, missing_fields, errors, extra
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (external_id) DO UPDATE SET
    associated_airports = COALESCE(EXCLUDED.associated_airports, nearby_airports.associated_airports),
    icaos = COALESCE(EXCLUDED.icaos, nearby_airports.icaos),
    names = COALESCE(EXCLUDED.names, nearby_airports.names),
    urls = COALESCE(EXCLUDED.urls, nearby_airports.urls),
    primary_runways = COALESCE(EXCLUDED.primary_runways, nearby_airports.primary_runways),
    airport_types = COALESCE(EXCLUDED.airport_types, nearby_airports.airport_types),
    cities = COALESCE(EXCLUDED.cities, nearby_airports.cities),
    url = COALESCE(EXCLUDED.url, nearby_airports.url),
    scrape_status = COALESCE(EXCLUDED.scrape_status, nearby_airports.scrape_status),
    external_id = COALESCE(EXCLUDED.external_id, nearby_airports.external_id),
    observed_fields = COALESCE(EXCLUDED.observed_fields, nearby_airports.observed_fields),
    missing_fields = COALESCE(EXCLUDED.missing_fields, nearby_airports.missing_fields),
    errors = COALESCE(EXCLUDED.errors, nearby_airports.errors),
    extra = COALESCE(EXCLUDED.extra, nearby_airports.extra)
RETURNING id;
"""
