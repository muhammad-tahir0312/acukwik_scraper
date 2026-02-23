"""
Raw source models for storing verbatim scraped JSON.
"""
RAW_SCRAPED_INSERT = """
INSERT INTO scraped_records (
	source, entity_type, external_id, scraped_at, url, scrape_status, data,
	scrape_duration_ms, observed_fields, missing_fields, validation_errors, errors
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;
"""
