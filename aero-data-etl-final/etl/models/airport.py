"""
Airport upsert and lookup queries.
"""
AIRPORT_UPSERT = """
INSERT INTO airports (
    icao, iata, name, airport_type, city_id, country_id, latitude, longitude, elevation_ft,
    fuel_available, approaches, runway_surface, longest_runway_length_ft, longest_runway_width_ft, longest_runway_ident, utc_offset,
    pcn, url, scrape_status, external_id, observed_fields, missing_fields,
    afs_aftn, airport_general_remarks, airport_hours, airport_light_intensity, airport_manager_phone,
    airport_email,
    airport_of_entry, airport_of_entry_remarks, airport_ownership, airport_website, atis_frequency,
    control_tower_hours, coordinates_raw, ctaf_frequency, customs, distance_from_city, dst, elevation_raw,
    faa_id, facility_use, fire_category, fire_category_remarks, handling_mandatory, local_standard_time,
    longest_runway_raw, open_24h, slots_required, sunrise, sunset, tower_frequency, us_customs_pre_clearance, variation,
    errors, extra
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (icao) DO UPDATE SET
    iata = COALESCE(EXCLUDED.iata, airports.iata),
    name = COALESCE(EXCLUDED.name, airports.name),
    airport_type = COALESCE(EXCLUDED.airport_type, airports.airport_type),
    city_id = COALESCE(EXCLUDED.city_id, airports.city_id),
    country_id = COALESCE(EXCLUDED.country_id, airports.country_id),
    latitude = COALESCE(EXCLUDED.latitude, airports.latitude),
    longitude = COALESCE(EXCLUDED.longitude, airports.longitude),
    elevation_ft = COALESCE(EXCLUDED.elevation_ft, airports.elevation_ft),
    fuel_available = COALESCE(EXCLUDED.fuel_available, airports.fuel_available),
    approaches = COALESCE(EXCLUDED.approaches, airports.approaches),
    runway_surface = COALESCE(EXCLUDED.runway_surface, airports.runway_surface),
    longest_runway_length_ft = COALESCE(EXCLUDED.longest_runway_length_ft, airports.longest_runway_length_ft),
    longest_runway_width_ft = COALESCE(EXCLUDED.longest_runway_width_ft, airports.longest_runway_width_ft),
    longest_runway_ident = COALESCE(EXCLUDED.longest_runway_ident, airports.longest_runway_ident),
    utc_offset = COALESCE(EXCLUDED.utc_offset, airports.utc_offset),
    pcn = COALESCE(EXCLUDED.pcn, airports.pcn),
    url = COALESCE(EXCLUDED.url, airports.url),
    scrape_status = COALESCE(EXCLUDED.scrape_status, airports.scrape_status),
    external_id = COALESCE(EXCLUDED.external_id, airports.external_id),
    observed_fields = COALESCE(EXCLUDED.observed_fields, airports.observed_fields),
    missing_fields = COALESCE(EXCLUDED.missing_fields, airports.missing_fields),
    afs_aftn = COALESCE(EXCLUDED.afs_aftn, airports.afs_aftn),
    airport_general_remarks = COALESCE(EXCLUDED.airport_general_remarks, airports.airport_general_remarks),
    airport_hours = COALESCE(EXCLUDED.airport_hours, airports.airport_hours),
    airport_light_intensity = COALESCE(EXCLUDED.airport_light_intensity, airports.airport_light_intensity),
    airport_manager_phone = COALESCE(EXCLUDED.airport_manager_phone, airports.airport_manager_phone),
    airport_email = COALESCE(EXCLUDED.airport_email, airports.airport_email),
    airport_of_entry = COALESCE(EXCLUDED.airport_of_entry, airports.airport_of_entry),
    airport_of_entry_remarks = COALESCE(EXCLUDED.airport_of_entry_remarks, airports.airport_of_entry_remarks),
    airport_ownership = COALESCE(EXCLUDED.airport_ownership, airports.airport_ownership),
    airport_website = COALESCE(EXCLUDED.airport_website, airports.airport_website),
    atis_frequency = COALESCE(EXCLUDED.atis_frequency, airports.atis_frequency),
    control_tower_hours = COALESCE(EXCLUDED.control_tower_hours, airports.control_tower_hours),
    coordinates_raw = COALESCE(EXCLUDED.coordinates_raw, airports.coordinates_raw),
    ctaf_frequency = COALESCE(EXCLUDED.ctaf_frequency, airports.ctaf_frequency),
    customs = COALESCE(EXCLUDED.customs, airports.customs),
    distance_from_city = COALESCE(EXCLUDED.distance_from_city, airports.distance_from_city),
    dst = COALESCE(EXCLUDED.dst, airports.dst),
    elevation_raw = COALESCE(EXCLUDED.elevation_raw, airports.elevation_raw),
    faa_id = COALESCE(EXCLUDED.faa_id, airports.faa_id),
    facility_use = COALESCE(EXCLUDED.facility_use, airports.facility_use),
    fire_category = COALESCE(EXCLUDED.fire_category, airports.fire_category),
    fire_category_remarks = COALESCE(EXCLUDED.fire_category_remarks, airports.fire_category_remarks),
    handling_mandatory = COALESCE(EXCLUDED.handling_mandatory, airports.handling_mandatory),
    local_standard_time = COALESCE(EXCLUDED.local_standard_time, airports.local_standard_time),
    longest_runway_raw = COALESCE(EXCLUDED.longest_runway_raw, airports.longest_runway_raw),
    open_24h = COALESCE(EXCLUDED.open_24h, airports.open_24h),
    slots_required = COALESCE(EXCLUDED.slots_required, airports.slots_required),
    sunrise = COALESCE(EXCLUDED.sunrise, airports.sunrise),
    sunset = COALESCE(EXCLUDED.sunset, airports.sunset),
    tower_frequency = COALESCE(EXCLUDED.tower_frequency, airports.tower_frequency),
    us_customs_pre_clearance = COALESCE(EXCLUDED.us_customs_pre_clearance, airports.us_customs_pre_clearance),
    variation = COALESCE(EXCLUDED.variation, airports.variation),
    errors = COALESCE(EXCLUDED.errors, airports.errors),
    extra = COALESCE(EXCLUDED.extra, airports.extra)
RETURNING id;
"""

# Relationship upsert queries
AIRPORT_ORGANIZATION_LINK = """
INSERT INTO organization_airports (organization_id, airport_id)
VALUES (%s, %s)
ON CONFLICT DO NOTHING;
"""

AIRPORT_CLEARANCE_LINK = """
INSERT INTO airport_clearances (airport_id, clearance_id)
VALUES (%s, %s)
ON CONFLICT DO NOTHING;
"""

AIRPORT_NEARBY_LINK = """
INSERT INTO airport_nearby_airports (airport_id, nearby_airport_id)
VALUES (%s, %s)
ON CONFLICT DO NOTHING;
"""
