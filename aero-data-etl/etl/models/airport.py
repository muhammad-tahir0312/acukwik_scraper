"""
Airport upsert and lookup queries.
"""
AIRPORT_UPSERT = """
INSERT INTO airports (
    icao, iata, name, airport_type, city_id, country_id, latitude, longitude, elevation_ft,
    fuel_available, approaches, runway_surface, longest_runway_length_ft, longest_runway_width_ft, longest_runway_ident, utc_offset,
    source, source_external_id, pcn, scraped_at, url, scrape_status, scrape_duration_ms, external_id, observed_fields, missing_fields, errors, extra
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    source = COALESCE(EXCLUDED.source, airports.source),
    source_external_id = COALESCE(EXCLUDED.source_external_id, airports.source_external_id),
    pcn = COALESCE(EXCLUDED.pcn, airports.pcn),
    scraped_at = COALESCE(EXCLUDED.scraped_at, airports.scraped_at),
    url = COALESCE(EXCLUDED.url, airports.url),
    scrape_status = COALESCE(EXCLUDED.scrape_status, airports.scrape_status),
    scrape_duration_ms = COALESCE(EXCLUDED.scrape_duration_ms, airports.scrape_duration_ms),
    external_id = COALESCE(EXCLUDED.external_id, airports.external_id),
    observed_fields = COALESCE(EXCLUDED.observed_fields, airports.observed_fields),
    missing_fields = COALESCE(EXCLUDED.missing_fields, airports.missing_fields),
    errors = COALESCE(EXCLUDED.errors, airports.errors),
    extra = COALESCE(EXCLUDED.extra, airports.extra)
RETURNING id;
"""
